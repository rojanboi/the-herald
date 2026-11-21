"""The outbox worker: claim due notifications, send them, record the result.

Nothing is dropped. A failed send is retried with exponential backoff and jitter; after
the last attempt the row is kept as 'dead' and the alert moves to the next channel in
the fallback chain (phone -> sms -> email -> ui).
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
import asyncpg

from alertsvc import store
from alertsvc.channels import CHANNELS, Undeliverable
from alertsvc.routing import FALLBACK
from alertsvc.settings import settings

log = logging.getLogger("herald.delivery")
BATCH = 200


def backoff_seconds(attempt: int) -> float:
    """2s, 4s, 8s ... capped at a minute, with jitter so retries do not arrive in step."""
    return min(60.0, settings.retry_base_seconds * 2 ** (attempt - 1)) * random.uniform(0.5, 1.5)


async def _send(row: asyncpg.Record) -> tuple[asyncpg.Record, float, str | None, str | None, bool]:
    """Returns (row, when, provider id, error, permanent)."""
    try:
        provider_id = await CHANNELS.get(row["channel"])(row["payload"])
        return row, time.time(), provider_id, None, False
    except Undeliverable as error:  # no point retrying: go straight to the fallback channel
        return row, time.time(), None, str(error)[:500], True
    except Exception as error:  # any other failure is a retry, never a lost alert
        return row, time.time(), None, f"{type(error).__name__}: {error}"[:500], False


async def deliver_once(pool: asyncpg.Pool) -> int:
    """Claim and send one batch. Returns how many rows were handled."""
    rows = await pool.fetch(store.CLAIM_NOTIFICATIONS, BATCH)
    if not rows:
        return 0
    results = await asyncio.gather(*(_send(row) for row in rows))
    sent = [(row["id"], at, provider_id) for row, at, provider_id, error, _ in results if error is None]
    failed = [(row, error, permanent) for row, _, _, error, permanent in results if error is not None]
    touched: set[int] = {row["alert_id"] for row in rows}

    async with pool.acquire() as con, con.transaction():
        if sent:
            await con.execute(
                "UPDATE notifications n SET status = 'sent', delivered_ts = to_timestamp(d.at), provider_id = d.pid, "
                "last_error = NULL FROM unnest($1::bigint[], $2::float8[], $3::text[]) AS d(id, at, pid) WHERE n.id = d.id",
                [s[0] for s in sent], [s[1] for s in sent], [s[2] for s in sent],
            )
        for row, error, permanent in failed:
            if not permanent and row["attempts"] < settings.max_attempts:
                await con.execute(
                    "UPDATE notifications SET status = 'retrying', last_error = $2, "
                    "next_attempt_at = now() + make_interval(secs => $3) WHERE id = $1",
                    row["id"], error, backoff_seconds(row["attempts"]),
                )
                continue
            await con.execute("UPDATE notifications SET status = 'dead', last_error = $2 WHERE id = $1", row["id"], error)
            fallback = FALLBACK.get(row["channel"])
            log.warning("notification %s dead on %s after %s attempts: %s", row["id"], row["channel"], row["attempts"], error)
            if fallback:  # the payload carries both the email address and the phone number
                await con.execute(
                    "INSERT INTO notifications (alert_id, recipient, channel, idempotency_key, payload, ingest_ts) "
                    "SELECT alert_id, recipient, $2, idempotency_key || ':fallback:' || $2, payload, ingest_ts "
                    "FROM notifications WHERE id = $1 ON CONFLICT (idempotency_key) DO NOTHING",
                    row["id"], fallback,
                )
        feed = await con.fetch(
            "INSERT INTO feed (owner, kind, alert_id) SELECT owner, 'delivery', id FROM alerts WHERE id = ANY($1::bigint[]) "
            "RETURNING id, owner, alert_id",
            list(touched),
        )
        for entry in feed[:50]:  # enough to refresh an open screen; a storm does not need one push per alert
            await con.execute(
                "SELECT pg_notify('feed', $1)",
                f'{{"id": {entry["id"]}, "owner": "{entry["owner"]}", "kind": "delivery", "alert_id": {entry["alert_id"]}}}',
            )
    return len(rows)


async def run(pool: asyncpg.Pool, wake: asyncio.Event) -> None:
    """Send whenever there is work; otherwise sleep until the pipeline signals new rows or a retry is due."""
    while True:
        try:
            handled = await deliver_once(pool)
        except (asyncpg.PostgresError, OSError) as error:
            log.error("delivery loop error, retrying: %s", error)
            handled = 0
            await asyncio.sleep(1)
        if handled < BATCH:
            wake.clear()
            try:
                await asyncio.wait_for(wake.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                pass
