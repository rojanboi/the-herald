from __future__ import annotations

import json
from pathlib import Path

import pytest

from alertsvc.configloader import Config, load_config
from alertsvc.normalize import Event, normalise

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples" / "events"


def load_event(name: str, **kwargs) -> Event:
    return normalise(json.loads((SAMPLES / f"{name}.json").read_text()), **kwargs)


@pytest.fixture(scope="session")
def config() -> Config:
    return load_config(ROOT / "config")


@pytest.fixture(scope="session")
def rules(config: Config) -> dict:
    return {rule.id: rule for rule in config.rules}


@pytest.fixture()
def events() -> dict[str, Event]:
    return {path.stem: load_event(path.stem) for path in SAMPLES.glob("*.json")}
