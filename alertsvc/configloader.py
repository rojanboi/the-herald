"""Read the `config/` directory: rules, runbooks, teams, sources.

Files are the GitOps way in. Rules loaded here are marked `source: file`; rules made
in the UI are `source: ui`, so a file reload never overwrites a UI edit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from alertsvc.models import Person, Rule, Runbook, Source, Team
from alertsvc.routing import Directory
from alertsvc.runbooks.parsers import parse_runbook

_RUNBOOK_FORMATS = {".yaml": "yaml", ".yml": "yaml", ".md": "markdown", ".txt": "text"}


@dataclass
class Config:
    rules: list[Rule] = field(default_factory=list)
    runbooks: dict[str, Runbook] = field(default_factory=dict)
    directory: Directory = field(default_factory=Directory)
    sources: dict[str, Source] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def _documents(path: Path, key: str) -> list[dict[str, Any]]:
    """A file may hold one item, a list, or `{<key>: [...]}`."""
    doc = yaml.safe_load(path.read_text()) or []
    if isinstance(doc, dict) and key in doc:
        doc = doc[key]
    return doc if isinstance(doc, list) else [doc]


def load_config(root: str | Path = "config") -> Config:
    """Load everything. A bad file is reported in `errors` and skipped; the rest still loads."""
    root = Path(root)
    config = Config()

    for path in sorted((root / "rules").glob("*.y*ml")):
        for doc in _documents(path, "rules"):
            try:
                config.rules.append(Rule(**{**doc, "source": "file"}))
            except Exception as error:  # report and carry on
                config.errors.append(f"{path.name}: {error}")

    for path in sorted((root / "runbooks").glob("*")):
        fmt = _RUNBOOK_FORMATS.get(path.suffix.lower())
        if fmt:
            runbook = parse_runbook(path.read_text(), runbook_id=path.stem, fmt=fmt)
            config.runbooks[runbook.id] = runbook

    teams_file = root / "teams.yaml"
    if teams_file.exists():
        doc = yaml.safe_load(teams_file.read_text()) or {}
        try:
            config.directory = Directory(
                teams={t["id"]: Team(**t) for t in doc.get("teams", [])},
                people={p["id"]: Person(**p) for p in doc.get("people", [])},
            )
        except Exception as error:
            config.errors.append(f"teams.yaml: {error}")

    sources_file = root / "sources.yaml"
    if sources_file.exists():
        for doc in _documents(sources_file, "sources"):
            try:
                source = Source(**doc)
                config.sources[source.id] = source
            except Exception as error:
                config.errors.append(f"sources.yaml: {error}")

    for rule in config.rules:
        if rule.runbook and rule.runbook not in config.runbooks:
            config.errors.append(f"rule {rule.id}: runbook '{rule.runbook}' not found")
        unknown = unknown_channels(rule)
        if unknown:
            config.errors.append(f"rule {rule.id}: unknown channel(s) {', '.join(unknown)}")
    return config


def unknown_channels(rule: Rule) -> list[str]:
    """Channels a rule names that no registered channel implements."""
    from alertsvc.channels import CHANNELS  # imported late: channels pull in the network clients

    named = {c for target in rule.notify for c in target.otherwise}
    named |= {c for target in rule.notify if target.channels != "preferences" for c in target.channels}
    return sorted(c for c in named if c not in CHANNELS)
