"""Single sign-on pieces that must hold whatever the provider: tokens, cookies, redirects, roles."""
from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from alertsvc import auth
from alertsvc.models import Person

ISSUER = "http://localhost:8180/realms/herald"
CLIENT = "herald"
SECRET = "x" * 40


def _key(kid: str):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key(), as_dict=True)
    return private, dict(public, kid=kid, use="sig", alg="RS256")


@pytest.fixture(scope="module")
def keys():
    private, public = _key("k1")
    other_private, _ = _key("k1")  # same kid, different key: a forged token
    return private, {"keys": [public]}, other_private


def _token(private, kid="k1", **overrides):
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": CLIENT, "sub": "u1", "iat": now, "exp": now + 300, "nonce": "n1",
              "preferred_username": "priya", "roles": ["responder", "team.settlements"]}
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, private, algorithm="RS256", headers={"kid": kid})


def _verify(token, jwks, nonce="n1"):
    return auth.verify_id_token(token, jwks=jwks, issuer=ISSUER, audience=CLIENT, nonce=nonce)


def test_valid_token_is_accepted(keys):
    private, jwks, _ = keys
    assert _verify(_token(private), jwks)["preferred_username"] == "priya"


@pytest.mark.parametrize("change", [
    {"aud": "another-app"},
    {"iss": "https://evil.example/realms/herald"},
    {"exp": int(time.time()) - 120},
    {"nonce": "replayed"},
    {"nonce": None},
    {"sub": None},
])
def test_wrong_claims_are_rejected(keys, change):
    private, jwks, _ = keys
    with pytest.raises(auth.AuthError):
        _verify(_token(private, **change), jwks)


def test_forged_signature_and_unknown_key_are_rejected(keys):
    private, jwks, other = keys
    with pytest.raises(auth.AuthError):
        _verify(_token(other), jwks)
    with pytest.raises(auth.AuthError):
        _verify(_token(private, kid="k2"), jwks)


def test_symmetric_and_unsigned_tokens_are_rejected(keys):
    _, jwks, _ = keys
    hs = jwt.encode({"iss": ISSUER, "aud": CLIENT, "sub": "u1", "nonce": "n1"}, "g" * 32, algorithm="HS256",
                    headers={"kid": "k1"})
    with pytest.raises(auth.AuthError):
        _verify(hs, jwks)
    unsigned = jwt.encode({"iss": ISSUER, "aud": CLIENT, "sub": "u1", "nonce": "n1"}, None, algorithm="none")
    with pytest.raises(auth.AuthError):
        _verify(unsigned, jwks)


def test_pkce_matches_rfc7636_example():
    assert auth.pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


@pytest.mark.parametrize("value,expected", [
    ("/#alert/12", "/#alert/12"),
    ("/rules?team=pricing", "/rules?team=pricing"),
    ("//evil.example", "/"),
    ("/\\evil.example", "/"),
    ("https://evil.example", "/"),
    ("javascript:alert(1)", "/"),
    ("/ok\r\nSet-Cookie: x", "/"),
    ("", "/"),
    (None, "/"),
])
def test_safe_next_stays_on_this_site(value, expected):
    assert auth.safe_next(value) == expected


def test_login_cookie_cannot_be_changed_reused_late_or_moved():
    sealed = auth.seal(SECRET, {"s": "state", "next": "/"}, now=1000)
    assert auth.unseal(SECRET, sealed, now=1001)["s"] == "state"
    payload, mac = sealed.rsplit(".", 1)
    assert auth.unseal(SECRET, payload[:-2] + "AA." + mac, now=1001) is None  # edited
    assert auth.unseal("y" * 40, sealed, now=1001) is None  # another secret
    assert auth.unseal(SECRET, sealed, now=1000 + auth.LOGIN_COOKIE_SECONDS + 1) is None  # too old
    assert auth.unseal(SECRET, "garbage", now=1001) is None
    assert auth.unseal(SECRET, None, now=1001) is None


def test_start_binds_state_nonce_and_verifier_to_the_cookie():
    provider = auth.Provider("sso", "Continue with SSO", ISSUER, CLIENT, "s", ISSUER + "/.well-known/openid-configuration")
    params, sealed = auth.start(provider, "//evil", step_up=True, secret=SECRET)
    data = auth.unseal(SECRET, sealed)
    assert data["s"] == params["state"] and data["n"] == params["nonce"]
    assert auth.pkce_challenge(data["v"]) == params["code_challenge"] and params["code_challenge_method"] == "S256"
    assert data["next"] == "/" and params["prompt"] == "login" and params["max_age"] == "0"
    assert "v" not in params and "code_verifier" not in params
    plain, sealed_plain = auth.start(provider, "/", step_up=False, secret=SECRET)
    assert "prompt" not in plain and auth.unseal(SECRET, sealed_plain)["up"] is False
    again, sealed_again = auth.start(provider, "/", step_up=False, secret=SECRET, reauth=True)
    assert again["prompt"] == "login" and auth.unseal(SECRET, sealed_again)["up"] is False  # not audited as step-up


PEOPLE = {
    "priya": Person(id="priya", name="Priya N.", email="priya@example.com"),
    "marcus": Person(id="marcus", name="Marcus L.", email="marcus@example.com"),
}
TEAMS = {"settlements", "pricing"}


def test_highest_role_wins_and_only_known_teams_count():
    who = auth.identity_from_claims(
        {"sub": "1", "iat": 100, "preferred_username": "marcus",
         "roles": ["responder", "approver", "team.settlements", "team.unknown", "superuser", "offline_access"]},
        "sso", TEAMS, PEOPLE,
    )
    assert (who.user, who.role, who.teams, who.name, who.auth_time) == (
        "person:marcus", "approver", ["settlements"], "Marcus L.", 100)


def test_no_herald_role_means_no_access():
    assert auth.identity_from_claims({"sub": "1", "iat": 1, "roles": ["team.settlements"]}, "sso", TEAMS, PEOPLE) is None
    assert auth.identity_from_claims({"sub": "1", "iat": 1}, "sso", TEAMS, PEOPLE) is None


def test_entra_style_username_is_matched_by_email_and_auth_time_preferred():
    who = auth.identity_from_claims(
        {"sub": "1", "iat": 100, "auth_time": 90, "preferred_username": "Priya@Example.com", "roles": "viewer"},
        "microsoft", TEAMS, PEOPLE,
    )
    assert (who.user, who.role, who.auth_time, who.provider) == ("person:priya", "viewer", 90, "microsoft")


def test_providers_need_complete_config_and_entra_is_single_tenant():
    env = {"OIDC_ISSUER": ISSUER + "/", "OIDC_CLIENT_ID": CLIENT, "OIDC_CLIENT_SECRET": "s"}
    assert list(auth.providers(env)) == ["sso"] and auth.providers(env)["sso"].issuer == ISSUER
    assert auth.providers({"OIDC_ISSUER": ISSUER}) == {}
    tenant = "72f988bf-86f1-41af-91ab-2d7cd011db47"
    entra = auth.providers({"ENTRA_TENANT_ID": tenant, "ENTRA_CLIENT_ID": "c", "ENTRA_CLIENT_SECRET": "QQ-secret-QQ"})
    assert entra["microsoft"].issuer == f"https://login.microsoftonline.com/{tenant}/v2.0"
    assert auth.providers({"ENTRA_TENANT_ID": "common", "ENTRA_CLIENT_ID": "c", "ENTRA_CLIENT_SECRET": "s"}) == {}
    assert "QQ-secret-QQ" not in repr(entra["microsoft"])  # the secret is kept out of logs


def test_sso_refuses_to_start_with_demo_secret_or_no_provider():
    good = {"HERALD_SECRET": "z" * 40, "OIDC_ISSUER": ISSUER, "OIDC_CLIENT_ID": CLIENT, "OIDC_CLIENT_SECRET": "s",
            "ALLOWED_NETWORKS": "10.0.0.0/8"}
    assert auth.config_problems(good) == []
    assert auth.config_problems({**good, "HERALD_SECRET": auth.DEFAULT_SECRET})
    assert auth.config_problems({"HERALD_SECRET": "z" * 40})
    assert auth.config_problems({**good, "ENTRA_TENANT_ID": "contoso.onmicrosoft.com"})
