"""Load test: send events through the real sources and report measured numbers.

    python loadtest/run.py --rate 2000 --duration 20 --match-ratio 0.05
    python loadtest/run.py --rate 500 --duration 1 --same-key        # 500 identical errors -> one alert, counter 500

Inside the compose network:
    docker compose run --rm -e LOADTEST_API=http://api:8000 api python loadtest/run.py --rate 2000 --duration 20

It sends at a fixed rate whatever the service does (open loop), so a slow service cannot
hide its own latency. Latency is ingest-to-delivery, read from the outbox table through
/api/stats (filtered to this run): from the moment an event reached the webhook or the broker to the moment the
SMS gateway accepted the notification.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import orjson
from aiokafka import AIOKafkaProducer

API = os.environ.get("LOADTEST_API", "http://127.0.0.1:8080")
BROKER = os.environ.get("KAFKA_BOOTSTRAP", "127.0.0.1:19092")
WEBHOOK_SOURCE = "generic-webhook"
KAFKA_TOPIC = "workflow.events"


def event(run: str, index: int, matching: bool, same_key: bool) -> bytes:
    return orjson.dumps({
        "type": "LOADTEST",
        "run": run,
        "key": run if same_key else f"{run}-{index}",
        "match": matching,
        "seq": index,
        "payload": {"desk": "rates", "notional": 1_000_000 + index, "currency": "EUR", "note": "x" * 120},
    })


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rate", type=int, default=1000, help="events per second, all sources together")
    parser.add_argument("--duration", type=float, default=10, help="seconds to send for")
    parser.add_argument("--match-ratio", type=float, default=0.05, help="share of events that fire the load-test rule")
    parser.add_argument("--source", choices=["both", "webhook", "kafka"], default="both")
    parser.add_argument("--same-key", action="store_true", help="every event is the same error (dedup test)")
    parser.add_argument("--connections", type=int, default=64, help="webhook connections kept open")
    parser.add_argument("--label", default="", help="free text for the results table, e.g. '4 workers'")
    parser.add_argument("--timeout", type=float, default=120, help="seconds to wait for deliveries after sending")
    args = parser.parse_args()

    run = uuid.uuid4().hex[:8]
    total = int(args.rate * args.duration)
    every = 1 if args.same_key else max(1, round(1 / args.match_ratio)) if args.match_ratio > 0 else 0
    is_match = [bool(every) and (i % every == 0 or i == total - 1) for i in range(total)]
    expected_events = sum(is_match)
    expected_deliveries = 1 if args.same_key else expected_events

    producer = AIOKafkaProducer(bootstrap_servers=BROKER, linger_ms=5, acks=1)
    await producer.start()
    client = httpx.AsyncClient(timeout=10.0)
    errors = {"webhook": 0, "kafka": 0}
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()
    target = urlsplit(API)
    head = (f"POST /ingest/{WEBHOOK_SOURCE} HTTP/1.1\r\nHost: {target.netloc}\r\n"
            "Content-Type: application/json\r\nContent-Length: ").encode()

    async def connection() -> None:
        """One keep-alive connection sending requests back to back. A plain socket keeps the
        generator cheap, so it measures the service and not itself."""
        reader, writer = await asyncio.open_connection(target.hostname, target.port or 80)
        while (body := await queue.get()) is not None:
            try:
                writer.write(head + str(len(body)).encode() + b"\r\n\r\n" + body)
                await writer.drain()
                response = await reader.readuntil(b"\r\n\r\n")
                length = int(next((line.split(b":")[1] for line in response.split(b"\r\n")
                                   if line.lower().startswith(b"content-length")), b"0"))
                await reader.readexactly(length)
                if b" 202 " not in response[:20]:
                    errors["webhook"] += 1
            except (OSError, asyncio.IncompleteReadError):
                errors["webhook"] += 1
                reader, writer = await asyncio.open_connection(target.hostname, target.port or 80)
        writer.close()

    connections = [asyncio.create_task(connection()) for _ in range(args.connections)]

    started = time.time()
    clock = time.perf_counter()
    for index in range(total):
        due = clock + index / args.rate
        delay = due - time.perf_counter()
        if delay > 0:
            await asyncio.sleep(delay)
        body = event(run, index, is_match[index], args.same_key)
        via_webhook = args.source == "webhook" or (args.source == "both" and index % 2 == 0)
        if via_webhook:
            queue.put_nowait(body)
        else:
            try:
                await producer.send(KAFKA_TOPIC, body)
            except Exception:
                errors["kafka"] += 1
    for _ in connections:
        queue.put_nowait(None)
    await asyncio.gather(*connections)
    await producer.flush()
    sent_in = time.time() - started

    stats: dict = {}
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        stats = (await client.get(f"{API}/api/stats", params={"run": run})).json()
        if stats["matched_events"] >= expected_events and (stats["delivered"] or 0) >= expected_deliveries \
                and not stats["outstanding"]:
            break
        await asyncio.sleep(0.5)
    await producer.stop()
    await client.aclose()

    processed_in = (stats.get("last_delivered") or time.time()) - started
    result = {
        "label": args.label, "run": run, "source": args.source,
        "events_sent": total, "target_rate": args.rate, "achieved_send_rate": round(total / sent_in),
        "send_errors": errors,
        "events_matched_expected": expected_events, "events_matched_counted": stats.get("matched_events"),
        "deliveries_expected": expected_deliveries, "deliveries_done": stats.get("delivered"),
        "deliveries_outstanding": stats.get("outstanding"),
        "processed_events_per_second": round(total / processed_in) if processed_in > 0 else None,
        "latency_ms": {k: (round(stats[k], 1) if stats.get(k) is not None else None) for k in ("p50_ms", "p99_ms", "max_ms")},
    }
    complete = result["events_matched_counted"] == expected_events and result["deliveries_done"] == expected_deliveries
    result["complete"] = complete

    out = Path(__file__).parent / "results"
    out.mkdir(exist_ok=True)
    (out / f"{time.strftime('%Y%m%d-%H%M%S')}-{run}.json").write_text(json.dumps(result, indent=2))

    print(json.dumps(result, indent=2))
    lat = result["latency_ms"]
    print("\n| run | sent | send rate /s | processed /s | matched | delivered | p50 ms | p99 ms | max ms | complete |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    print(f"| {args.label or run} | {total} | {result['achieved_send_rate']} | {result['processed_events_per_second']} "
          f"| {result['events_matched_counted']}/{expected_events} | {result['deliveries_done']}/{expected_deliveries} "
          f"| {lat['p50_ms']} | {lat['p99_ms']} | {lat['max_ms']} | {'yes' if complete else 'NO'} |")
    if not complete:
        raise SystemExit("not everything was processed and delivered before the timeout: do not quote these numbers")


if __name__ == "__main__":
    asyncio.run(main())
