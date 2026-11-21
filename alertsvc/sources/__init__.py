"""Sources that Herald pulls from (as opposed to webhooks, which push, and topics, which the
worker reads directly). Each is one async function that calls `emit(bytes)` per event.

    python -m alertsvc.sources        # runs every pull source in config/sources.yaml

Adding a source type: write a function here, register it, add an entry to sources.yaml.
"""
from __future__ import annotations

import asyncio
import os
from typing import Awaitable, Callable

import httpx
import orjson

from alertsvc.models import Source, duration_seconds
from alertsvc.registry import Registry

Emit = Callable[[bytes], Awaitable[None]]
SOURCES: Registry[Callable[[Source, Emit], Awaitable[None]]] = Registry("source")


@SOURCES.register("logtail")
async def logtail(source: Source, emit: Emit) -> None:
    """Follow a log file, one event per line. Survives the file not existing yet, and rotation."""
    path, position, inode = source.path or "", 0, None
    while True:
        try:
            stat = os.stat(path)
        except FileNotFoundError:
            await asyncio.sleep(1)
            continue
        if inode is None:
            position = stat.st_size  # start at the end: only new lines are events
        elif stat.st_ino != inode or stat.st_size < position:
            position = 0  # rotated or truncated: read the new file from the top
        inode = stat.st_ino
        if stat.st_size > position:
            with open(path, "rb") as handle:
                handle.seek(position)
                chunk = handle.read()
            complete = chunk.rfind(b"\n") + 1  # leave a half-written last line for next time
            position += complete
            for line in chunk[:complete].splitlines():
                if line.strip():
                    await emit(line)
        await asyncio.sleep(0.25)


@SOURCES.register("http_poll")
async def http_poll(source: Source, emit: Emit) -> None:
    """GET a URL on an interval. A JSON array is one event per element."""
    interval = duration_seconds(source.interval) or 30
    async with httpx.AsyncClient(timeout=10.0) as client:
        while True:
            try:
                response = await client.get(source.url or "")
                response.raise_for_status()
                data = response.json()
                for item in data if isinstance(data, list) else [data]:
                    await emit(orjson.dumps(item))
            except (httpx.HTTPError, ValueError):
                pass  # the next poll tries again
            await asyncio.sleep(interval)
