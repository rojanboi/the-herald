"""Turn any payload into an Event without assuming a schema.

The raw payload is kept as it arrived. Rules address it by dotted path
(`error.code`, `payload.notional`). Four optional aliases (`@type`, `@severity`,
`@timestamp`, `@service`) are filled from a list of likely paths, which a source
can override in config. Nothing here is required for a rule to work.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

MISSING: Any = type("Missing", (), {"__repr__": lambda self: "MISSING", "__bool__": lambda self: False})()

SEVERITY_RANK = {"debug": 10, "info": 20, "warning": 30, "error": 40, "critical": 50}

_SEVERITY_WORDS = {
    "trace": "debug", "debug": "debug", "verbose": "debug",
    "info": "info", "informational": "info", "notice": "info", "low": "info", "ok": "info",
    "sev4": "info", "p4": "info", "sev5": "info", "p5": "info",
    "warn": "warning", "warning": "warning", "minor": "warning", "medium": "warning",
    "sev3": "warning", "p3": "warning",
    "err": "error", "error": "error", "major": "error", "high": "error", "sev2": "error", "p2": "error",
    "crit": "critical", "critical": "critical", "fatal": "critical", "emerg": "critical",
    "emergency": "critical", "alert": "critical", "page": "critical", "disaster": "critical",
    "sev1": "critical", "p1": "critical", "sev0": "critical", "p0": "critical",
}
# Bare numbers are ambiguous (syslog counts down, most scales count up), so they are only
# read when the source says `severity_scale: syslog` or gives a `severity_map`.
_SYSLOG = {0: "critical", 1: "critical", 2: "critical", 3: "error", 4: "warning", 5: "info", 6: "info", 7: "debug"}

DEFAULT_ALIASES: dict[str, list[str]] = {
    "type": ["type", "alertname", "labels.alertname", "meta.schema", "event_type", "eventType", "event", "kind"],
    "severity": ["severity", "labels.severity", "level", "log.level", "priority", "meta.severity"],
    "timestamp": ["timestamp", "startsAt", "meta.ts", "time", "ts", "@timestamp", "eventTime", "created_at"],
    "service": ["service", "labels.service", "meta.producer", "workflowName", "logger", "host", "app", "source"],
}


def get_path(obj: Any, path: str) -> Any:
    """Read `a.b.c` from nested dicts and lists. Returns MISSING when absent.

    Keys that themselves contain dots (`{"http.status": 500}`) are found too.
    A path through a list without an index returns the list of values found
    in its elements, so `alerts.labels.severity` works on a batch.
    """
    if path == "":
        return obj
    if isinstance(obj, dict):
        if path in obj:
            return obj[path]
        parts = path.split(".")
        for i in range(len(parts) - 1, 0, -1):
            head = ".".join(parts[:i])
            if head in obj:
                found = get_path(obj[head], ".".join(parts[i:]))
                if found is not MISSING:
                    return found
        return MISSING
    if isinstance(obj, list):
        head, _, rest = path.partition(".")
        if head == "*":
            path = rest
            if not path:
                return obj
        elif head.isdigit():
            return get_path(obj[int(head)], rest) if int(head) < len(obj) else MISSING
        values = [v for v in (get_path(item, path) for item in obj) if v is not MISSING]
        return values if values else MISSING
    return MISSING


def flatten(obj: Any, prefix: str = "", out: dict[str, Any] | None = None) -> dict[str, Any]:
    """`{"a": {"b": [1, 2]}}` -> `{"a.b.0": 1, "a.b.1": 2}`. Feeds the field picker."""
    out = {} if out is None else out
    if isinstance(obj, dict):
        for key, value in obj.items():
            flatten(value, f"{prefix}.{key}" if prefix else str(key), out)
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            flatten(value, f"{prefix}.{index}" if prefix else str(index), out)
    else:
        out[prefix] = obj
    return out


def normalise_severity(
    value: Any, severity_map: dict[Any, str] | None = None, scale: str | None = None
) -> str | None:
    """Map any severity vocabulary onto debug < info < warning < error < critical."""
    if value is None or value is MISSING or isinstance(value, bool):
        return None
    if severity_map:
        mapped = severity_map.get(value, severity_map.get(str(value)))
        if mapped:
            return normalise_severity(mapped)
    word = str(int(value)) if isinstance(value, (int, float)) else str(value).strip().lower()
    if word.isdigit():
        return _SYSLOG.get(int(word)) if scale == "syslog" else None
    return _SEVERITY_WORDS.get(word)


def severity_rank(value: Any) -> int | None:
    name = normalise_severity(value)
    return SEVERITY_RANK[name] if name else None


def parse_timestamp(value: Any) -> float | None:
    """ISO 8601 strings and epoch seconds / milliseconds / microseconds -> epoch seconds."""
    if value is None or value is MISSING or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 1e14:
            return number / 1e6
        if number > 1e11:
            return number / 1e3
        return number if number > 0 else None
    text = str(value).strip()
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return parse_timestamp(float(text))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


@dataclass(slots=True)
class Event:
    raw: dict[str, Any]
    source: str = "unknown"
    source_type: str = "webhook"
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    received_at: float = field(default_factory=time.time)
    attrs: dict[str, Any] = field(default_factory=dict)

    def get(self, path: str) -> Any:
        """`@name` reads a normalised attribute, anything else reads the raw payload."""
        if path.startswith("@"):
            value = get_path(self.attrs, path[1:])
            return MISSING if value is None else value
        return get_path(self.raw, path)


def _first(raw: Any, paths: list[str]) -> Any:
    for path in paths:
        value = get_path(raw, path)
        if value is not MISSING and value is not None and not isinstance(value, (dict, list)):
            return value
    return None


def normalise(
    raw: Any,
    source: str = "unknown",
    source_type: str = "webhook",
    mapping: dict[str, Any] | None = None,
    received_at: float | None = None,
    event_id: str | None = None,
) -> Event:
    """Wrap a payload in an Event and fill the aliases it is possible to fill."""
    if not isinstance(raw, dict):
        raw = {"message": raw} if isinstance(raw, str) else {"value": raw}
    mapping = mapping or {}

    def candidates(alias: str) -> list[str]:
        override = mapping.get(alias)
        extra = [override] if isinstance(override, str) else list(override or [])
        return extra + DEFAULT_ALIASES[alias]

    severity = normalise_severity(
        _first(raw, candidates("severity")), mapping.get("severity_map"), mapping.get("severity_scale")
    )
    attrs = {
        "type": _first(raw, candidates("type")),
        "severity": severity,
        "severity_rank": SEVERITY_RANK[severity] if severity else None,
        "timestamp": parse_timestamp(_first(raw, candidates("timestamp"))),
        "service": _first(raw, candidates("service")),
        "source": source,
        "source_type": source_type,
    }
    if mapping.get("headers"):
        attrs["headers"] = mapping["headers"]
    return Event(
        raw=raw,
        source=source,
        source_type=source_type,
        id=event_id or uuid.uuid4().hex,
        received_at=received_at if received_at is not None else time.time(),
        attrs=attrs,
    )


def unpack(raw: Any) -> list[Any]:
    """Split batch shapes into single events.

    Handles a top-level JSON array and the Alertmanager webhook (`alerts[]`),
    whose alerts each become one event that keeps the batch's receiver and status.
    """
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict) and isinstance(raw.get("alerts"), list) and ("groupKey" in raw or "receiver" in raw):
        shared = {k: raw[k] for k in ("receiver", "groupKey", "externalURL") if k in raw}
        out = []
        for alert in raw["alerts"]:
            if isinstance(alert, dict):
                merged = dict(alert)
                # In a batch the alert's name lives in its labels; lift it so batched and single alerts look alike.
                name = get_path(alert, "labels.alertname")
                if "alertname" not in merged and isinstance(name, str):
                    merged["alertname"] = name
                merged["_batch"] = shared
                out.append(merged)
        return out or [raw]
    return [raw]


_LOG_LINE = re.compile(r"^(?P<timestamp>\S+)\s+(?P<level>[A-Za-z]+)\s+(?P<logger>\S+)\s+(?P<rest>.*)$")
_KEY_VALUE = re.compile(r"(\w[\w.\-]*)=(\"[^\"]*\"|\S+)")


def parse_log_line(line: str, pattern: str | None = None) -> dict[str, Any]:
    """A log line -> dict. JSON lines pass through; text lines are split by `pattern`
    (named groups) or by the default `<time> <LEVEL> <logger> key=value... message`."""
    line = line.strip()
    if line.startswith("{"):
        try:
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                return parsed
        except ValueError:
            pass
    match = re.match(pattern, line) if pattern else _LOG_LINE.match(line)
    if not match:
        return {"message": line}
    out: dict[str, Any] = {k: v for k, v in match.groupdict().items() if v is not None}
    rest = out.pop("rest", None)
    if rest is not None:
        for key, value in _KEY_VALUE.findall(rest):
            value = value.strip('"')
            try:
                out[key] = float(value) if "." in value else int(value)
            except ValueError:
                out[key] = value
        out["message"] = _KEY_VALUE.sub("", rest).strip()
    return out


def schema_fingerprint(raw: Any) -> str:
    """Same set of field paths -> same fingerprint, whatever the values. Identifies a schema."""
    paths = sorted({re.sub(r"\.\d+(?=\.|$)", "[]", p) for p in flatten(raw)})
    return hashlib.sha1("\n".join(paths).encode()).hexdigest()[:12]
