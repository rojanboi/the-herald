"""Emergency accounts and the network rule: passwords, one-time codes, lockout, client address."""
from __future__ import annotations

import base64

import pytest

from alertsvc import auth, localauth

RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()  # RFC 6238 appendix B, SHA-1


def test_password_hash_round_trip_and_rejections():
    encoded = localauth.hash_password("correct horse battery staple")
    assert encoded.startswith("scrypt:16384:8:5:") and "$" not in encoded
    assert localauth.verify_password("correct horse battery staple", encoded)
    assert not localauth.verify_password("correct horse battery stapl", encoded)
    assert localauth.hash_password("same") != localauth.hash_password("same")  # salted
    scheme, n, r, p, salt, digest = encoded.split(":")
    tampered = ":".join([scheme, n, r, p, salt, digest[:-2] + ("AA" if digest[-2:] != "AA" else "BB")])
    assert not localauth.verify_password("correct horse battery staple", tampered)
    for garbage in ("", "plain", "bcrypt:1:2:3:x:y", "scrypt:x:8:5:a:b"):
        assert not localauth.verify_password("anything", garbage)


def test_unknown_user_still_costs_a_hash_and_fails():
    assert localauth.check_password("whatever", None) is False


@pytest.mark.parametrize("t,expected", [(59, "94287082"), (1111111109, "07081804"), (1234567890, "89005924")])
def test_totp_matches_rfc6238_vectors(t, expected):
    assert localauth.totp(RFC_SECRET, t // 30, digits=8) == expected


def test_totp_window_and_no_reuse():
    now = 1111111109
    step = now // 30
    code = localauth.totp(RFC_SECRET, step)
    assert localauth.verify_totp(RFC_SECRET, code, None, now=now) == step
    assert localauth.verify_totp(RFC_SECRET, code, step, now=now) is None  # same code again
    assert localauth.verify_totp(RFC_SECRET, localauth.totp(RFC_SECRET, step - 1), None, now=now) == step - 1
    assert localauth.verify_totp(RFC_SECRET, localauth.totp(RFC_SECRET, step + 1), None, now=now) == step + 1
    assert localauth.verify_totp(RFC_SECRET, localauth.totp(RFC_SECRET, step - 2), None, now=now) is None
    assert localauth.verify_totp(RFC_SECRET, f"{code[:3]} {code[3:]}", None, now=now) == step  # spaces typed in


@pytest.mark.parametrize("code", ["", "12345", "1234567", "abcdef", "12 34 5", None])
def test_totp_rejects_malformed_codes(code):
    assert localauth.verify_totp(RFC_SECRET, code, None, now=59) is None


def test_lockout_thresholds():
    assert not localauth.is_locked(4, 19)
    assert localauth.is_locked(5, 0)
    assert localauth.is_locked(0, 20)


def test_accounts_need_their_secrets_in_the_environment(tmp_path):
    path = tmp_path / "local-accounts.yaml"
    path.write_text(
        "accounts:\n"
        "  - {username: BreakGlass, person: anna, role: admin, teams: [], password_hash_env: H1, totp_secret_env: T1}\n"
        "  - {username: other, role: viewer, password_hash_env: H2, totp_secret_env: T2}\n"
    )
    accounts = localauth.load_accounts(path, {"H1": "scrypt:...", "T1": RFC_SECRET})
    assert list(accounts) == ["breakglass"] and accounts["breakglass"].person == "anna"
    assert RFC_SECRET not in repr(accounts["breakglass"])
    assert localauth.load_accounts(tmp_path / "missing.yaml", {}) == {}


NETS = localauth.parse_networks("10.0.0.0/8, 192.168.1.10, ::1/128, fd00::/8")


@pytest.mark.parametrize("address,allowed", [
    ("10.20.30.40", True), ("192.168.1.10", True), ("192.168.1.11", False), ("8.8.8.8", False),
    ("::1", True), ("fd12::1", True), ("2001:db8::1", False), ("::ffff:10.1.2.3", True),
    ("not-an-ip", False), ("", False), (None, False),
])
def test_ip_allowed(address, allowed):
    assert localauth.ip_allowed(address, NETS) is allowed


def test_malformed_network_fails_loudly():
    with pytest.raises(ValueError):
        localauth.parse_networks("10.0.0.0/8, office-wifi")


PROXIES = localauth.parse_networks("172.16.0.0/12")


def test_forwarded_for_is_ignored_unless_the_peer_is_a_trusted_proxy():
    # A laptop on the internet claims to be inside: ignored, judged on its real address.
    assert localauth.client_ip("203.0.113.9", "10.1.1.1", PROXIES) == "203.0.113.9"
    # Through our proxy: the right-most address that is not a proxy is the client.
    assert localauth.client_ip("172.18.0.5", "10.1.1.1", PROXIES) == "10.1.1.1"
    # The client prepends a fake hop; the proxy appends the real one. The real one wins.
    assert localauth.client_ip("172.18.0.5", "10.9.9.9, 203.0.113.9", PROXIES) == "203.0.113.9"
    assert localauth.client_ip("172.18.0.5", "203.0.113.9, 172.20.0.2", PROXIES) == "203.0.113.9"
    # Garbled header or none: the peer itself.
    assert localauth.client_ip("172.18.0.5", "10.1.1.1, <script>", PROXIES) == "172.18.0.5"
    assert localauth.client_ip("172.18.0.5", None, PROXIES) == "172.18.0.5"
    assert localauth.client_ip("172.18.0.5", "10.1.1.1", []) == "172.18.0.5"


def test_sso_mode_refuses_to_start_without_a_network_rule():
    good = {"HERALD_SECRET": "z" * 40, "OIDC_ISSUER": "http://idp/realms/x", "OIDC_CLIENT_ID": "c",
            "OIDC_CLIENT_SECRET": "s", "ALLOWED_NETWORKS": "10.0.0.0/8"}
    assert auth.config_problems(good) == []
    assert any("ALLOWED_NETWORKS" in p for p in auth.config_problems({**good, "ALLOWED_NETWORKS": ""}))
    assert any("ALLOWED_NETWORKS" in p for p in auth.config_problems({**good, "ALLOWED_NETWORKS": "office"}))


# ---- the network rule as the app applies it (no services needed: refused requests never reach a handler) ----


@pytest.fixture()
def guarded(monkeypatch):
    from fastapi.testclient import TestClient

    from alertsvc.api import main

    monkeypatch.setattr(main, "_ALLOWED_NETWORKS", localauth.parse_networks("10.99.0.0/16"))
    monkeypatch.setattr(main, "_TRUSTED_PROXIES", [])
    return TestClient(main.app, client=("203.0.113.9", 50000))


def test_wrong_network_is_refused_before_anything_else(guarded):
    for path in ("/", "/login", "/api/me", "/auth/providers", "/docs"):
        response = guarded.get(path, headers={"Accept": "application/json"})
        assert response.status_code == 403, path
        assert "corporate network" in response.json()["detail"]
    page = guarded.get("/login", headers={"Accept": "text/html"})
    assert page.status_code == 403 and "not available from this network" in page.text
    refused = guarded.post("/auth/password", json={"username": "breakglass", "password": "x", "code": "123456"},
                           headers={"Accept": "application/json"})
    assert refused.status_code == 403  # a right password would not help either


def test_forged_forwarded_for_does_not_get_in(guarded):
    response = guarded.get("/api/me", headers={"X-Forwarded-For": "10.99.0.5", "Accept": "application/json"})
    assert response.status_code == 403


def test_machine_routes_are_not_behind_the_people_rule(guarded):
    assert guarded.get("/healthz").status_code == 200
