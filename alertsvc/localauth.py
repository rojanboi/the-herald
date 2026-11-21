"""Emergency (break-glass) accounts and the network rule for people-facing routes.

Single sign-on is the normal way in (`alertsvc/auth.py`). These accounts are for when the identity
provider itself is down during an incident: username, password and a one-time code (TOTP), checked
by Herald. Passwords are kept only as scrypt hashes; the account list in `config/local-accounts.yaml`
names the environment variables that hold each hash and TOTP secret, never the values.

    python -m alertsvc.localauth hash-password     # prompts, prints a hash for the environment
    python -m alertsvc.localauth new-totp <user>   # prints a new TOTP secret and otpauth:// URI
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import secrets
import struct
import sys
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import yaml

# scrypt cost: one of OWASP's listed settings (N=2^14, r=8, p=5, about 16 MB per check).
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 5
MIN_PASSWORD_LENGTH = 14
TOTP_STEP_SECONDS = 30
TOTP_DIGITS = 6
TOTP_WINDOW = 1  # accept the previous and next step too, for clock drift
MAX_FAILURES_PER_USER = 5
MAX_FAILURES_PER_IP = 20
LOCKOUT_SECONDS = 900


# ---- passwords ------------------------------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=256 * 1024 * 1024, dklen=32)


def hash_password(password: str) -> str:
    """`scrypt:N:r:p:salt:hash`. No `$` in it, so it can sit in an environment variable or compose file."""
    salt = secrets.token_bytes(16)
    digest = _scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return f"scrypt:{SCRYPT_N}:{SCRYPT_R}:{SCRYPT_P}:{_b64(salt)}:{_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = encoded.split(":")
        if scheme != "scrypt":
            return False
        expected = _unb64(digest)
        actual = _scrypt(password, _unb64(salt), int(n), int(r), int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# Checked when the username does not exist, so the answer takes as long either way (no account enumeration).
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def check_password(password: str, encoded: str | None) -> bool:
    if encoded is None:
        verify_password(password, _DUMMY_HASH)
        return False
    return verify_password(password, encoded)


# ---- one-time codes (RFC 6238) --------------------------------------------------------


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp(secret: str, step: int, digits: int = TOTP_DIGITS) -> str:
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    mac = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    number = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10**digits).zfill(digits)


def verify_totp(secret: str, code: str, last_step: int | None, now: float | None = None) -> int | None:
    """The step the code belongs to, or None. A step at or before `last_step` is refused (no reuse)."""
    code = (code or "").strip().replace(" ", "")
    if len(code) != TOTP_DIGITS or not code.isdigit():
        return None
    current = int((now if now is not None else time.time()) // TOTP_STEP_SECONDS)
    for step in range(current - TOTP_WINDOW, current + TOTP_WINDOW + 1):
        if hmac.compare_digest(totp(secret, step), code):
            return step if last_step is None or step > last_step else None
    return None


def otpauth_uri(secret: str, username: str, issuer: str = "Herald") -> str:
    return (f"otpauth://totp/{quote(issuer)}:{quote(username)}?secret={secret}&issuer={quote(issuer)}"
            f"&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_STEP_SECONDS}")


# ---- lockout --------------------------------------------------------------------------


def is_locked(user_failures: int, ip_failures: int) -> bool:
    """Failures in the last LOCKOUT_SECONDS, counted per username (existing or not) and per address."""
    return user_failures >= MAX_FAILURES_PER_USER or ip_failures >= MAX_FAILURES_PER_IP


# ---- accounts -------------------------------------------------------------------------


@dataclass(frozen=True)
class LocalAccount:
    username: str
    person: str
    role: str
    teams: list[str]
    password_hash: str = field(repr=False)
    totp_secret: str = field(repr=False)


def load_accounts(path: Path, env: Mapping[str, str]) -> dict[str, LocalAccount]:
    """Accounts whose secrets are present in the environment. One without them is simply not usable."""
    if not path.is_file():
        return {}
    accounts = {}
    for item in (yaml.safe_load(path.read_text()) or {}).get("accounts", []):
        password_hash = env.get(item.get("password_hash_env", ""), "")
        totp_secret = env.get(item.get("totp_secret_env", ""), "")
        if not password_hash or not totp_secret:
            continue
        username = str(item["username"]).strip().lower()
        accounts[username] = LocalAccount(
            username=username, person=str(item.get("person") or username), role=str(item["role"]),
            teams=[str(t) for t in item.get("teams", [])], password_hash=password_hash, totp_secret=totp_secret,
        )
    return accounts


# ---- network rule ---------------------------------------------------------------------


def parse_networks(value: str | None) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """`10.0.0.0/8, 192.168.1.10, ::1/128` -> networks. A malformed entry raises ValueError (fail at start-up)."""
    return [ipaddress.ip_network(part.strip(), strict=False) for part in (value or "").split(",") if part.strip()]


def ip_allowed(address: str | None, allowed: Iterable[ipaddress.IPv4Network | ipaddress.IPv6Network]) -> bool:
    try:
        ip = ipaddress.ip_address((address or "").strip())
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip in network for network in allowed)


def client_ip(peer: str | None, forwarded_for: str | None,
              trusted: Iterable[ipaddress.IPv4Network | ipaddress.IPv6Network]) -> str | None:
    """The caller's address. X-Forwarded-For counts only when the request came from a trusted proxy;
    then the right-most address that is not itself a trusted proxy is the client (anything left of it
    could have been written by the client)."""
    trusted = list(trusted)
    if not forwarded_for or not ip_allowed(peer, trusted):
        return peer
    for hop in reversed([h.strip() for h in forwarded_for.split(",") if h.strip()]):
        try:
            ipaddress.ip_address(hop)
        except ValueError:
            return peer  # garbled header: fall back to the proxy itself, which is then judged on its own
        if not ip_allowed(hop, trusted):
            return hop
    return peer


# ---- command line ---------------------------------------------------------------------


def _main(argv: list[str]) -> int:
    import getpass

    if argv[:1] == ["hash-password"]:
        password = getpass.getpass("New password: ")
        if len(password) < MIN_PASSWORD_LENGTH:
            print(f"Use at least {MIN_PASSWORD_LENGTH} characters.", file=sys.stderr)
            return 1
        if getpass.getpass("Again: ") != password:
            print("The two entries differ.", file=sys.stderr)
            return 1
        print(hash_password(password))
        return 0
    if argv[:1] == ["new-totp"] and len(argv) == 2:
        secret = new_totp_secret()
        print(f"secret: {secret}\nuri:    {otpauth_uri(secret, argv[1])}")
        return 0
    print(__doc__.split("\n\n")[-1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))

