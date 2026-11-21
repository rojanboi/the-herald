from __future__ import annotations

import asyncio
import logging
import time

from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaError

from alertsvc.configloader import load_config
from alertsvc.settings import settings
from alertsvc.sources import SOURCES

log = logging.getLogger("herald.sources")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    pull = [s for s in load_config(settings.config_dir).sources.values() if s.type in SOURCES]
    while True:
        producer = AIOKafkaProducer(bootstrap_servers=settings.kafka_bootstrap, acks="all", linger_ms=5)
        try:
            await producer.start()
            break
        except (KafkaError, OSError):
            await producer.stop()
            await asyncio.sleep(2)

    def emitter(source_id: str, source_type: str):
        async def emit(value: bytes) -> None:
            headers = [("source", source_id.encode()), ("source_type", source_type.encode()),
                       ("ingest_ts", repr(time.time()).encode())]
            await producer.send_and_wait(settings.raw_topic, value=value, headers=headers)
        return emit

    log.info("running pull sources: %s", [s.id for s in pull])
    try:
        await asyncio.gather(*(SOURCES.get(s.type)(s, emitter(s.id, s.type)) for s in pull))
    finally:
        await producer.stop()


if __name__ == "__main__":
    asyncio.run(main())
