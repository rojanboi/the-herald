"""Single sign-on with OpenID Connect: the authorization code flow with PKCE.

Works with Keycloak (the demo), Microsoft Entra ID, or any OIDC provider: they differ only in
configuration. The browser never holds a token. The API exchanges the code itself, checks the ID
token's signature against the provider's published keys, turns its `roles` claim into a Herald role
and teams, and keeps the session on the server (see `api/main.py`).

Access model, the same for every provider: the ID token carries one claim, `roles`, listing Herald
roles (`viewer` .. `admin`, the highest wins) and team memberships as `team.<team id>`. In Entra
these are App Roles; in Keycloak, client roles. Someone with no Herald role is refused.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

ROLES = ("viewer", "responder", "editor", "approver", "admin")  # lowest to highest, as in the API
DEFAULT_SECRET = "dev-only-change-me"
LOGIN_COOKIE_SECONDS = 600  # time allowed between leaving for the provider and coming back
CLOCK_LEEWAY_SECONDS = 30
DISCOVERY_TTL_SECONDS = 3600
_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


class AuthError(Exception):
    """Sign-in failed. The message is for the log; the person sees a generic page."""


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    issuer: str
    client_id: str
    client_secret: str = field(repr=False)
    discovery_url: str


def providers(env: Mapping[str, str]) -> dict[str, Provider]:
    """The providers with a complete configuration, in the order the sign-in page shows them."""
    found: dict[str, Provider] = {}
    tenant = env.get("ENTRA_TENANT_ID", "").strip()
    if tenant and env.get("ENTRA_CLIENT_ID") and env.get("ENTRA_CLIENT_SECRET") and _GUID.match(tenant):
        # One tenant only. The multi-tenant endpoints (common, organizations) would let any Microsoft account in.
        issuer = f"https://login.microsoftonline.com/{tenant}/v2.0"
        found["microsoft"] = Provider(
            "microsoft", "Continue with Microsoft", issuer, env["ENTRA_CLIENT_ID"], env["ENTRA_CLIENT_SECRET"],
            f"{issuer}/.well-known/openid-configuration",
        )
    issuer = env.get("OIDC_ISSUER", "").strip().rstrip("/")
    if issuer and env.get("OIDC_CLIENT_ID") and env.get("OIDC_CLIENT_SECRET"):
        found["sso"] = Provider(
            "sso", env.get("OIDC_LABEL", "Continue with SSO"), issuer, env["OIDC_CLIENT_ID"], env["OIDC_CLIENT_SECRET"],
            # Separate when the API reaches the provider by another name than browsers do (Keycloak in Docker).
            env.get("OIDC_DISCOVERY_URL") or f"{issuer}/.well-known/openid-configuration",
        )
    return found


def config_problems(env: Mapping[str, str]) -> list[str]:
    """Why single sign-on must not start. Empty when it may. The API refuses to start otherwise (fail closed)."""
    problems = []
    secret = env.get("HERALD_SECRET", "")
    if secret in ("", DEFAULT_SECRET) or len(secret) < 32:
        problems.append("HERALD_SECRET must be set to a random value of at least 32 characters")
    tenant = env.get("ENTRA_TENANT_ID", "").strip()
    if tenant and not _GUID.match(tenant):
        problems.append("ENTRA_TENANT_ID must be the tenant's ID (a GUID), not a domain or 'common'")
    if not providers(env):
        problems.append("no sign-in provider is configured (OIDC_ISSUER/OIDC_CLIENT_ID/OIDC_CLIENT_SECRET or ENTRA_*)")
    from alertsvc.localauth import parse_networks  # here to keep auth.py importable on its own

    try:
        if not parse_networks(env.get("ALLOWED_NETWORKS", "")):
            problems.append("ALLOWED_NETWORKS must list the networks people may sign in from (CIDR, comma separated)")
    except ValueError as error:
        problems.append(f"ALLOWED_NETWORKS is not a list of networks: {error}")
    return problems


# ---- small pieces ---------------------------------------------------------------------


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def pkce_challenge(verifier: str) -> str:
    """RFC 7636, method S256."""
    return _b64url(hashlib.sha256(verifier.encode()).digest())


def safe_next(value: str | None) -> str:
    """Where to land after signing in: a path on this site only, never another site (open redirect)."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    if any(ord(char) < 32 for char in value):
        return "/"
    return value


def seal(secret: str, data: dict[str, Any], now: float | None = None) -> str:
    """A short-lived value the browser carries but cannot change: JSON plus an HMAC over it."""
    body = dict(data, exp=int(now if now is not None else time.time()) + LOGIN_COOKIE_SECONDS)
    payload = _b64url(json.dumps(body, separators=(",", ":")).encode())
    mac = hmac.new(secret.encode(), f"herald-login.{payload}".encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{mac}"


def unseal(secret: str, value: str | None, now: float | None = None) -> dict[str, Any] | None:
    """The data, or None if the value was changed, signed with another secret, or is too old."""
    payload, _, mac = (value or "").rpartition(".")
    if not payload:
        return None
    expected = hmac.new(secret.encode(), f"herald-login.{payload}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, expected):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("exp", 0) < (now if now is not None else time.time()):
        return None
    return data


def new_session_token() -> tuple[str, str]:
    """(token for the cookie, hash for the database). The database never holds a usable token."""
    token = secrets.token_urlsafe(32)
    return token, token_hash(token)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---- the flow -------------------------------------------------------------------------

_discovery: dict[str, tuple[float, dict[str, Any]]] = {}
_jwks: dict[str, dict[str, Any]] = {}


async def discovery(provider: Provider) -> dict[str, Any]:
    cached = _discovery.get(provider.id)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    async with httpx.AsyncClient(timeout=5) as client:
        response = await client.get(provider.discovery_url)
    if response.status_code != 200:
        raise AuthError(f"{provider.id}: discovery answered {response.status_code}")
    meta = response.json()
    if str(meta.get("issuer", "")).rstrip("/") != provider.issuer:
        raise AuthError(f"{provider.id}: discovery names issuer {meta.get('issuer')!r}, expected {provider.issuer!r}")
    _discovery[provider.id] = (time.monotonic() + DISCOVERY_TTL_SECONDS, meta)
    return meta


def start(provider: Provider, next_path: str | None, step_up: bool, secret: str,
          reauth: bool = False) -> tuple[dict[str, str], str]:
    """Fresh state, nonce and PKCE verifier: (query parameters for the provider, sealed cookie value).

    The cookie ties the state to this browser, so a sign-in started by someone else cannot be
    completed here (login CSRF). `reauth` asks the provider for credentials again without it being
    a step-up, so another person can sign in after a refusal."""
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    params = {
        "response_type": "code",
        "client_id": provider.client_id,
        "scope": "openid profile email",
        "state": state,
        "nonce": nonce,
        "code_challenge": pkce_challenge(verifier),
        "code_challenge_method": "S256",
    }
    if step_up or reauth:  # approving a risky action, or switching person: the provider must ask again, now
        params.update(prompt="login", max_age="0")
    sealed = seal(secret, {"p": provider.id, "s": state, "n": nonce, "v": verifier,
                           "next": safe_next(next_path), "up": step_up})
    return params, sealed


async def authorization_url(provider: Provider, params: dict[str, str], redirect_uri: str) -> str:
    endpoint = (await discovery(provider))["authorization_endpoint"]
    return endpoint + ("&" if "?" in endpoint else "?") + urlencode({**params, "redirect_uri": redirect_uri})


async def finish(provider: Provider, code: str, verifier: str, nonce: str, redirect_uri: str) -> dict[str, Any]:
    """Exchange the code (with the PKCE verifier and our client secret) and return the verified ID token claims."""
    meta = await discovery(provider)
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(meta["token_endpoint"], data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
            "client_id": provider.client_id,
            "client_secret": provider.client_secret,
        })
    if response.status_code != 200:
        error = response.json().get("error", "") if "json" in response.headers.get("content-type", "") else ""
        raise AuthError(f"{provider.id}: token endpoint answered {response.status_code} {error}".strip())
    id_token = response.json().get("id_token")
    if not id_token:
        raise AuthError(f"{provider.id}: no ID token in the response")
    kid = jwt.get_unverified_header(id_token).get("kid")
    keys = await _signing_keys(provider, meta["jwks_uri"], kid)
    return verify_id_token(id_token, jwks=keys, issuer=provider.issuer, audience=provider.client_id, nonce=nonce)


async def _signing_keys(provider: Provider, jwks_uri: str, kid: str | None) -> dict[str, Any]:
    """The provider's keys, fetched again once when a token names a key we have not seen (rotation)."""
    keys = _jwks.get(provider.id)
    if keys is None or not any(k.get("kid") == kid for k in keys.get("keys", [])):
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(jwks_uri)
        if response.status_code != 200:
            raise AuthError(f"{provider.id}: key set answered {response.status_code}")
        keys = _jwks[provider.id] = response.json()
    return keys


def verify_id_token(token: str, *, jwks: dict[str, Any], issuer: str, audience: str, nonce: str) -> dict[str, Any]:
    """Signature (RS256 only, key chosen by `kid`), issuer, audience, expiry and nonce. Raises AuthError."""
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as error:
        raise AuthError(f"unreadable ID token: {error}") from None
    if header.get("alg") != "RS256":
        raise AuthError(f"ID token signed with {header.get('alg')!r}; only RS256 is accepted")
    key = None
    for candidate in jwks.get("keys", []):
        if candidate.get("kid") == header.get("kid") and candidate.get("use", "sig") == "sig":
            try:
                key = jwt.PyJWK(candidate, algorithm="RS256").key
            except jwt.PyJWTError:
                continue
            break
    if key is None:
        raise AuthError("ID token signed with a key the provider does not publish")
    try:
        claims = jwt.decode(
            token, key, algorithms=["RS256"], audience=audience, issuer=issuer, leeway=CLOCK_LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as error:
        raise AuthError(f"ID token rejected: {error}") from None
    if not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
        raise AuthError("ID token nonce does not match this sign-in")
    return claims


# ---- from claims to a Herald identity -------------------------------------------------


@dataclass(frozen=True)
class Identity:
    user: str  # "person:<id>"
    name: str
    role: str
    teams: list[str]
    auth_time: int  # when the person last entered credentials at the provider (epoch seconds)
    provider: str


def identity_from_claims(claims: dict[str, Any], provider_id: str, team_ids: set[str],
                         people: Mapping[str, Any]) -> Identity | None:
    """None when the token grants no Herald role: the person is signed in at the provider but has no access here."""
    granted = claims.get("roles") or []
    if isinstance(granted, str):
        granted = [granted]
    roles = [r for r in granted if r in ROLES]
    if not roles:
        return None
    teams = sorted({r.removeprefix("team.") for r in granted if r.startswith("team.")} & team_ids)
    username = str(claims.get("preferred_username") or claims.get("email") or claims["sub"]).strip().lower()
    person_id = username if username in people else next(
        (p.id for p in people.values() if getattr(p, "email", None) and p.email.lower() == username), None
    )
    if person_id is None:  # allowed by the provider but not in the directory yet: no contact details, still traceable
        person_id = re.sub(r"[^a-z0-9._@-]", "-", username)[:80]
    person = people.get(person_id)
    name = str(claims.get("name") or (person.name if person else person_id))
    return Identity(
        user=f"person:{person_id}",
        name=name,
        role=max(roles, key=ROLES.index),
        teams=teams,
        auth_time=int(claims.get("auth_time") or claims["iat"]),
        provider=provider_id,
    )
