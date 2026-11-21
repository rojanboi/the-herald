"""Sign-in end to end, against the running stack in SSO mode. Skipped unless HERALD_E2E=1.

    AUTH_MODE=oidc HERALD_SECRET=$(openssl rand -hex 32) docker compose --profile sso up -d --build
    HERALD_E2E=1 .venv/bin/python -m pytest -q tests/test_e2e_signin.py

Demo credentials come from the project's own files: Keycloak users from config/keycloak/herald-realm.json,
the emergency account from .env.example and the compose defaults. Tests run in file order: failures first,
then a success (which resets the failure count, so repeated runs do not lock the demo account).
"""
from __future__ import annotations

import html
import json
import os
import re
import secrets
import socket
import time
from pathlib import Path

import httpx
import pytest

from alertsvc import localauth

ROOT = Path(__file__).resolve().parent.parent
HERALD = os.environ.get("HERALD_URL", "http://localhost:8080")
ORIGIN = {"Origin": HERALD}

pytestmark = pytest.mark.skipif(os.environ.get("HERALD_E2E") != "1", reason="end-to-end: set HERALD_E2E=1")


def _demo_breakglass() -> tuple[str, str]:
    password = os.environ.get("HERALD_BREAKGLASS_PASSWORD") or re.search(
        r"#\s+password:\s*(\S+)", (ROOT / ".env.example").read_text()).group(1)
    secret = os.environ.get("HERALD_BREAKGLASS_TOTP_SECRET_PLAIN") or re.search(
        r"HERALD_BREAKGLASS_TOTP_SECRET:-([A-Z2-7]+)", (ROOT / "docker-compose.yml").read_text()).group(1)
    return password, secret


def _password_login(client: httpx.Client, username: str, password: str, code: str) -> httpx.Response:
    return client.post(f"{HERALD}/auth/password", headers=ORIGIN,
                       json={"username": username, "password": password, "code": code, "next": "/"})


@pytest.fixture(scope="module", autouse=True)
def stack_in_sso_mode():
    try:
        me = httpx.get(f"{HERALD}/api/me", timeout=3)
    except httpx.HTTPError:
        pytest.skip(f"Herald is not running at {HERALD}")
    if me.status_code != 401:
        pytest.skip("Herald is not in SSO mode (AUTH_MODE=oidc)")


def test_forged_identity_headers_get_nothing():
    response = httpx.get(f"{HERALD}/api/alerts", headers={"X-Herald-User": "person:anna", "X-Herald-Role": "admin"})
    assert response.status_code == 401


def test_sign_in_page_offers_sso_and_the_emergency_form():
    providers = httpx.get(f"{HERALD}/auth/providers").json()
    assert {"id": "sso", "label": "Continue with SSO", "type": "oidc"} in providers
    assert any(p["type"] == "password" for p in providers)


def test_cross_site_password_post_is_refused():
    response = httpx.post(f"{HERALD}/auth/password", headers={"Origin": "https://evil.example"},
                          json={"username": "breakglass", "password": "x", "code": "000000"})
    assert response.status_code == 403


def test_wrong_password_and_wrong_code_look_the_same():
    password, _ = _demo_breakglass()
    with httpx.Client() as client:
        wrong_password = _password_login(client, "breakglass", password + "x", "000000")
        wrong_code = _password_login(client, "breakglass", password, "000000")
        unknown_user = _password_login(client, "nobody-" + secrets.token_hex(4), password, "000000")
    assert wrong_password.status_code == wrong_code.status_code == unknown_user.status_code == 401
    assert wrong_password.json() == wrong_code.json() == unknown_user.json()
    assert "herald_session" not in wrong_code.cookies


def test_lockout_applies_to_unknown_usernames_too():
    username = "nobody-" + secrets.token_hex(4)
    with httpx.Client() as client:
        answers = [_password_login(client, username, "guess", "000000").status_code for _ in range(5)]
        locked = _password_login(client, username, "guess", "000000")
    assert answers == [401] * 5
    assert locked.status_code == 429 and locked.headers.get("retry-after")


def test_emergency_sign_in_then_the_code_cannot_be_reused():
    password, secret = _demo_breakglass()
    step = int(time.time() // localauth.TOTP_STEP_SECONDS)
    with httpx.Client() as client:
        for candidate in (step, step + 1):  # a run in the same 30 s may already have used the current code
            code = localauth.totp(secret, candidate)
            ok = _password_login(client, "breakglass", password, code)
            if ok.status_code == 200:
                break
        assert ok.status_code == 200, ok.text
        me = client.get(f"{HERALD}/api/me").json()
        assert (me["mode"], me["provider"], me["role"], me["user"]) == ("oidc", "local", "admin", "person:anna")
        with httpx.Client() as other:
            replay = _password_login(other, "breakglass", password, code)
        assert replay.status_code == 401
        audit = client.get(f"{HERALD}/api/audit", params={"limit": 20}).json()
        assert audit["intact"]
        assert any(e["action"] == "login" and e["detail"].get("break_glass") for e in audit["entries"])


def test_step_up_for_an_emergency_account_returns_to_the_form():
    response = httpx.get(f"{HERALD}/auth/login", params={"provider": "local", "step_up": "1", "next": "/#alert-9"})
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login?") and "step_up=1" in response.headers["location"]


def test_sso_sign_in_through_keycloak():
    realm = json.loads((ROOT / "config/keycloak/herald-realm.json").read_text())
    password = next(u for u in realm["users"] if u["username"] == "priya")["credentials"][0]["value"]
    with httpx.Client(follow_redirects=False, timeout=10) as client:
        start = client.get(f"{HERALD}/auth/login", params={"provider": "sso", "next": "/"})
        assert start.status_code == 303 and "code_challenge_method=S256" in start.headers["location"]
        page = client.get(start.headers["location"], follow_redirects=True)
        for cookie in client.cookies.jar:  # browsers send Secure cookies to http://localhost; Python's jar does not
            cookie.secure = False
        action = html.unescape(re.search(r'<form[^>]+id="kc-form-login"[^>]+action="([^"]+)"', page.text).group(1))
        back = client.post(action, data={"username": "priya", "password": password, "credentialId": ""})
        assert back.headers["location"].startswith(f"{HERALD}/auth/callback")
        assert client.get(back.headers["location"]).status_code == 303
        me = client.get(f"{HERALD}/api/me").json()
    assert (me["user"], me["role"], me["teams"], me["provider"]) == ("person:priya", "responder", ["settlements"], "sso")


def _lan_address() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # no packet is sent; this only picks the outgoing interface
            address = s.getsockname()[0]
    except OSError:
        return None
    return None if address.startswith("127.") else address


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def test_ports_are_published_on_this_machine_only():
    assert _port_open("127.0.0.1", 8080)
    assert not _port_open("127.0.0.1", 5432), "Postgres must not be reachable from the host"
    lan = _lan_address()
    if lan is None:
        pytest.skip("no network interface besides loopback")
    for port in (8080, 8180, 8090, 8025, 19092):
        assert not _port_open(lan, port), f"port {port} answers on {lan}: it should listen on 127.0.0.1 only"
