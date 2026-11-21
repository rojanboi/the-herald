"""HTTP API: webhook ingest, the live feed (SSE), alerts, rules, runbook steps, approvals.

    uvicorn alertsvc.api.main:app

Stateless. Any number of replicas can run behind a load balancer; SSE clients on every
replica are woken through Postgres LISTEN/NOTIFY.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import asyncpg
import httpx
import orjson
from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaError
from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from alertsvc import auth, incident, localauth, security, store
from alertsvc.channels import CHANNELS, email_bodies, http, sms_text, voice_script
from alertsvc.configloader import load_config, unknown_channels
from alertsvc.models import Rule, Source
from alertsvc.normalize import MISSING, get_path, normalise, parse_log_line, unpack
from alertsvc.routing import alert_severity, plan
from alertsvc.rules import RuleError, explain, failing_leaves
from alertsvc.rules.operators import OPERATORS
from alertsvc.runbooks import ACTIONS, effective_approval, parse_runbook, render_runbook
from alertsvc.settings import settings
from alertsvc.templating import render
from alertsvc.worker.pipeline import find_runbook

log = logging.getLogger("herald.api")
ROLES = {"viewer": 0, "responder": 1, "editor": 2, "approver": 3, "admin": 4}


class App:
    pool: asyncpg.Pool
    producer: AIOKafkaProducer | None = None
    runtime = store.Runtime()
    subscribers: set[tuple[str | None, asyncio.Queue[dict[str, Any]]]] = set()


ctx = App()


# ---- start-up --------------------------------------------------------------------------


async def _seed_and_watch() -> None:
    """Load config/ into the database now, and again whenever a file changes (no restart)."""
    from watchfiles import awatch

    async def seed() -> None:
        config = load_config(settings.config_dir)
        for error in config.errors:
            log.error("config: %s", error)
        await store.seed_from_files(ctx.pool, config)
        ctx.runtime = await store.load_runtime(ctx.pool)
        log.info("config seeded: %s rules", len(config.rules))

    await seed()
    try:
        async for _ in awatch(settings.config_dir):
            await seed()
    except Exception as error:  # watching is a convenience; the API still serves without it
        log.warning("config watcher stopped: %s", error)


async def _listen() -> None:
    def on_feed(_con: Any, _pid: int, _channel: str, payload: str) -> None:
        item = json.loads(payload)
        for owner, queue in list(ctx.subscribers):
            if owner is None or owner == item["owner"]:
                if queue.qsize() < 1000:  # a slow browser must not hold memory; it resyncs on reconnect
                    queue.put_nowait(item)

    async def reload(*_: Any) -> None:
        ctx.runtime = await store.load_runtime(ctx.pool)

    while True:
        try:
            con = await asyncpg.connect(settings.database_url)
            await con.add_listener("feed", on_feed)
            await con.add_listener("config_changed", lambda *_: asyncio.ensure_future(reload()))
            while not con.is_closed():
                await asyncio.sleep(5)
        except (OSError, asyncpg.PostgresError) as error:
            log.warning("listener lost, reconnecting: %s", error)
        await asyncio.sleep(1)


async def _start_producer() -> None:
    while ctx.producer is None:
        producer = AIOKafkaProducer(bootstrap_servers=settings.kafka_bootstrap, acks="all", linger_ms=5)
        try:
            await producer.start()
            ctx.producer = producer
        except (KafkaError, OSError) as error:
            log.warning("broker not ready, retrying: %s", error)
            await producer.stop()
            await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if settings.auth_mode == "oidc" and (problems := auth.config_problems(os.environ)):
        # Fail closed: better no service than one with a known secret or no way to check who is calling.
        raise RuntimeError("single sign-on is misconfigured: " + "; ".join(problems))
    ctx.pool = await store.connect(settings.database_url)
    await store.ensure_auth_tables(ctx.pool)
    tasks = [asyncio.create_task(t()) for t in (_seed_and_watch, _listen, _start_producer)]
    yield
    for task in tasks:
        task.cancel()
    if ctx.producer:
        await ctx.producer.stop()
    await ctx.pool.close()


app = FastAPI(title="Herald", version="0.1.0", lifespan=lifespan)


# ---- who is calling ----------------------------------------------------------------------
# AUTH_MODE=oidc: single sign-on (alertsvc/auth.py) and a server-side session; identity headers are ignored.
# AUTH_MODE=demo: the UI's "Viewing as" switcher sends identity headers (mock; see README "What is mocked").

SESSION_COOKIE = "herald_session"
LOGIN_COOKIE = "herald_login"


class Actor(BaseModel):
    user: str
    role: str
    teams: list[str]
    name: str | None = None
    provider: str | None = None
    auth_time: float | None = None  # epoch seconds of the last credential check at the provider (oidc only)

    @property
    def owners(self) -> list[str] | None:
        """The owners this actor may see. None means all (admin)."""
        if self.role == "admin":
            return None
        return [self.user, *(f"team:{team}" for team in self.teams)]

    def may_see(self, owner: str) -> bool:
        return self.owners is None or owner in self.owners


class SignInRequired(Exception):
    pass


@app.exception_handler(SignInRequired)
async def _sign_in_required(_request: Request, _error: SignInRequired) -> JSONResponse:
    return JSONResponse({"detail": "sign in required", "login_url": "/login"}, status_code=401,
                        headers={"WWW-Authenticate": 'Bearer realm="herald"'})


def _public_origin() -> str:
    scheme, _, rest = settings.public_url.partition("://")
    return f"{scheme}://{rest.split('/', 1)[0]}"


def _secure_cookies() -> bool:
    return settings.public_url.startswith("https://")


# ---- network rule: people reach Herald only from allowed networks -----------------------
# Applies to the UI, the API and sign-in. Machine routes (webhook ingest, provider callbacks, the health check)
# have their own credentials and are left alone. A wrong network is refused before any password is looked at.

_ALLOWED_NETWORKS = localauth.parse_networks(settings.allowed_networks)  # malformed -> fails at start-up
_TRUSTED_PROXIES = localauth.parse_networks(settings.trusted_proxies)
_denied_audited: dict[str, float] = {}
_DENIED_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Not available · Herald</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f7f8f9;color:#172b4d;
font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif}
main{max-width:420px;margin:16px;padding:32px;background:#fff;border-radius:8px;
box-shadow:0 8px 12px rgba(9,30,66,.15),0 0 1px rgba(9,30,66,.31)}h1{font-size:20px;margin:0 0 8px}
p{margin:0 0 8px;color:#44546f}</style></head><body><main><h1>Herald is not available from this network</h1>
<p>Herald can only be opened from the corporate network or VPN, even with a valid account.</p>
<p>Connect to the VPN and try again. If you are on the corporate network, contact the service desk.</p>
</main></body></html>"""


def _caller_ip(request: Request) -> str | None:
    peer = request.client.host if request.client else None
    return localauth.client_ip(peer, request.headers.get("x-forwarded-for"), _TRUSTED_PROXIES)


@app.middleware("http")
async def network_guard(request: Request, call_next: Any) -> Any:
    path = request.url.path
    if not _ALLOWED_NETWORKS or path == "/healthz" or path.startswith(("/ingest/", "/inbound/")):
        return await call_next(request)
    ip = _caller_ip(request)
    if localauth.ip_allowed(ip, _ALLOWED_NETWORKS):
        return await call_next(request)
    log.warning("refused %s %s from %s: not an allowed network", request.method, path, ip)
    now = time.monotonic()
    if now - _denied_audited.get(ip or "?", float("-inf")) > 600:  # audit once per address per 10 min, not per hit
        if len(_denied_audited) > 10_000:
            _denied_audited.clear()
        _denied_audited[ip or "?"] = now
        try:
            async with ctx.pool.acquire() as con, con.transaction():
                await store.audit(con, f"network:{ip}", "network_denied", path[:200], {"method": request.method})
        except (OSError, asyncpg.PostgresError, AttributeError) as error:
            log.warning("could not audit a refused request: %s", error)
    if "text/html" in request.headers.get("accept", ""):
        return HTMLResponse(_DENIED_PAGE, status_code=403)
    return JSONResponse({"detail": "Herald is reachable only from the corporate network"}, status_code=403)


async def _session_actor(request: Request) -> Actor:
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and origin != _public_origin():  # second layer behind SameSite=Strict (CSRF)
            raise HTTPException(403, "cross-site request refused")
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise SignInRequired()
    row = await ctx.pool.fetchrow(
        "UPDATE sessions SET last_seen = now() WHERE id_hash = $1 AND revoked_at IS NULL AND expires_at > now() "
        "AND last_seen > now() - make_interval(secs => $2) "
        "RETURNING user_ref, name, role, teams, provider, extract(epoch FROM auth_time) AS auth_time",
        auth.token_hash(token), settings.session_idle_seconds,
    )
    if row is None:
        raise SignInRequired()
    return Actor(user=row["user_ref"], role=row["role"], teams=list(row["teams"]), name=row["name"],
                 provider=row["provider"], auth_time=float(row["auth_time"]))


async def actor(request: Request) -> Actor:
    """Who is calling. Under single sign-on only the session counts; headers a client sends are ignored."""
    if settings.auth_mode == "oidc":
        return await _session_actor(request)
    user = request.headers.get("x-herald-user")
    if not user:
        if settings.auth_mode != "demo":
            raise SignInRequired()
        return Actor(user="person:demo", role="admin", teams=[])
    role = request.headers.get("x-herald-role", "responder")
    if role not in ROLES:
        raise HTTPException(400, f"unknown role '{role}'")
    teams = [t.strip() for t in request.headers.get("x-herald-teams", "").split(",") if t.strip()]
    return Actor(user=user, role=role, teams=teams)


def need(who: Actor, role: str) -> None:
    if ROLES[who.role] < ROLES[role]:
        raise HTTPException(403, f"this needs the {role} role")


# ---- sign-in (AUTH_MODE=oidc) ------------------------------------------------------------


def _redirect_uri() -> str:
    return f"{settings.public_url.rstrip('/')}/auth/callback"


def _sso() -> dict[str, auth.Provider]:
    if settings.auth_mode != "oidc":
        raise HTTPException(404, "single sign-on is off (AUTH_MODE=demo)")
    return auth.providers(os.environ)


async def _open_session(con: asyncpg.Connection, request: Request, *, user: str, name: str, role: str,
                        teams: list[str], provider: str, auth_time: float, audit: dict[str, Any]) -> str:
    """One session row (hash of a random token) and an audit entry. Replaces this browser's previous session."""
    old = request.cookies.get(SESSION_COOKIE)
    if old:
        await con.execute("UPDATE sessions SET revoked_at = now() WHERE id_hash = $1 AND revoked_at IS NULL",
                          auth.token_hash(old))
    token, id_hash = auth.new_session_token()
    await con.execute(
        "INSERT INTO sessions (id_hash, user_ref, name, role, teams, provider, auth_time, expires_at) "
        "VALUES ($1, $2, $3, $4, $5, $6, to_timestamp($7), now() + make_interval(secs => $8))",
        id_hash, user, name, role, teams, provider, auth_time, settings.session_max_seconds,
    )
    await store.audit(con, user, "login", "session", {"provider": provider, "role": role, "teams": teams, **audit})
    return token


def _set_session_cookie(response: Any, token: str) -> None:
    response.set_cookie(SESSION_COOKIE, token, max_age=settings.session_max_seconds, path="/",
                        httponly=True, samesite="strict", secure=_secure_cookies())


@app.get("/login", include_in_schema=False)
async def login_page() -> FileResponse:
    page = Path(os.environ.get("UI_DIR", "ui")) / "login.html"
    if not page.is_file():
        raise HTTPException(404, "no sign-in page")
    return FileResponse(page, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"})


@app.get("/auth/providers")
async def auth_providers() -> list[dict[str, str]]:
    """The sign-in buttons to show: only providers with a complete configuration."""
    if settings.auth_mode != "oidc":
        return []
    buttons = [{"id": p.id, "label": p.label, "type": "oidc"} for p in auth.providers(os.environ).values()]
    if _local_accounts():
        buttons.append({"id": "local", "label": "Emergency account", "type": "password"})
    return buttons


@app.get("/auth/login", include_in_schema=False)
async def auth_login(provider: str | None = None, next_path: str = Query("/", alias="next"),
                     step_up: bool = False, reauth: bool = False) -> RedirectResponse:
    configured = _sso()
    if provider == "local":  # an emergency account confirms with its password and code again
        query = {"next": auth.safe_next(next_path), **({"step_up": "1"} if step_up else {})}
        return RedirectResponse("/login?" + urlencode(query), status_code=303)
    chosen = configured.get(provider) if provider else next(iter(configured.values()), None)
    if chosen is None:
        raise HTTPException(404, "unknown sign-in provider")
    params, sealed = auth.start(chosen, next_path, step_up, settings.secret, reauth=reauth)
    try:
        url = await auth.authorization_url(chosen, params, _redirect_uri())
    except (auth.AuthError, httpx.HTTPError, KeyError, ValueError) as error:
        log.warning("sign-in could not start: %s", error)
        return RedirectResponse("/login?error=login_failed", status_code=303)
    response = RedirectResponse(url, status_code=303)
    response.set_cookie(LOGIN_COOKIE, sealed, max_age=auth.LOGIN_COOKIE_SECONDS, path="/auth",
                        httponly=True, samesite="lax", secure=_secure_cookies())
    return response


@app.get("/auth/callback", include_in_schema=False)
async def auth_callback(request: Request, code: str = "", state: str = "", error: str = "") -> RedirectResponse:
    configured = _sso()
    flow = auth.unseal(settings.secret, request.cookies.get(LOGIN_COOKIE))

    def back_to_login(reason: str, kind: str = "login_failed") -> RedirectResponse:
        log.warning("sign-in failed: %s", reason)
        response = RedirectResponse(f"/login?error={kind}", status_code=303)
        response.delete_cookie(LOGIN_COOKIE, path="/auth")
        return response

    if error:  # e.g. access_denied: the provider refused (Entra: user not assigned to the app)
        return back_to_login(f"provider answered {error}", "no_access" if error == "access_denied" else "login_failed")
    if not flow or not code or not hmac.compare_digest(state, str(flow.get("s", ""))):
        return back_to_login("state missing, expired or not from this browser")
    provider = configured.get(flow["p"])
    if provider is None:
        return back_to_login(f"provider {flow['p']!r} is no longer configured")
    try:
        claims = await auth.finish(provider, code, flow["v"], flow["n"], _redirect_uri())
    except (auth.AuthError, httpx.HTTPError, KeyError, ValueError) as failure:
        return back_to_login(str(failure))

    directory = ctx.runtime.directory
    who = auth.identity_from_claims(claims, provider.id, set(directory.teams), directory.people)
    async with ctx.pool.acquire() as con, con.transaction():
        if who is None:
            username = str(claims.get("preferred_username") or claims.get("email") or claims.get("sub"))[:120]
            await store.audit(con, f"person:{username}", "login_denied", "session",
                              {"provider": provider.id, "reason": "no Herald role"})
            return back_to_login(f"{username} has no Herald role", "no_access")
        token = await _open_session(con, request, user=who.user, name=who.name, role=who.role, teams=who.teams,
                                    provider=who.provider, auth_time=who.auth_time,
                                    audit={"step_up": bool(flow.get("up"))})
    response = RedirectResponse(flow.get("next") or "/", status_code=303)
    _set_session_cookie(response, token)
    response.delete_cookie(LOGIN_COOKIE, path="/auth")
    return response


# ---- emergency accounts: username + password + one-time code (alertsvc/localauth.py) -----


class PasswordLogin(BaseModel):
    username: str = Field(max_length=120)
    password: str = Field(max_length=1024)
    code: str = Field(max_length=12)
    next: str = "/"


WRONG_CREDENTIALS = "Wrong username, password or code"


def _local_accounts() -> dict[str, localauth.LocalAccount]:
    if settings.auth_mode != "oidc" or not settings.local_login:
        return {}
    accounts = localauth.load_accounts(Path(settings.config_dir) / "local-accounts.yaml", os.environ)
    return {name: a for name, a in accounts.items() if a.role in ROLES}


@app.post("/auth/password")
async def auth_password(body: PasswordLogin, request: Request) -> JSONResponse:
    """Emergency sign-in when single sign-on is down. Every failure looks the same to the caller."""
    _sso()
    if not settings.local_login:
        raise HTTPException(404, "emergency accounts are switched off")
    if request.headers.get("origin") != _public_origin():  # also stops login CSRF
        raise HTTPException(403, "cross-site request refused")
    username, ip = body.username.strip().lower()[:120], (_caller_ip(request) or "?")
    # Failures in the window since the last success for this username / this address. A success resets the
    # count, so people behind one NAT address (or one proxy) are not locked out by someone else's typos forever.
    failures = await ctx.pool.fetchrow(
        "WITH last_ok AS (SELECT max(at) FILTER (WHERE username = $1) AS user_ok, max(at) FILTER (WHERE ip = $2) AS ip_ok "
        "  FROM login_attempts WHERE ok AND (username = $1 OR ip = $2)) "
        "SELECT count(*) FILTER (WHERE a.username = $1 AND a.at > coalesce(l.user_ok, '-infinity')) AS by_user, "
        "       count(*) FILTER (WHERE a.ip = $2 AND a.at > coalesce(l.ip_ok, '-infinity')) AS by_ip "
        "FROM login_attempts a CROSS JOIN last_ok l "
        "WHERE NOT a.ok AND a.at > now() - make_interval(secs => $3) AND (a.username = $1 OR a.ip = $2)",
        username, ip, localauth.LOCKOUT_SECONDS,
    )
    by_user, by_ip = failures["by_user"], failures["by_ip"]
    if localauth.is_locked(by_user, by_ip):
        raise HTTPException(429, "Too many attempts. Try again later.",
                            headers={"Retry-After": str(localauth.LOCKOUT_SECONDS)})

    account = _local_accounts().get(username)
    password_ok = await asyncio.to_thread(localauth.check_password, body.password,
                                          account.password_hash if account else None)
    step = None
    if password_ok and account:
        last = await ctx.pool.fetchval("SELECT last_totp_step FROM local_account_state WHERE username = $1", username)
        step = localauth.verify_totp(account.totp_secret, body.code, last)

    token = None
    async with ctx.pool.acquire() as con, con.transaction():
        if step is not None and account:
            # Claim the code's step atomically: two requests with the same code cannot both win.
            claimed = await con.fetchval(
                "INSERT INTO local_account_state (username, last_totp_step) VALUES ($1, $2) "
                "ON CONFLICT (username) DO UPDATE SET last_totp_step = EXCLUDED.last_totp_step "
                "WHERE local_account_state.last_totp_step < EXCLUDED.last_totp_step RETURNING last_totp_step",
                username, step,
            )
            if claimed is not None:
                await con.execute("INSERT INTO login_attempts (username, ip, ok) VALUES ($1, $2, true)", username, ip)
                directory = ctx.runtime.directory
                person = directory.people.get(account.person)
                token = await _open_session(
                    con, request, user=f"person:{account.person}", name=person.name if person else account.username,
                    role=account.role, teams=[t for t in account.teams if t in directory.teams], provider="local",
                    auth_time=time.time(), audit={"break_glass": True, "account": account.username, "ip": ip},
                )
        if token is None:
            await con.execute("INSERT INTO login_attempts (username, ip, ok) VALUES ($1, $2, false)", username, ip)
            if localauth.is_locked(by_user + 1, by_ip + 1) and not localauth.is_locked(by_user, by_ip):
                await store.audit(con, "system:herald", "login_locked", "session", {"username": username, "ip": ip})
    if token is None:
        log.warning("emergency sign-in failed for %r from %s", username, ip)
        raise HTTPException(401, WRONG_CREDENTIALS)
    log.warning("EMERGENCY ACCOUNT USED: %s from %s", username, ip)
    response = JSONResponse({"next": auth.safe_next(body.next)})
    _set_session_cookie(response, token)
    return response


@app.post("/auth/logout")
async def auth_logout(request: Request) -> JSONResponse:
    """Ends the session here at once, then sends the browser to the provider to end its session too."""
    configured = _sso()
    origin = request.headers.get("origin")
    if origin and origin != _public_origin():
        raise HTTPException(403, "cross-site request refused")
    logout_url = "/login"
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        async with ctx.pool.acquire() as con, con.transaction():
            row = await con.fetchrow(
                "UPDATE sessions SET revoked_at = now() WHERE id_hash = $1 AND revoked_at IS NULL "
                "RETURNING user_ref, provider", auth.token_hash(token),
            )
            if row:
                await store.audit(con, row["user_ref"], "logout", "session", {"provider": row["provider"]})
        provider = configured.get(row["provider"]) if row else None
        if provider:
            try:
                end = (await auth.discovery(provider)).get("end_session_endpoint")
            except (auth.AuthError, httpx.HTTPError, ValueError):
                end = None
            if end:
                logout_url = end + ("&" if "?" in end else "?") + urlencode({
                    "client_id": provider.client_id,
                    "post_logout_redirect_uri": f"{settings.public_url.rstrip('/')}/login",
                })
    response = JSONResponse({"logout_url": logout_url})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@app.get("/api/me")
async def me(who: Actor = Depends(actor)) -> dict[str, Any]:
    """Who the service thinks is calling, and how they signed in. The UI starts here."""
    person = ctx.runtime.directory.people.get(who.user.removeprefix("person:"))
    return {
        "mode": "oidc" if settings.auth_mode == "oidc" else "demo",
        "user": who.user,
        "name": who.name or (person.name if person else who.user.removeprefix("person:")),
        "role": who.role,
        "teams": who.teams,
        "provider": who.provider,
        "auth_time": datetime.fromtimestamp(who.auth_time, tz=timezone.utc).isoformat() if who.auth_time else None,
    }


# ---- ingest -----------------------------------------------------------------------------


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {"status": "ok", "broker": ctx.producer is not None, "rules": len(ctx.runtime.ruleset.rules)}


@app.post("/ingest/{source_id}", status_code=202)
async def ingest(source_id: str, request: Request) -> dict[str, Any]:
    """Accept any JSON (or a text line) and put it on the bus. Returns once the broker has it."""
    source = ctx.runtime.sources.get(source_id)
    if source is None or source.type != "webhook":
        raise HTTPException(404, f"no webhook source '{source_id}'; add it to config/sources.yaml")
    declared = int(request.headers.get("content-length") or 0)
    if declared > security.MAX_BODY_BYTES:
        raise HTTPException(413, "payload too large")
    body = await request.body()
    if len(body) > security.MAX_BODY_BYTES:
        raise HTTPException(413, "payload too large")

    if source.token_sha256:
        given = hashlib.sha256(request.headers.get("x-herald-token", "").encode()).hexdigest()
        if not hmac.compare_digest(given, source.token_sha256):
            raise HTTPException(401, "bad or missing token")
    secret = os.environ.get(source.token_env or "")
    if secret:
        token = request.headers.get("x-herald-token", "")
        signed = security.verify_signature(
            secret, body, request.headers.get("x-herald-timestamp"), request.headers.get("x-herald-signature")
        )
        if not (signed or hmac.compare_digest(token, secret)):
            raise HTTPException(401, "bad or missing token / signature")

    key = None
    try:
        parsed = orjson.loads(body)
        if security.depth(parsed) > security.MAX_DEPTH:
            raise HTTPException(400, "payload is nested too deeply")
        if source.partition_key and isinstance(parsed, dict):
            value = get_path(parsed, source.partition_key)
            key = str(value).encode() if value else None
    except orjson.JSONDecodeError:
        pass  # not JSON: the worker treats it as a text line

    if ctx.producer is None:
        raise HTTPException(503, "broker not ready, retry shortly", headers={"Retry-After": "2"})
    forwarded = {k: v for k, v in request.headers.items() if k.startswith("x-") and not k.startswith("x-herald-")}
    headers = [
        ("source", source_id.encode()),
        ("source_type", b"webhook"),
        ("ingest_ts", repr(time.time()).encode()),
    ]
    if forwarded:
        headers.append(("headers", json.dumps(forwarded).encode()))
    await ctx.producer.send_and_wait(settings.raw_topic, value=body, key=key, headers=headers)
    return {"accepted": True}


# ---- live feed ---------------------------------------------------------------------------


@app.get("/api/stream")
async def stream(request: Request, owner: str | None = None, who: Actor = Depends(actor)) -> StreamingResponse:
    """Server-sent events. `Last-Event-ID` replays what was missed while disconnected."""
    if owner and not who.may_see(owner):
        raise HTTPException(403, "not your team")
    if owner is None and who.owners is not None:
        raise HTTPException(400, "choose an owner, e.g. ?owner=team:settlements")
    last_id = request.headers.get("last-event-id")
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    subscription = (owner, queue)

    def frame(item: dict[str, Any]) -> str:
        return f"id: {item['id']}\nevent: {item['kind']}\ndata: {json.dumps(item)}\n\n"

    async def events():
        ctx.subscribers.add(subscription)
        try:
            yield ": connected\n\n"
            if last_id and last_id.isdigit():
                rows = await ctx.pool.fetch(
                    "SELECT id, owner, kind, alert_id FROM feed WHERE id > $1 AND ($2::text IS NULL OR owner = $2) "
                    "ORDER BY id LIMIT 1000",
                    int(last_id), owner,
                )
                for row in rows:
                    yield frame(dict(row))
            while True:
                try:
                    yield frame(await asyncio.wait_for(queue.get(), timeout=15))
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            ctx.subscribers.discard(subscription)

    return StreamingResponse(
        events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


# ---- alerts ------------------------------------------------------------------------------

_ALERT_COLUMNS = (
    "a.id, a.rule_id, a.owner, a.status, a.severity, a.title, a.body, a.fields, a.highlight, a.colour, a.count, "
    "a.first_seen, a.last_seen, a.acked_by, a.resolved_by, a.incident_manager, a.source, "
    "(SELECT coalesce(jsonb_agg(jsonb_build_object('recipient', n.recipient, 'channel', n.channel, "
    "'status', n.status, 'attempts', n.attempts, 'error', n.last_error) ORDER BY n.id), '[]'::jsonb) "
    "FROM notifications n WHERE n.alert_id = a.id) AS deliveries"
)


def _json(row: asyncpg.Record) -> dict[str, Any]:
    out = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in dict(row).items()}
    if "rule_id" in out and "id" in out:
        out["key"] = incident.alert_key(out["id"])
    return out


async def _alert(alert_id: int, who: Actor) -> asyncpg.Record:
    row = await ctx.pool.fetchrow("SELECT * FROM alerts WHERE id = $1", alert_id)
    if row is None or not who.may_see(row["owner"]):
        raise HTTPException(404, "no such alert")  # same answer whether it is missing or not yours
    return row


@app.get("/api/alerts")
async def list_alerts(
    owner: str | None = None, status: str | None = None, source: str | None = None,
    limit: int = Query(100, le=500), who: Actor = Depends(actor),
) -> list[dict[str, Any]]:
    if owner and not who.may_see(owner):
        raise HTTPException(403, "not your team")
    owners = [owner] if owner else who.owners
    rows = await ctx.pool.fetch(
        f"SELECT {_ALERT_COLUMNS} FROM alerts a WHERE ($1::text[] IS NULL OR a.owner = ANY($1)) "
        "AND ($2::text IS NULL OR a.status = $2) AND ($4::text IS NULL OR a.source = $4) "
        "ORDER BY a.last_seen DESC LIMIT $3",
        owners, status, limit, source,
    )
    return [_json(row) for row in rows]


@app.get("/api/alerts/{alert_id}")
async def get_alert(alert_id: int, who: Actor = Depends(actor)) -> dict[str, Any]:
    row = await _alert(alert_id, who)
    out = _json(row)
    rule = next((r for r in ctx.runtime.ruleset.rules if r.id == row["rule_id"]), None)
    if rule:
        out["why"] = explain(rule.when, normalise(row["sample_event"]))
    out["deliveries"] = [_json(r) for r in await ctx.pool.fetch(
        "SELECT recipient, channel, status, attempts, last_error, created_at, delivered_ts FROM notifications "
        "WHERE alert_id = $1 ORDER BY id", alert_id)]
    out["timeline"] = [_json(r) for r in await ctx.pool.fetch(
        "SELECT at, actor, action, detail FROM audit_log WHERE target = $1 ORDER BY id", f"alert:{alert_id}")]
    out["approvals"] = [_json(r) for r in await ctx.pool.fetch(
        "SELECT id, step_id, action, required, requested_by, approver, status, decided_by, decided_via, expires_at "
        "FROM approvals WHERE alert_id = $1 ORDER BY id", alert_id)]
    return out


async def _set_status(con: asyncpg.Connection, alert: asyncpg.Record, status: str, user: str, via: str) -> None:
    column = "acked" if status == "acked" else "resolved"
    await con.execute(
        f"UPDATE alerts SET status = $2, {column}_by = $3, {column}_at = now() WHERE id = $1", alert["id"], status, user
    )
    await store.audit(con, user, "ack" if status == "acked" else "resolve", f"alert:{alert['id']}", {"via": via})
    if status == "acked" and not alert["incident_manager"]:
        # One named person in charge: whoever acknowledges takes it, unless it was already handed to someone.
        await con.execute("UPDATE alerts SET incident_manager = $2 WHERE id = $1", alert["id"], user)
        await store.audit(con, user, "incident_manager", f"alert:{alert['id']}", {"via": via})
    await store.publish(con, alert["owner"], "alert", alert["id"])


@app.post("/api/alerts/{alert_id}/ack")
async def ack(alert_id: int, who: Actor = Depends(actor)) -> dict[str, Any]:
    need(who, "responder")
    alert = await _alert(alert_id, who)
    if alert["status"] != "firing":
        raise HTTPException(409, f"alert is already {alert['status']}")
    async with ctx.pool.acquire() as con, con.transaction():
        await _set_status(con, alert, "acked", who.user, "ui")
    return {"status": "acked"}


@app.post("/api/alerts/{alert_id}/resolve")
async def resolve(alert_id: int, who: Actor = Depends(actor)) -> dict[str, Any]:
    need(who, "responder")
    alert = await _alert(alert_id, who)
    if alert["status"] == "resolved":
        raise HTTPException(409, "alert is already resolved")
    async with ctx.pool.acquire() as con, con.transaction():
        await _set_status(con, alert, "resolved", who.user, "ui")
    return {"status": "resolved"}


# ---- handing an alert to someone ---------------------------------------------------------


class Assign(BaseModel):
    to: str | None = None  # person:<id>, or null to unassign


@app.post("/api/alerts/{alert_id}/assign")
async def assign(alert_id: int, body: Assign, who: Actor = Depends(actor)) -> dict[str, Any]:
    """Hand the alert to a person. They are told on their own channels for this severity, and
    the handover is on the alert's history."""
    need(who, "responder")
    alert = await _alert(alert_id, who)
    person = None
    if body.to:
        person = ctx.runtime.directory.people.get(body.to.removeprefix("person:"))
        if person is None or not body.to.startswith("person:"):
            raise HTTPException(422, "choose a person to hand this to")
    if (body.to or None) == alert["incident_manager"]:
        return {"assignee": alert["incident_manager"]}
    sender = ctx.runtime.directory.people.get(who.user.removeprefix("person:"))
    async with ctx.pool.acquire() as con, con.transaction():
        await con.execute("UPDATE alerts SET incident_manager = $2 WHERE id = $1", alert_id, body.to)
        await store.audit(con, who.user, "assigned", f"alert:{alert_id}", {"to": body.to})
        if person and body.to != who.user and alert["status"] != "resolved":
            key = incident.alert_key(alert_id)
            by = sender.name if sender else who.user
            text = f"Herald {key}: {by} handed this to you. {alert['title'][:70]}. Reply ACK {alert_id}."
            channels = ctx.runtime.directory.preferences(body.to).get(alert["severity"], ["ui"])
            for channel in dict.fromkeys(channels):
                await con.execute(
                    store.INSERT_NOTIFICATION, alert_id, body.to, channel, f"assign:{alert_id}:{body.to}:{channel}:{time.time():.0f}",
                    {"alert_id": alert_id, "recipient": body.to, "phone": person.phone, "email": person.email,
                     "title": f"Handed to you by {by}: {alert['title']}", "body": alert["body"], "severity": alert["severity"],
                     "count": alert["count"], "fields": alert["fields"], "runbook": alert["runbook"], "text": text,
                     "url": f"{settings.public_url}/#alert-{alert_id}"},
                    time.time(),
                )
            await con.execute("SELECT pg_notify('outbox', '')")
        await store.publish(con, alert["owner"], "alert", alert_id)
    return {"assignee": body.to}


# ---- incident practice: notes, status updates, postmortem drafts --------------------------


class Note(BaseModel):
    text: str


@app.post("/api/alerts/{alert_id}/notes")
async def add_note(alert_id: int, note: Note, who: Actor = Depends(actor)) -> dict[str, Any]:
    """An observation or decision, kept on the alert's timeline."""
    need(who, "responder")
    alert = await _alert(alert_id, who)
    async with ctx.pool.acquire() as con, con.transaction():
        await store.audit(con, who.user, "note", f"alert:{alert_id}", {"text": note.text[:2000]})
        await store.publish(con, alert["owner"], "alert", alert_id)
    return {"saved": True}


async def _timeline(alert_id: int) -> list[dict[str, Any]]:
    rows = await ctx.pool.fetch(
        "SELECT at, actor, action, detail FROM audit_log WHERE target = $1 ORDER BY id", f"alert:{alert_id}")
    return [dict(row) for row in rows]


@app.get("/api/alerts/{alert_id}/status-update")
async def status_update(alert_id: int, who: Actor = Depends(actor)) -> dict[str, Any]:
    """A status update in a fixed shape, filled from the alert, for a person to edit and send."""
    alert = dict(await _alert(alert_id, who))
    return {"text": incident.status_update(alert, datetime.now(timezone.utc))}


@app.get("/api/alerts/{alert_id}/postmortem")
async def postmortem(alert_id: int, names: bool = False, who: Actor = Depends(actor)) -> dict[str, Any]:
    """A blameless postmortem draft built from the audit trail. Roles, not names, unless asked."""
    alert = dict(await _alert(alert_id, who))
    team = ctx.runtime.directory.teams.get(alert["owner"].removeprefix("team:"))
    markdown = incident.postmortem_draft(alert, await _timeline(alert_id), team.lead if team else None, names)
    return {"markdown": markdown, "required": bool(incident.SEVERITY_GUIDE.get(alert["severity"], {}).get("postmortem"))}


@app.get("/api/meta")
async def meta() -> dict[str, Any]:
    """What the UI needs to build its pickers: severities and what they mean, operators, channels, actions."""
    return {
        "severities": incident.SEVERITY_GUIDE,
        "operators": OPERATORS.names(),
        "channels": CHANNELS.names(),
        "actions": [{"name": a.name, "risk": a.risk, "description": a.description}
                    for a in (ACTIONS.get(name) for name in ACTIONS.names())],
        "roles": list(ROLES),
    }


# ---- runbook steps and approvals ---------------------------------------------------------


async def _record_step(con: asyncpg.Connection, alert: asyncpg.Record, step_id: int, state: dict[str, Any]) -> None:
    await con.execute(
        "UPDATE alerts SET step_state = step_state || jsonb_build_object($2::text, $3::jsonb) WHERE id = $1",
        alert["id"], str(step_id), state,
    )
    await store.publish(con, alert["owner"], "alert", alert["id"])


async def _run_action(action: str, params: dict[str, Any]) -> dict[str, Any]:
    if action == "manual":
        return {"ok": True, "message": "Approved. A person carries this step out by hand."}
    try:
        return await ACTIONS.get(action).run(params)
    except Exception as error:  # the step is shown as failed with the reason; nothing else breaks
        return {"ok": False, "error": f"{type(error).__name__}: {error}"}


@app.post("/api/alerts/{alert_id}/steps/{step_id}/run")
async def run_step(alert_id: int, step_id: int, who: Actor = Depends(actor)) -> dict[str, Any]:
    """Tick a manual step, run a safe action, or ask for approval for a risky one."""
    need(who, "responder")
    alert = await _alert(alert_id, who)
    step = next((s for s in (alert["runbook"] or {}).get("steps", []) if s["id"] == step_id), None)
    if step is None:
        raise HTTPException(404, "no such step")
    action = step.get("action")
    if action is None and step.get("approval", "none") != "none":
        action = "manual"  # done by hand, but the runbook says a second person must agree first
    if action is None:
        async with ctx.pool.acquire() as con, con.transaction():
            await _record_step(con, alert, step_id, {"state": "done", "by": who.user})
            await store.audit(con, who.user, "step_done", f"alert:{alert_id}", {"step": step_id, "text": step["text"]})
        return {"state": "done"}
    if action != "manual" and action not in ACTIONS:
        raise HTTPException(400, f"unknown action '{action}'")

    required = effective_approval(action, step.get("approval", "none"))
    if required != "none":
        team = alert["owner"].removeprefix("team:") if alert["owner"].startswith("team:") else None
        lead = ctx.runtime.directory.teams[team].lead if team in ctx.runtime.directory.teams else None
        approver = f"person:{lead}" if lead else None
        code = security.new_code()
        async with ctx.pool.acquire() as con, con.transaction():
            approval_id = await con.fetchval(
                "INSERT INTO approvals (alert_id, step_id, action, params, required, requested_by, approver) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id",
                alert_id, step_id, action, step.get("params", {}), required, who.user, approver,
            )
            await con.execute(
                "UPDATE approvals SET code_hash = $2 WHERE id = $1",
                approval_id, security.hash_code(settings.secret, code, approval_id, approver or ""),
            )
            await _record_step(con, alert, step_id, {"state": "awaiting_approval", "approval": approval_id, "by": who.user})
            await store.audit(con, who.user, "approval_requested", f"alert:{alert_id}", {"step": step_id, "action": action})
            person = ctx.runtime.directory.people.get(lead or "")
            if person and person.phone:  # the code travels only to the approver's phone
                asker = ctx.runtime.directory.people.get(who.user.removeprefix("person:"))
                text = (f"Herald {incident.alert_key(alert_id)}: approve \"{str(step['text'])[:50]}\"? "
                        f"Reply YES {code} or NO {code}. Asked by {asker.name if asker else who.user}")
                await con.execute(
                    store.INSERT_NOTIFICATION, alert_id, approver, "sms", f"approval:{approval_id}",
                    {"alert_id": alert_id, "recipient": approver, "phone": person.phone, "text": text, "title": text},
                    time.time(),
                )
                await con.execute("SELECT pg_notify('outbox', '')")
        return {"state": "awaiting_approval", "approval": approval_id, "approver": approver}

    result = await _run_action(action, step.get("params", {}))
    async with ctx.pool.acquire() as con, con.transaction():
        state = "done" if result.get("ok") else "failed"
        await _record_step(con, alert, step_id, {"state": state, "by": who.user, "result": result})
        await store.audit(con, who.user, "action_run", f"alert:{alert_id}", {"step": step_id, "action": action, "result": result})
    return {"state": state, "result": result}


async def _decide(approval: asyncpg.Record, user: str, approve: bool, via: str) -> dict[str, Any]:
    """Shared by the UI button and the SMS reply. Four-eyes: the requester cannot approve their own request."""
    if user == approval["requested_by"]:
        raise HTTPException(403, "you cannot approve your own request")
    result: dict[str, Any] | None = None
    async with ctx.pool.acquire() as con, con.transaction():
        claimed = await con.fetchrow(
            "UPDATE approvals SET status = $2, decided_by = $3, decided_via = $4, decided_at = now() "
            "WHERE id = $1 AND status = 'pending' AND expires_at > now() RETURNING id",
            approval["id"], "approved" if approve else "rejected", user, via,
        )
        if claimed is None:  # used twice, or too late
            raise HTTPException(409, "this approval is no longer pending")
        alert = await con.fetchrow("SELECT * FROM alerts WHERE id = $1", approval["alert_id"])
        await store.audit(
            con, user, "approved" if approve else "rejected", f"alert:{alert['id']}",
            {"step": approval["step_id"], "action": approval["action"], "via": via},
        )
        if approve:
            result = await _run_action(approval["action"], approval["params"])
            state = {"state": "done" if result.get("ok") else "failed", "by": approval["requested_by"],
                     "approved_by": user, "result": result}
            await store.audit(con, user, "action_run", f"alert:{alert['id']}",
                              {"step": approval["step_id"], "action": approval["action"], "result": result})
        else:
            state = {"state": "rejected", "by": approval["requested_by"], "rejected_by": user}
        await _record_step(con, alert, approval["step_id"], state)
    return {"status": "approved" if approve else "rejected", "result": result}


class Decision(BaseModel):
    approve: bool


@app.post("/api/approvals/{approval_id}/decide")
async def decide(approval_id: int, decision: Decision, who: Actor = Depends(actor)) -> dict[str, Any]:
    need(who, "approver")
    approval = await ctx.pool.fetchrow("SELECT * FROM approvals WHERE id = $1", approval_id)
    if approval is None:
        raise HTTPException(404, "no such approval")
    await _alert(approval["alert_id"], who)
    if approval["required"].startswith("role:") and approval["approver"] and who.user != approval["approver"] and who.role != "admin":
        raise HTTPException(403, f"this needs {approval['approver']}")
    if decision.approve and settings.auth_mode == "oidc" and (
        who.auth_time is None or time.time() - who.auth_time > settings.step_up_max_age
    ):
        # Step-up (RFC 9470): saying yes to a risky action needs a recent credential check, not just a session.
        raise HTTPException(401, "step_up_required", headers={"WWW-Authenticate": (
            'Bearer error="insufficient_user_authentication", '
            f'error_description="Approving needs a sign-in in the last {settings.step_up_max_age} seconds", '
            f'max_age={settings.step_up_max_age}'
        )})
    return await _decide(approval, who.user, decision.approve, "ui")


@app.post("/inbound/sms")
async def inbound_sms(From: str = Form(...), Body: str = Form(...)) -> dict[str, Any]:
    """Replies to an alert: `ACK 17`, `RESOLVE 17`, `YES 4821`, `NO 4821`.

    The sender is identified by phone number. In production, verify the provider's request
    signature here before trusting `From` (see docs/SECURITY.md).
    """
    person = next((p for p in ctx.runtime.directory.people.values() if p.phone == From), None)
    parsed = security.parse_reply(Body)
    if person is None or parsed is None:
        return {"handled": False, "reason": "unknown sender or unreadable reply"}
    user, (verb, code) = f"person:{person.id}", parsed

    if verb in ("ack", "resolve"):
        alert = await ctx.pool.fetchrow(
            "SELECT a.* FROM alerts a WHERE a.id = $1 AND EXISTS "
            "(SELECT 1 FROM notifications n WHERE n.alert_id = a.id AND n.recipient = $2)",
            int(code), user,
        )
        if alert is None or alert["status"] == "resolved" or (verb == "ack" and alert["status"] != "firing"):
            return {"handled": False, "reason": "nothing to do for that alert"}
        async with ctx.pool.acquire() as con, con.transaction():
            await _set_status(con, alert, "acked" if verb == "ack" else "resolved", user, "sms")
        return {"handled": True, "alert": alert["id"], "status": verb}

    pending = await ctx.pool.fetch(
        "SELECT * FROM approvals WHERE status = 'pending' AND approver = $1 AND expires_at > now()", user
    )
    for approval in pending:
        expected = security.hash_code(settings.secret, code, approval["id"], user)
        if hmac.compare_digest(expected, approval["code_hash"] or ""):
            try:
                return {"handled": True, **await _decide(approval, user, verb == "approve", "sms")}
            except HTTPException as error:
                return {"handled": False, "reason": error.detail}
    return {"handled": False, "reason": "no pending approval matches that code"}


# ---- rules ------------------------------------------------------------------------------


@app.get("/api/rules")
async def list_rules(who: Actor = Depends(actor)) -> list[dict[str, Any]]:
    rows = await ctx.pool.fetch(
        "SELECT r.doc, r.version, r.source, r.updated_at, r.updated_by, "
        "(SELECT count(*) FROM alerts a WHERE a.rule_id = r.id) AS alerts, "
        "(SELECT max(last_seen) FROM alerts a WHERE a.rule_id = r.id) AS last_fired "
        "FROM rules r WHERE ($1::text[] IS NULL OR r.owner = ANY($1)) ORDER BY r.owner, r.id",
        who.owners,
    )
    return [_json(row) for row in rows]


@app.put("/api/rules/{rule_id}")
async def save_rule(rule_id: str, doc: dict[str, Any], who: Actor = Depends(actor)) -> dict[str, Any]:
    """Create or change a rule at runtime. It is validated first; workers pick it up within a second."""
    need(who, "editor")
    try:
        rule = Rule(**{**doc, "id": rule_id, "source": "ui"})
    except ValidationError as error:
        raise HTTPException(422, json.loads(error.json(include_url=False, include_context=False))) from None
    if not who.may_see(rule.owner):
        raise HTTPException(403, "you can only save rules for your own teams")
    unknown = unknown_channels(rule)
    if unknown:
        raise HTTPException(422, f"unknown channel(s): {', '.join(unknown)}")
    async with ctx.pool.acquire() as con, con.transaction():
        existing = await con.fetchrow("SELECT owner FROM rules WHERE id = $1", rule_id)
        if existing and not who.may_see(existing["owner"]):
            raise HTTPException(403, "that rule belongs to another team")
        version = await con.fetchval(
            "INSERT INTO rules (id, owner, name, enabled, source, doc, updated_by) VALUES ($1, $2, $3, $4, 'ui', $5, $6) "
            "ON CONFLICT (id) DO UPDATE SET owner = EXCLUDED.owner, name = EXCLUDED.name, enabled = EXCLUDED.enabled, "
            "source = 'ui', doc = EXCLUDED.doc, version = rules.version + 1, updated_at = now(), updated_by = EXCLUDED.updated_by "
            "RETURNING version",
            rule.id, rule.owner, rule.name, rule.enabled, rule.model_dump(by_alias=True), who.user,
        )
        await store.audit(con, who.user, "rule_saved", f"rule:{rule_id}", {"version": version})
        await con.execute("SELECT pg_notify('config_changed', '')")
    return {"id": rule_id, "version": version}


class RuleTest(BaseModel):
    when: dict[str, Any]
    events: list[Any] | None = None
    source: str | None = None


@app.post("/api/rules/test")
async def test_rule(body: RuleTest, who: Actor = Depends(actor)) -> dict[str, Any]:
    """Dry-run a condition against given events, or the most recent ones received.

    Drives "matches N of your last M events" and names the condition that failed for each miss.
    """
    if body.events is None:
        rows = await ctx.pool.fetch(
            "SELECT id, source, received_at, raw FROM recent_events WHERE ($1::text IS NULL OR source = $1) "
            "ORDER BY id DESC LIMIT 50", body.source,
        )
        items = [(row["id"], row["source"], row["received_at"].isoformat(), row["raw"])
                 for row in rows if _may_see_source(who, row["source"])]
    else:
        items = [(None, body.source, None, item) for event in body.events for item in unpack(event)]
    results = []
    try:
        for event_id, source, received_at, raw in items:
            event = _event(raw, source)
            explained = explain(body.when, event)
            results.append({
                "id": event_id, "source": source, "received_at": received_at, "passed": explained["passed"],
                "failed_because": failing_leaves(explained), "event": raw,
                "attrs": {k: event.attrs.get(k) for k in ("type", "severity", "timestamp", "service")},
            })
    except RuleError as error:
        raise HTTPException(422, str(error)) from None
    return {"matched": sum(r["passed"] for r in results), "total": len(results), "results": results}


@app.get("/api/fields")
async def fields(source: str | None = None) -> list[dict[str, Any]]:
    """Every field path seen so far, with a type and an example. Feeds the rule editor's field picker."""
    rows = await ctx.pool.fetch(
        "SELECT source, path, type, example, seen FROM seen_fields WHERE ($1::text IS NULL OR source = $1) "
        "ORDER BY source, path", source,
    )
    return [dict(row) for row in rows]


class RunbookText(BaseModel):
    text: str
    format: str | None = None


@app.post("/api/runbooks/parse")
async def parse(body: RunbookText) -> dict[str, Any]:
    """Any runbook text -> steps, using the deterministic parsers. Never fails on odd input."""
    return parse_runbook(body.text, "draft", body.format).model_dump(by_alias=True)


# ---- measurement and audit ---------------------------------------------------------------


@app.get("/api/stats")
async def stats(since: float = 0.0, run: str | None = None) -> dict[str, Any]:
    """Counts and ingest-to-delivery latency, for events since `since` (epoch seconds) or for
    one load-test run (events carrying `"run": "<id>"`).

    The load test reads its numbers from here, so they are measured, not estimated.
    """
    row = await ctx.pool.fetchrow(
        """
        SELECT count(*) FILTER (WHERE n.status = 'sent') AS delivered,
               count(*) FILTER (WHERE n.status <> 'sent') AS outstanding,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM n.delivered_ts - n.ingest_ts) * 1000)
                   FILTER (WHERE n.status = 'sent') AS p50_ms,
               percentile_cont(0.99) WITHIN GROUP (ORDER BY extract(epoch FROM n.delivered_ts - n.ingest_ts) * 1000)
                   FILTER (WHERE n.status = 'sent') AS p99_ms,
               max(extract(epoch FROM n.delivered_ts - n.ingest_ts) * 1000) FILTER (WHERE n.status = 'sent') AS max_ms,
               extract(epoch FROM max(n.delivered_ts)) AS last_delivered
        FROM notifications n JOIN alerts a ON a.id = n.alert_id
        WHERE n.ingest_ts >= to_timestamp($1) AND ($2::text IS NULL OR a.sample_event->>'run' = $2)
        """,
        since, run,
    )
    events = await ctx.pool.fetchval(
        "SELECT coalesce(sum(count), 0) FROM alerts WHERE first_seen >= to_timestamp($1) "
        "AND ($2::text IS NULL OR sample_event->>'run' = $2)", since, run,
    )
    return {**{k: (float(v) if v is not None else None) for k, v in dict(row).items()}, "matched_events": int(events)}


@app.get("/api/audit")
async def audit_log(limit: int = Query(200, le=1000), who: Actor = Depends(actor)) -> dict[str, Any]:
    need(who, "approver")
    rows = [dict(r) for r in await ctx.pool.fetch(
        "SELECT id, at, actor, action, target, detail, prev_hash, hash FROM audit_log ORDER BY id")]
    for row in rows:
        row["at"] = row["at"].isoformat()
    intact = security.verify_chain(rows) is None
    if who.owners is not None:
        ids = [int(r["target"].split(":")[1]) for r in rows if r["target"].startswith("alert:")]
        owner = {f"alert:{r['id']}": r["owner"] for r in await ctx.pool.fetch(
            "SELECT id, owner FROM alerts WHERE id = ANY($1::bigint[])", ids)}
        owner.update({f"rule:{r['id']}": r["owner"] for r in await ctx.pool.fetch("SELECT id, owner FROM rules")})
        rows = [r for r in rows if r["target"].startswith(("runbook:", "platform:")) or who.may_see(owner.get(r["target"], ""))]
    return {"intact": intact, "entries": rows[-limit:]}


# ---- what the web UI needs to draw itself ------------------------------------------------


def _may_see_source(who: Actor, source_id: str) -> bool:
    source = ctx.runtime.sources.get(source_id)
    return who.owners is None or source is None or not source.visible_to or bool(set(source.visible_to) & set(who.teams))


def _event(raw: Any, source: str | None):
    src = ctx.runtime.sources.get(source or "")
    return normalise(raw, source=source or "unknown", source_type=src.type if src else "webhook",
                     mapping=dict(src.map) if src else None)


@app.get("/api/directory")
async def directory() -> dict[str, Any]:
    """Teams, people and who is on call now. Names only: no phone numbers or addresses."""
    d, now = ctx.runtime.directory, datetime.now(timezone.utc)
    owners = {row["owner"] for row in await ctx.pool.fetch("SELECT DISTINCT owner FROM rules")}
    return {
        "teams": [
            {"id": t.id, "name": t.name, "members": t.members, "lead": t.lead, "on_call": d.on_call(t.id, now),
             "rota": t.on_call.rota if t.on_call else [], "shift_days": t.on_call.shift_days if t.on_call else None,
             "preferences": d.preferences(f"team:{t.id}"), "has_rules": f"team:{t.id}" in owners}
            for t in d.teams.values()
        ],
        "people": [
            {"id": p.id, "name": p.name, "tz": p.tz, "preferences": d.preferences(f"person:{p.id}"),
             "has_rules": f"person:{p.id}" in owners}
            for p in d.people.values()
        ],
    }


def _platform(s: Any) -> dict[str, Any]:
    return {"id": s.id, "name": s.name or s.id, "icon": s.icon or s.type, "type": s.type, "topic": s.topic, "path": s.path,
            "managed": s.managed, "needs_token": bool(s.token_sha256 or os.environ.get(s.token_env or ""))}


@app.get("/api/sources")
async def sources() -> list[dict[str, Any]]:
    return [_platform(s) for s in ctx.runtime.sources.values()]


@app.get("/api/platforms")
async def platforms(who: Actor = Depends(actor)) -> list[dict[str, Any]]:
    """Every connected platform, with how many problems are open on it for the caller's teams
    and when it was last heard from."""
    counts = {row["source"]: row for row in await ctx.pool.fetch(
        "SELECT source, count(*) FILTER (WHERE status = 'firing') AS firing, count(*) FILTER (WHERE status = 'acked') AS acked, "
        "count(*) FILTER (WHERE status = 'resolved') AS resolved FROM alerts "
        "WHERE ($1::text[] IS NULL OR owner = ANY($1)) GROUP BY source", who.owners)}
    seen = {row["source"]: row["at"] for row in await ctx.pool.fetch(
        "SELECT source, max(received_at) AS at FROM recent_events GROUP BY source")}
    out = []
    for s in ctx.runtime.sources.values():
        if not _may_see_source(who, s.id):
            continue
        c = counts.get(s.id)
        out.append({**_platform(s), "firing": c["firing"] if c else 0, "acked": c["acked"] if c else 0,
                    "resolved": c["resolved"] if c else 0,
                    "last_event_at": seen[s.id].isoformat() if s.id in seen else None})
    return out


class PlatformIn(BaseModel):
    name: str
    icon: str | None = None


@app.put("/api/platforms/{platform_id}")
async def connect_platform(platform_id: str, body: PlatformIn, who: Actor = Depends(actor)) -> dict[str, Any]:
    """Connect a new platform over a webhook. Returns its token once; only a hash is kept."""
    need(who, "editor")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,40}", platform_id):
        raise HTTPException(422, "use lower-case letters, digits and dashes for the platform id")
    if platform_id in ctx.runtime.sources:
        raise HTTPException(409, "a platform with this id is already connected")
    token = secrets.token_urlsafe(24)
    source = Source(id=platform_id, name=body.name.strip()[:60] or platform_id, icon=body.icon or "webhook", type="webhook",
                    token_sha256=hashlib.sha256(token.encode()).hexdigest(), managed="ui")
    async with ctx.pool.acquire() as con, con.transaction():
        await con.execute("INSERT INTO config_docs (kind, id, doc) VALUES ('source', $1, $2)", platform_id,
                          source.model_dump(by_alias=True))
        await store.audit(con, who.user, "platform_connected", f"platform:{platform_id}", {"name": source.name})
        await con.execute("SELECT pg_notify('config_changed', '')")
    ctx.runtime = await store.load_runtime(ctx.pool)
    return {**_platform(source), "token": token, "url": f"{settings.public_url}/ingest/{platform_id}"}


@app.get("/api/events/recent")
async def recent_events(source: str | None = None, limit: int = Query(50, le=200), who: Actor = Depends(actor)) -> list[dict[str, Any]]:
    """A sample of what arrived lately (one event per source per second), with the rules each one fired."""
    rows = await ctx.pool.fetch(
        "SELECT id, source, received_at, raw FROM recent_events WHERE ($1::text IS NULL OR source = $1) ORDER BY id DESC LIMIT $2",
        source, limit,
    )
    out = []
    for row in rows:
        if not _may_see_source(who, row["source"]):
            continue
        event = _event(row["raw"], row["source"])
        closing = ctx.runtime.ruleset.resolves(event)
        fired = ctx.runtime.ruleset.match(event, skip=closing)
        out.append({
            "id": row["id"], "source": row["source"], "received_at": row["received_at"].isoformat(), "raw": row["raw"],
            "attrs": {k: event.attrs.get(k) for k in ("type", "severity", "timestamp", "service")},
            "fired": [r.id for r in fired if who.may_see(r.owner)],
            "closed": [r.id for r in closing if who.may_see(r.owner)],
        })
    return out


class EventIn(BaseModel):
    event: Any
    source: str | None = None


@app.post("/api/events/explain")
async def explain_event(body: EventIn, who: Actor = Depends(actor)) -> dict[str, Any]:
    """Every rule the caller can see, against one event: matched or not, the exact condition
    that failed, and whether the event would close an open alert instead of raising one.

    Accepts what a source can send: an object, a batch, or a text line. `items` has one entry
    per event in a batch; `attrs` and `rules` repeat the first for convenience.
    """
    source = ctx.runtime.sources.get(body.source or "")
    raw = body.event
    if isinstance(raw, str):
        try:
            raw = orjson.loads(raw)
        except orjson.JSONDecodeError:
            raw = parse_log_line(raw, source.pattern if source else None)
    rows = await ctx.pool.fetch(
        "SELECT doc FROM rules WHERE ($1::text[] IS NULL OR owner = ANY($1)) ORDER BY owner, id", who.owners)
    docs = [Rule(**row["doc"]) for row in rows]
    items = []
    for item in unpack(raw):
        event = _event(item, body.source)
        rules = []
        for rule in docs:
            explained = explain(rule.when, event)
            closes = bool(rule.resolve_when) and explain(rule.resolve_when, event)["passed"]
            rules.append({"id": rule.id, "name": rule.name, "owner": rule.owner, "enabled": rule.enabled,
                          "passed": explained["passed"], "closes": closes, "why": explained,
                          "failed_because": failing_leaves(explained)})
        items.append({"attrs": {k: event.attrs.get(k) for k in ("type", "severity", "timestamp", "service")},
                      "rules": rules, "event": item})
    return {"attrs": items[0]["attrs"], "rules": items[0]["rules"], "items": items}


class PreviewIn(BaseModel):
    rule: dict[str, Any]
    event: Any
    source: str | None = None


@app.post("/api/rules/preview")
async def preview_rule(body: PreviewIn) -> dict[str, Any]:
    """How a draft rule would look for one event on every channel, using the code that really sends them."""
    try:
        rule = Rule(**{**body.rule, "id": body.rule.get("id") or "draft", "source": "ui"})
    except ValidationError as error:
        raise HTTPException(422, json.loads(error.json(include_url=False, include_context=False))) from None
    event = _event(body.event, body.source)
    severity = alert_severity(rule, event)
    runbook = find_runbook(rule, event, ctx.runtime.runbooks)
    rendered = render_runbook(runbook, event) if runbook else None
    fields = {p: (None if (v := event.get(p)) is MISSING else v) for p in rule.present.fields}
    payload = {
        "alert_id": 123, "title": render(rule.present.title, event), "body": render(rule.present.body, event),
        "severity": severity, "count": 1, "fields": fields, "runbook": rendered, "url": f"{settings.public_url}/#feed",
    }
    return {
        "matches": explain(rule.when, event)["passed"],
        "severity": severity, "colour": rule.present.colour, "title": payload["title"], "body": payload["body"],
        "fields": [{"path": p, "value": fields[p], "highlight": p in rule.present.highlight} for p in fields],
        "sms": sms_text(payload), "voice": voice_script(payload), "email": email_bodies(payload)[0],
        "notify": [{"recipient": r, "channel": c} for r, c in plan(rule, severity, ctx.runtime.directory)],
        "runbook": rendered,
    }


@app.get("/api/runbooks")
async def list_runbooks() -> list[dict[str, Any]]:
    used: dict[str, list[str]] = {}
    for rule in ctx.runtime.ruleset.rules:
        if rule.runbook:
            used.setdefault(rule.runbook, []).append(rule.name)
    return [{**rb.model_dump(by_alias=True), "used_by": used.get(rb.id, [])} for rb in ctx.runtime.runbooks.values()]


class RunbookIn(BaseModel):
    text: str
    format: str | None = None
    title: str | None = None


@app.put("/api/runbooks/{runbook_id}")
async def save_runbook(runbook_id: str, body: RunbookIn, who: Actor = Depends(actor)) -> dict[str, Any]:
    """Save a runbook pasted in any format. As easy as saving a rule; live within a second."""
    need(who, "editor")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,60}", runbook_id):
        raise HTTPException(422, "use lower-case letters, digits and dashes for the runbook id")
    if not body.text.strip() or len(body.text) > 50_000:
        raise HTTPException(422, "the runbook text is empty or too long")
    runbook = parse_runbook(body.text, runbook_id, body.format).model_copy(update={"id": runbook_id})
    if body.title:
        runbook = runbook.model_copy(update={"title": body.title[:200]})
    async with ctx.pool.acquire() as con, con.transaction():
        await con.execute(
            "INSERT INTO config_docs (kind, id, doc) VALUES ('runbook', $1, $2) "
            "ON CONFLICT (kind, id) DO UPDATE SET doc = EXCLUDED.doc, updated_at = now()",
            runbook_id, runbook.model_dump(by_alias=True),
        )
        await store.audit(con, who.user, "runbook_saved", f"runbook:{runbook_id}", {"steps": len(runbook.steps), "format": runbook.source_format})
        await con.execute("SELECT pg_notify('config_changed', '')")
    return runbook.model_dump(by_alias=True)


@app.get("/api/deliveries")
async def deliveries(owner: str | None = None, limit: int = Query(60, le=500), who: Actor = Depends(actor)) -> list[dict[str, Any]]:
    """The delivery log: every notification, its attempts, and ingest-to-delivery latency."""
    if owner and not who.may_see(owner):
        raise HTTPException(403, "not your team")
    rows = await ctx.pool.fetch(
        "SELECT n.id, n.alert_id, a.title, n.recipient, n.channel, n.status, n.attempts, n.last_error, n.created_at, "
        "extract(epoch FROM n.delivered_ts - n.ingest_ts) * 1000 AS latency_ms "
        "FROM notifications n JOIN alerts a ON a.id = n.alert_id "
        "WHERE ($1::text[] IS NULL OR a.owner = ANY($1)) ORDER BY n.id DESC LIMIT $2",
        [owner] if owner else who.owners, limit,
    )
    return [_json(row) for row in rows]


# ---- demo tools: send sample events and stand in for the outside world ------------------
# Available only in demo mode. They go through the real sources, broker and gateway.


async def demo_tools(request: Request) -> None:
    """On in demo mode, or with DEMO_TOOLS=on. Under single sign-on they also need a signed-in person."""
    if not settings.demo_tools:
        raise HTTPException(404, "demo tools are switched off")
    if settings.auth_mode == "oidc":
        await actor(request)


@app.get("/api/sim/samples", dependencies=[Depends(demo_tools)])
async def sim_samples() -> dict[str, Any]:
    folder = Path(settings.samples_dir)
    return {path.stem: json.loads(path.read_text()) for path in sorted(folder.glob("*.json"))} if folder.is_dir() else {}


class SimSend(BaseModel):
    source: str
    event: Any
    count: int = 1


@app.post("/api/sim/send", status_code=202, dependencies=[Depends(demo_tools)])
async def sim_send(body: SimSend) -> dict[str, Any]:
    """Put an event on the bus the way its source would: on the source's own topic, or on the raw topic."""
    source = ctx.runtime.sources.get(body.source)
    if source is None:
        raise HTTPException(404, f"no source '{body.source}'")
    if ctx.producer is None:
        raise HTTPException(503, "broker not ready, retry shortly")
    count = max(1, min(body.count, 2000))
    value = body.event.encode() if isinstance(body.event, str) else orjson.dumps(body.event)
    pending = []
    for _ in range(count):
        if source.type == "kafka" and source.topic:
            pending.append(await ctx.producer.send(source.topic, value=value))
        else:
            headers = [("source", source.id.encode()), ("source_type", source.type.encode()),
                       ("ingest_ts", repr(time.time()).encode())]
            pending.append(await ctx.producer.send(settings.raw_topic, value=value, headers=headers))
    await asyncio.gather(*pending)
    return {"sent": count, "through": source.type}


async def _gateway(method: str, path: str, **kwargs: Any) -> Any:
    try:
        response = await http().request(method, f"{settings.gateway_url}{path}", **kwargs)
        return response.json()
    except Exception as error:
        raise HTTPException(502, f"the SMS gateway did not answer: {type(error).__name__}") from None


@app.get("/api/sim/phone", dependencies=[Depends(demo_tools)])
async def sim_phone() -> dict[str, Any]:
    """What the fake SMS / phone gateway has received, by person."""
    data = await _gateway("GET", "/messages", params={"limit": 200})
    by_phone = {p.phone: p.id for p in ctx.runtime.directory.people.values() if p.phone}
    return {
        "down": data.get("down", False),
        "page": settings.gateway_public_url,
        "messages": [{"kind": m["kind"], "person": by_phone.get(m["to"]), "text": m["text"], "at": m["at"]}
                     for m in data.get("messages", [])],
    }


class SimReply(BaseModel):
    person: str
    text: str


@app.post("/api/sim/reply", dependencies=[Depends(demo_tools)])
async def sim_reply(body: SimReply) -> dict[str, Any]:
    """A person replies from their phone. Goes through the gateway to the same inbound webhook a provider would call."""
    person = ctx.runtime.directory.people.get(body.person)
    if person is None or not person.phone:
        raise HTTPException(404, "that person has no phone")
    return await _gateway("POST", "/reply", data={"From": person.phone, "Body": body.text})


class SimGateway(BaseModel):
    down: bool


@app.post("/api/sim/gateway", dependencies=[Depends(demo_tools)])
async def sim_gateway(body: SimGateway) -> dict[str, Any]:
    return await _gateway("POST", "/control", data={"down": str(body.down).lower()})


@app.get("/api/sim/mail", dependencies=[Depends(demo_tools)])
async def sim_mail() -> dict[str, Any]:
    """The last emails captured by the mock mail server."""
    try:
        data = (await http().get(f"{settings.mailpit_url}/api/v1/messages", params={"limit": 20})).json()
    except Exception:
        return {"page": settings.mailpit_public_url, "messages": [], "unavailable": True}
    return {
        "page": settings.mailpit_public_url,
        "messages": [{"id": m.get("ID"), "subject": m.get("Subject"), "to": [t.get("Address") for t in m.get("To") or []],
                      "at": m.get("Created"), "snippet": m.get("Snippet")} for m in data.get("messages", [])],
    }


# The web UI is plain files served at /. Falls back to the simulated prototype if it is missing.
_ui = next((p for p in (Path(os.environ.get("UI_DIR", "ui")), Path("ui-mockup")) if (p / "index.html").is_file()), None)
if _ui:
    app.mount("/", StaticFiles(directory=_ui, html=True), name="ui")
