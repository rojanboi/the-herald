"""The event path: bytes from a topic -> alerts and outbox rows in Postgres.

Stateless. Run as many copies as you like; the broker's consumer group shares the
partitions between them and the database upsert keeps counters correct.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import asyncpg
import orjson

from alertsvc import store
from alertsvc.models import Rule, Runbook, duration_seconds
from alertsvc.normalize import MISSING, Event, flatten, normalise, parse_log_line, unpack
from alertsvc.routing import Directory, alert_severity, group_key, plan
from alertsvc.rules import compile_condition
from alertsvc.runbooks import render_runbook
from alertsvc.settings import settings
from alertsvc.templating import render


@dataclass
class Incoming:
    """One message as it came off a topic."""

    value: bytes
    source: str
    source_type: str
    ingest_ts: float
    headers: dict[str, str] | None = None


@dataclass
class Group:
    rule: Rule
    event: Event
    count: int
    ingest_ts: float


def decode(message: Incoming, runtime: store.Runtime) -> list[Event]:
    """Bytes -> events. JSON objects, JSON arrays, Alertmanager batches and plain text lines all work."""
    source = runtime.sources.get(message.source)
    mapping: dict[str, Any] = dict(source.map) if source else {}
    if message.headers:
        mapping["headers"] = message.headers
    try:
        parsed = orjson.loads(message.value)
    except orjson.JSONDecodeError:
        text = message.value.decode("utf-8", errors="replace")
        parsed = parse_log_line(text, source.pattern if source else None)
    return [
        normalise(item, source=message.source, source_type=message.source_type, mapping=mapping, received_at=message.ingest_ts)
        for item in unpack(parsed)
    ]


def find_runbook(rule: Rule, event: Event, runbooks: dict[str, Runbook]) -> Runbook | None:
    """The runbook the rule names, else the first one whose `matches` fits the event."""
    if rule.runbook and rule.runbook in runbooks:
        return runbooks[rule.runbook]
    for runbook in runbooks.values():
        if runbook.matches and compile_condition(runbook.matches)(event):
            return runbook
    return None


def address(recipient: str, channel: str, directory: Directory, now: datetime) -> str | None:
    """Where a channel should send for this recipient. Teams use their mailbox, or the on-call person's phone."""
    kind, _, ident = recipient.partition(":")
    if kind == "person":
        person = directory.people.get(ident)
        return (person.email if channel == "email" else person.phone) if person else None
    team = directory.teams.get(ident)
    if not team:
        return None
    if channel == "email":
        return team.email
    on_call = directory.people.get(directory.on_call(ident, now) or "")
    return on_call.phone if on_call else None


async def process(pool: asyncpg.Pool, runtime: store.Runtime, messages: list[Incoming]) -> dict[str, int]:
    """Handle one batch in one transaction. Returns counters for logging and metrics."""
    groups: dict[tuple[str, str], Group] = {}
    closing: list[tuple[Rule, str]] = []
    samples: dict[str, Event] = {}
    events = 0
    for message in messages:
        for event in decode(message, runtime):
            events += 1
            samples.setdefault(event.source, event)
            resolved_by = runtime.ruleset.resolves(event)
            closing.extend((rule, group_key(rule, event)) for rule in resolved_by)
            for rule in runtime.ruleset.match(event, skip=resolved_by):
                key = (rule.id, group_key(rule, event))
                if key in groups:
                    groups[key].count += 1  # micro-batching: one upsert per alert per batch
                else:
                    groups[key] = Group(rule, event, 1, message.ingest_ts)

    now = datetime.now(timezone.utc)
    notified = 0
    async with pool.acquire() as con, con.transaction():
        for (rule_id, fingerprint), group in groups.items():
            rule, event = group.rule, group.event
            severity = alert_severity(rule, event)
            runbook = find_runbook(rule, event, runtime.runbooks)
            rendered = render_runbook(runbook, event) if runbook else None
            title = render(rule.present.title, event)[:500]
            body = render(rule.present.body, event)[:4000]
            fields = {p: (None if (v := event.get(p)) is MISSING else v) for p in rule.present.fields}
            window = duration_seconds(rule.group.window)
            row = await con.fetchrow(
                store.UPSERT_ALERT, rule_id, rule.owner, fingerprint, severity, title, body, fields,
                rule.present.highlight, rule.present.colour, group.count, event.raw, rendered, window, event.source,
            )
            sequence: int | None = 0 if row["inserted"] else None
            if sequence is None and window and row["status"] == "firing":
                sequence = await con.fetchval(store.CLAIM_REPEAT, row["id"], window)
            if sequence is not None:
                for recipient, channel in plan(rule, severity, runtime.directory, now):
                    payload = {
                        "alert_id": row["id"], "title": title, "body": body, "severity": severity,
                        "count": row["count"], "recipient": recipient, "fields": fields, "runbook": rendered,
                        "email": address(recipient, "email", runtime.directory, now),
                        "phone": address(recipient, "sms", runtime.directory, now),
                        "url": f"{settings.public_url}/#alert-{row['id']}",
                    }
                    await con.execute(
                        store.INSERT_NOTIFICATION, row["id"], recipient, channel,
                        f"{row['id']}:{recipient}:{channel}:{sequence}", payload, group.ingest_ts,
                    )
                    notified += 1
            await store.publish(con, rule.owner, "alert", row["id"])

        resolved = 0
        for rule, fingerprint in closing:
            for row in await con.fetch(store.RESOLVE_ALERT, rule.id, fingerprint, "system:auto-resolve"):
                resolved += 1
                await store.audit(con, "system:auto-resolve", "resolve", f"alert:{row['id']}", {"rule": rule.id})
                await store.publish(con, row["owner"], "alert", row["id"])

        for source, event in samples.items():
            await _remember(con, source, event)
        if notified:
            await con.execute("SELECT pg_notify('outbox', '')")
    return {"events": events, "alerts": len(groups), "notifications": notified, "resolved": resolved}


_last_sample: dict[str, float] = {}


async def _remember(con: asyncpg.Connection, source: str, event: Event) -> None:
    """Keep one sample per source per second: enough for the field picker and rule dry-runs, cheap under load."""
    if time.monotonic() - _last_sample.get(source, 0.0) < 1.0:
        return
    _last_sample[source] = time.monotonic()
    await con.execute(
        "INSERT INTO recent_events (source, raw, attrs) VALUES ($1, $2, $3)", source, event.raw, event.attrs
    )
    await con.execute(
        "DELETE FROM recent_events WHERE source = $1 AND id <= "
        "(SELECT id FROM recent_events WHERE source = $1 ORDER BY id DESC OFFSET $2 LIMIT 1)",
        source, settings.keep_recent_events,
    )
    paths = list(flatten(event.raw).items())[:200]
    await con.executemany(
        "INSERT INTO seen_fields (source, path, type, example) VALUES ($1, $2, $3, $4) "
        "ON CONFLICT (source, path) DO UPDATE SET seen = seen_fields.seen + 1, last_seen = now(), "
        "type = EXCLUDED.type, example = EXCLUDED.example",
        [(source, path, type(value).__name__, str(value)[:120]) for path, value in paths],
    )
