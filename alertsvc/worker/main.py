"""Worker process: consume events, write alerts, deliver notifications.

    python -m alertsvc.worker.main

Scale out with `docker compose up --scale worker=4`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import signal
import time

import asyncpg
from aiokafka import AIOKafkaConsumer
from aiokafka.errors import KafkaError

from alertsvc import store
from alertsvc.settings import settings
from alertsvc.worker import delivery
from alertsvc.worker.pipeline import Incoming, process

log = logging.getLogger("herald.worker")


class State:
    def __init__(self) -> None:
        self.runtime = store.Runtime()
        self.reload = asyncio.Event()
        self.outbox = asyncio.Event()


async def keep_config_fresh(pool: asyncpg.Pool, state: State) -> None:
    """Reload rules within a moment of a change (NOTIFY), and at least every 30 seconds (poll)."""
    while True:
        try:
            await asyncio.wait_for(state.reload.wait(), timeout=30)
        except asyncio.TimeoutError:
            if await store.config_stamp(pool) == state.runtime.stamp:
                continue
        state.reload.clear()
        try:
            state.runtime = await store.load_runtime(pool)
            log.info("config loaded: %s rules, %s runbooks", len(state.runtime.ruleset.rules), len(state.runtime.runbooks))
        except Exception as error:  # keep the last good rules if a bad one slips in
            log.error("config reload failed, keeping previous rules: %s", error)


async def listen(state: State) -> None:
    """A dedicated connection for LISTEN, reconnecting if it drops."""
    while True:
        try:
            con = await asyncpg.connect(settings.database_url)
            await con.add_listener("config_changed", lambda *_: state.reload.set())
            await con.add_listener("outbox", lambda *_: state.outbox.set())
            while not con.is_closed():
                await asyncio.sleep(5)
        except (OSError, asyncpg.PostgresError) as error:
            log.warning("listener connection lost, reconnecting: %s", error)
        await asyncio.sleep(1)


async def start_consumer(topics: list[str]) -> AIOKafkaConsumer:
    while True:
        consumer = AIOKafkaConsumer(
            *topics,
            bootstrap_servers=settings.kafka_bootstrap,
            group_id=settings.consumer_group,
            enable_auto_commit=False,  # commit only after the database transaction
            auto_offset_reset="earliest",
            max_poll_records=1000,
        )
        try:
            await consumer.start()
            return consumer
        except (KafkaError, OSError) as error:
            log.warning("broker not ready, retrying: %s", error)
            await consumer.stop()
            await asyncio.sleep(2)


async def process_safely(pool: asyncpg.Pool, state: State, messages: list[Incoming]) -> dict[str, int]:
    """Never lose a batch and never get stuck on one.

    Database trouble: keep the batch and retry until it is back. Anything else (a bug, a
    payload that breaks a rule): retry the messages one at a time and park the ones that
    still fail in `dead_events`, so one bad message cannot block the partition.
    """
    while True:
        try:
            return await process(pool, state.runtime, messages)
        except (asyncpg.PostgresConnectionError, asyncpg.InterfaceError, OSError) as error:
            log.error("database unavailable, retrying the batch in 1s: %s", error)
            await asyncio.sleep(1)
        except Exception as error:
            if len(messages) == 1:
                log.error("parking one message from %s: %r", messages[0].source, error)
                await pool.execute(
                    "INSERT INTO dead_events (source, error, raw) VALUES ($1, $2, $3)",
                    messages[0].source, repr(error)[:1000], messages[0].value,
                )
                return {"events": 0, "alerts": 0, "notifications": 0, "resolved": 0, "dead": 1}
            log.error("batch failed (%r); retrying its %s messages one by one", error, len(messages))
            totals = {"events": 0, "alerts": 0, "notifications": 0, "resolved": 0, "dead": 0}
            for message in messages:
                for key, value in (await process_safely(pool, state, [message])).items():
                    totals[key] = totals.get(key, 0) + value
            return totals


async def consume(pool: asyncpg.Pool, state: State) -> None:
    topic_source = {s.topic: s.id for s in state.runtime.sources.values() if s.type == "kafka" and s.topic}
    consumer = await start_consumer([settings.raw_topic, *topic_source])
    log.info("consuming %s", [settings.raw_topic, *topic_source])
    try:
        while True:
            batches = await consumer.getmany(timeout_ms=100, max_records=1000)
            messages: list[Incoming] = []
            for partition, records in batches.items():
                for record in records:
                    headers = {key: value.decode() for key, value in record.headers}
                    if partition.topic == settings.raw_topic:
                        messages.append(Incoming(
                            value=record.value,
                            source=headers.get("source", "unknown"),
                            source_type=headers.get("source_type", "webhook"),
                            ingest_ts=float(headers.get("ingest_ts", record.timestamp / 1000)),
                            headers=json.loads(headers["headers"]) if headers.get("headers") else None,
                        ))
                    else:  # a topic that is itself a source: the broker's timestamp is the ingest time
                        messages.append(Incoming(
                            value=record.value, source=topic_source.get(partition.topic, partition.topic),
                            source_type="kafka", ingest_ts=record.timestamp / 1000,
                        ))
            if not messages:
                continue
            started = time.perf_counter()
            stats = await process_safely(pool, state, messages)
            await consumer.commit()
            if stats["alerts"] or stats["resolved"]:
                log.info("batch %s in %.1f ms", stats, (time.perf_counter() - started) * 1000)
    finally:
        await consumer.stop()


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    pool = await store.connect(settings.database_url)
    state = State()
    while not state.runtime.ruleset.rules:  # the API seeds config on its start; wait for it
        state.runtime = await store.load_runtime(pool)
        if not state.runtime.ruleset.rules:
            await asyncio.sleep(1)
    log.info("started with %s rules", len(state.runtime.ruleset.rules))
    tasks = [
        asyncio.create_task(job)
        for job in (listen(state), keep_config_fresh(pool, state), consume(pool, state), delivery.run(pool, state.outbox))
    ]
    # On SIGTERM (a scale-down or a deploy) stop cleanly: the consumer leaves its group at once,
    # so the remaining workers pick up its partitions without waiting for a session timeout.
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, lambda: [task.cancel() for task in tasks])
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        log.info("stopped")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
