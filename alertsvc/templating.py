"""Logic-less templates: `{{path}}` and `{{path | filter}}`, nothing else.

There is no code execution and no loops, so a template written by a rule editor
cannot do anything except read fields. Values come from events and are untrusted:
pass `escape=html.escape` when the output goes into HTML.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

from alertsvc.normalize import MISSING, Event

_TAG = re.compile(r"\{\{\s*([^{}|]+?)\s*(?:\|\s*([a-z_]+)\s*)?\}\}")


def _number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{int(number):,}" if number == int(number) else f"{number:,.2f}"


FILTERS: dict[str, Callable[[Any], str]] = {
    "number": _number,
    "upper": lambda v: str(v).upper(),
    "lower": lambda v: str(v).lower(),
}


def render(template: str, event: Event, escape: Callable[[str], str] | None = None) -> str:
    def replace(match: re.Match[str]) -> str:
        path, filter_name = match.group(1), match.group(2)
        value = event.get(path)
        if value is MISSING or value is None:
            return f"⟨missing: {path}⟩"
        if filter_name:
            if filter_name not in FILTERS:
                return f"⟨unknown filter: {filter_name}⟩"
            text = FILTERS[filter_name](value)
        elif isinstance(value, (dict, list)):
            text = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
        elif isinstance(value, float) and value == int(value):
            text = str(int(value))
        else:
            text = str(value)
        return escape(text) if escape else text

    return _TAG.sub(replace, template)


def variables(template: str) -> list[str]:
    """The paths a template reads, in order, without duplicates."""
    seen: dict[str, None] = {}
    for match in _TAG.finditer(template):
        seen.setdefault(match.group(1), None)
    return list(seen)


def render_deep(value: Any, event: Event) -> Any:
    """Render every string inside a nested structure (used for runbook action parameters)."""
    if isinstance(value, str):
        return render(value, event)
    if isinstance(value, dict):
        return {k: render_deep(v, event) for k, v in value.items()}
    if isinstance(value, list):
        return [render_deep(v, event) for v in value]
    return value
