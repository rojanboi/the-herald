"""Postgres access. The SQL that carries the guarantees (dedup upsert, outbox claim) is here,
in one place, so it can be read and reviewed on its own."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import asyncpg

from alertsvc import security
from alertsvc.configloader import Config
from alertsvc.models import Person, Rule, Runbook, Source, Team
from alertsvc.routing import Directory
from alertsvc.rules import RuleSet

# One open alert per (rule, fingerprint). Concurrent workers all land on the same row and
# add their batch's count to it. `inserted` tells the caller whether this is a new alert.
UPSERT_ALERT = """
INSERT INTO alerts (rule_id, owner, fingerprint, severity, title, body, fields, highlight, colour,
                    count, sample_event, runbook, next_notify_at, source)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
        CASE WHEN $13::int IS NULL THEN NULL ELSE now() + make_interval(secs => $13::int) END, $14)
ON CONFLICT (rule_id, fingerprint) WHERE status <> 'resolved'
DO UPDATE SET count = alerts.count + EXCLUDED.count, last_seen = now()
RETURNING id, (xmax = 0) AS inserted, count, status
"""

# For rules with a group window: at most one repeat notification per window, claimed atomically.
CLAIM_REPEAT = """
UPDATE alerts SET notify_seq = notify_seq + 1, next_notify_at = now() + make_interval(secs => $2::int)
WHERE id = $1 AND status = 'firing' AND next_notify_at <= now()
RETURNING notify_seq
"""

INSERT_NOTIFICATION = """
INSERT INTO notifications (alert_id, recipient, channel, idempotency_key, payload, ingest_ts)
VALUES ($1, $2, $3, $4, $5, to_timestamp($6))
ON CONFLICT (idempotency_key) DO NOTHING
"""

RESOLVE_ALERT = """
UPDATE alerts SET status = 'resolved', resolved_by = $3, resolved_at = now()
WHERE rule_id = $1 AND fingerprint = $2 AND status <> 'resolved'
RETURNING id, owner
"""

# Many delivery workers can run this at once; SKIP LOCKED gives each a different set of rows.
# A row stuck in 'sending' for a minute (its worker died) becomes claimable again.
CLAIM_NOTIFICATIONS = """
UPDATE notifications SET status = 'sending', attempts = attempts + 1, locked_at = now()
WHERE id IN (
    SELECT id FROM notifications
    WHERE (status IN ('pending', 'retrying') AND next_attempt_at <= now())
       OR (status = 'sending' AND locked_at < now() - interval '60 seconds')
    ORDER BY next_attempt_at
    LIMIT $1
    FOR UPDATE SKIP LOCKED
)
RETURNING id, alert_id, recipient, channel, attempts, payload
"""


async def _init_connection(connection: asyncpg.Connection) -> None:
    for kind in ("json", "jsonb"):
        await connection.set_type_codec(kind, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def connect(dsn: str, attempts: int = 60) -> asyncpg.Pool:
    """Wait for Postgres rather than crash: containers start in any order."""
    for attempt in range(attempts):
        try:
            return await asyncpg.create_pool(dsn, min_size=1, max_size=10, init=_init_connection)
        except (OSError, asyncpg.PostgresError):
            if attempt == attempts - 1:
                raise
            await asyncio.sleep(1)
    raise RuntimeError("unreachable")


AUTH_DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    id_hash text PRIMARY KEY, user_ref text NOT NULL, name text NOT NULL, role text NOT NULL,
    teams text[] NOT NULL DEFAULT '{}', provider text NOT NULL, auth_time timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(), last_seen timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL, revoked_at timestamptz
);
CREATE INDEX IF NOT EXISTS sessions_by_user ON sessions (user_ref) WHERE revoked_at IS NULL;
CREATE TABLE IF NOT EXISTS login_attempts (
    id bigserial PRIMARY KEY, username text NOT NULL, ip text NOT NULL, ok boolean NOT NULL,
    at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS login_attempts_by_user ON login_attempts (username, at);
CREATE INDEX IF NOT EXISTS login_attempts_by_ip ON login_attempts (ip, at);
CREATE TABLE IF NOT EXISTS local_account_state (username text PRIMARY KEY, last_totp_step bigint NOT NULL);
"""


async def ensure_auth_tables(pool: asyncpg.Pool) -> None:
    """Same as db/schema.sql; here too so a database created before sign-in existed gets the tables."""
    async with pool.acquire() as con:
        await con.execute("SELECT pg_advisory_lock(421003)")  # several API replicas may start together
        try:
            await con.execute(AUTH_DDL)
        finally:
            await con.execute("SELECT pg_advisory_unlock(421003)")


async def seed_from_files(pool: asyncpg.Pool, config: Config) -> None:
    """Make the database agree with config/: replace file-owned rules, leave UI-owned ones alone."""
    async with pool.acquire() as con, con.transaction():
        await con.execute("SELECT pg_advisory_xact_lock(421001)")  # one seeder at a time
        ids = [rule.id for rule in config.rules]
        await con.execute("DELETE FROM rules WHERE source = 'file' AND NOT (id = ANY($1::text[]))", ids)
        for rule in config.rules:
            await con.execute(
                """
                INSERT INTO rules (id, owner, name, enabled, source, doc) VALUES ($1, $2, $3, $4, 'file', $5)
                ON CONFLICT (id) DO UPDATE
                SET owner = EXCLUDED.owner, name = EXCLUDED.name, enabled = EXCLUDED.enabled, doc = EXCLUDED.doc,
                    version = rules.version + 1, updated_at = now(), updated_by = 'system:config'
                WHERE rules.source = 'file' AND rules.doc IS DISTINCT FROM EXCLUDED.doc
                """,
                rule.id, rule.owner, rule.name, rule.enabled, rule.model_dump(by_alias=True),
            )
        docs: list[tuple[str, str, Any]] = (
            [("team", t.id, t.model_dump(by_alias=True)) for t in config.directory.teams.values()]
            + [("person", p.id, p.model_dump(by_alias=True)) for p in config.directory.people.values()]
            + [("runbook", r.id, r.model_dump(by_alias=True)) for r in config.runbooks.values()]
            + [("source", s.id, s.model_dump(by_alias=True)) for s in config.sources.values()]
        )
        for kind, ident, doc in docs:
            await con.execute(
                "INSERT INTO config_docs (kind, id, doc) VALUES ($1, $2, $3) "
                "ON CONFLICT (kind, id) DO UPDATE SET doc = EXCLUDED.doc, updated_at = now()",
                kind, ident, doc,
            )
        await con.execute("SELECT pg_notify('config_changed', '')")


@dataclass
class Runtime:
    """What a worker needs in memory. Rebuilt whenever config changes."""

    ruleset: RuleSet = field(default_factory=lambda: RuleSet([]))
    runbooks: dict[str, Runbook] = field(default_factory=dict)
    directory: Directory = field(default_factory=Directory)
    sources: dict[str, Source] = field(default_factory=dict)
    stamp: str = ""


async def config_stamp(pool: asyncpg.Pool) -> str:
    """Cheap "has anything changed?" check, polled as a safety net behind LISTEN/NOTIFY."""
    return str(await pool.fetchval(
        "SELECT coalesce(max(updated_at), 'epoch') FROM "
        "(SELECT updated_at FROM rules UNION ALL SELECT updated_at FROM config_docs) t"
    )) + str(await pool.fetchval("SELECT count(*) FROM rules"))


async def load_runtime(pool: asyncpg.Pool) -> Runtime:
    rules = [Rule(**row["doc"]) for row in await pool.fetch("SELECT doc FROM rules WHERE enabled ORDER BY id")]
    docs = await pool.fetch("SELECT kind, doc FROM config_docs")
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for row in docs:
        by_kind.setdefault(row["kind"], []).append(row["doc"])
    return Runtime(
        ruleset=RuleSet(rules),
        runbooks={d["id"]: Runbook(**d) for d in by_kind.get("runbook", [])},
        directory=Directory(
            teams={d["id"]: Team(**d) for d in by_kind.get("team", [])},
            people={d["id"]: Person(**d) for d in by_kind.get("person", [])},
        ),
        sources={d["id"]: Source(**d) for d in by_kind.get("source", [])},
        stamp=await config_stamp(pool),
    )


async def audit(con: asyncpg.Connection, actor: str, action: str, target: str, detail: dict[str, Any] | None = None) -> None:
    """Append to the hash-chained audit log. Serialised with an advisory lock so the chain has one order."""
    await con.execute("SELECT pg_advisory_xact_lock(421002)")
    prev = await con.fetchval("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1") or security.GENESIS
    at = await con.fetchval("SELECT now()")
    body = {"at": at.isoformat(), "actor": actor, "action": action, "target": target, "detail": detail or {}}
    await con.execute(
        "INSERT INTO audit_log (at, actor, action, target, detail, prev_hash, hash) VALUES ($1, $2, $3, $4, $5, $6, $7)",
        at, actor, action, target, detail or {}, prev, security.entry_hash(prev, body),
    )


async def publish(con: asyncpg.Connection, owner: str, kind: str, alert_id: int | None) -> None:
    """Record a feed entry and wake every API replica's SSE clients, in one round trip.
    Ids only: NOTIFY payloads are small."""
    await con.execute(
        "WITH f AS (INSERT INTO feed (owner, kind, alert_id) VALUES ($1, $2, $3) RETURNING id) "
        "SELECT pg_notify('feed', json_build_object('id', f.id, 'owner', $1::text, 'kind', $2::text, "
        "'alert_id', $3::bigint)::text) FROM f",
        owner, kind, alert_id,
    )
