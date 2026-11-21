"""Runbook text in any shape -> the canonical step list.

Three levels, so an unseen format never breaks an alert:
  0. show the text with variables filled in (always possible; see `render_runbook`)
  1. split it into steps with the deterministic parsers below
  2. ask an LLM for structured steps and action bindings, then have a human review them
     (see alertsvc/llm.py; off by default)

Parsers never make a prose step executable. They may set `suggest`, a guess the
reviewer can accept.
"""
from __future__ import annotations

import re
from typing import Any, Callable

import yaml

from alertsvc.models import Runbook, Step
from alertsvc.normalize import Event
from alertsvc.registry import Registry
from alertsvc.templating import render, render_deep

Parser = Callable[[str, str], Runbook]
PARSERS: Registry[Parser] = Registry("runbook parser")

_LIST_ITEM = re.compile(r"^\s*(?:\d+[.)]|[-*+])\s+(?:\[[ xX]?\]\s+)?(.*\S)\s*$")
_HEADING = re.compile(r"^\s*#{1,6}\s+(.*\S)\s*$")
_CONDITION = re.compile(r"^(?:and\s+|then\s+)?if\s+(.+?)(?::|,|\s+then)\s+(.+)$", re.IGNORECASE)
_WAIT = re.compile(r"\bwait\s+(?:for\s+)?(\d+)\s*(second|minute|hour)s?\b", re.IGNORECASE)
_LINK = re.compile(r"^https?://\S+$")
_APPROVAL_ROLE = re.compile(r"approv\w*\s+(?:from|by)\s+(?:the\s+|a\s+)?([\w\s-]+?)(?:[).,;]|$)", re.IGNORECASE)
_SUGGEST = [
    (re.compile(r"\brestart\b", re.I), "restart_service"),
    (re.compile(r"\bretry\b|\bre-?run\b", re.I), "retry_workflow"),
    (re.compile(r"\bescalate\b|\bcall\b.*\bon-?call\b|\bpage\b", re.I), "escalate"),
    (re.compile(r"\bticket\b|\bjira\b", re.I), "open_ticket"),
    (re.compile(r"\bwebhook\b|\bcurl\b", re.I), "webhook"),
]


def _approval(text: str) -> str:
    role = _APPROVAL_ROLE.search(text)
    if role:
        return "role:" + re.sub(r"\s+", "-", role.group(1).strip().lower())
    return "required" if re.search(r"\bapprov|\bsign-?off\b", text, re.I) else "none"


def _prose_step(index: int, text: str) -> Step:
    text = text.strip().rstrip(".")
    condition = None
    match = _CONDITION.match(text)
    if match:
        condition, text = match.group(1).strip(), match.group(2).strip()
        text = text[0].upper() + text[1:]
    wait = _WAIT.search(text)
    if wait and len(text) < 40:
        seconds = int(wait.group(1)) * {"second": 1, "minute": 60, "hour": 3600}[wait.group(2).lower()]
        return Step(id=index, text=text, kind="wait", action="wait", params={"seconds": seconds}, condition=condition)
    suggest = next((name for pattern, name in _SUGGEST if pattern.search(text)), None)
    return Step(id=index, text=text, kind="manual", condition=condition, approval=_approval(text), suggest=suggest)


def _action_label(action: str, params: dict[str, Any]) -> str:
    """A step with an action but no wording gets a sentence a person can read."""
    if action == "retry_workflow":
        return "Retry the workflow"
    if action == "escalate":
        return f"Escalate to {params['team']}" if params.get("team") else "Escalate to another team"
    if action == "restart_service":
        return f"Restart {params['service']}" if params.get("service") else "Restart the service"
    if action == "open_ticket":
        return "Open a ticket"
    if action == "http_get":
        return "Check that the service answers"
    if action == "webhook":
        return "Call the webhook"
    return f"Run {action.replace('_', ' ')}"


@PARSERS.register("yaml")
def parse_yaml(text: str, runbook_id: str) -> Runbook:
    """The structured form from the brief: `runbook`, `matches`, `steps` with check / action / if / approval."""
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict) or not isinstance(doc.get("steps"), list):
        raise ValueError("a YAML runbook needs a top-level 'steps' list")
    steps = []
    for index, item in enumerate(doc["steps"], start=1):
        if isinstance(item, str):
            steps.append(_prose_step(index, item))
            continue
        item = dict(item)
        check, action = item.pop("check", None), item.pop("action", None)
        condition = item.pop("if", None) or item.pop("condition", None)
        approval = str(item.pop("approval", "none"))
        label = item.pop("text", None) or check or (_action_label(action, item) if action else "Step")
        kind = item.pop("kind", None)
        if kind not in ("check", "action", "manual", "wait"):
            kind = "check" if check else ("action" if action else "manual")
        params = item.pop("params", None)
        # Anything else on the step (url, workflowId, repeat_every ...) is kept as a parameter.
        params = {**item, **params} if isinstance(params, dict) else item
        steps.append(
            Step(id=index, text=str(label), kind=kind, action=action, params=params,
                 condition=str(condition) if condition else None, approval=approval)
        )
    name = str(doc.get("runbook") or doc.get("name") or doc.get("title") or runbook_id)
    return Runbook(
        id=str(doc.get("runbook") or doc.get("name") or runbook_id),
        title=str(doc.get("title") or name),
        source_format="yaml",
        source_text=text,
        matches=doc.get("matches") if isinstance(doc.get("matches"), dict) else None,
        steps=steps,
    )


@PARSERS.register("markdown")
def parse_markdown(text: str, runbook_id: str) -> Runbook:
    """Headings give the title; numbered, bulleted and checklist items become steps."""
    title, steps = None, []
    for line in text.splitlines():
        heading = _HEADING.match(line)
        if heading and title is None:
            title = heading.group(1)
            continue
        item = _LIST_ITEM.match(line)
        if item:
            steps.append(_prose_step(len(steps) + 1, item.group(1)))
    if not steps:
        return parse_text(text, runbook_id)
    return Runbook(id=runbook_id, title=title or runbook_id, source_format="markdown", source_text=text, steps=steps)


@PARSERS.register("text")
def parse_text(text: str, runbook_id: str) -> Runbook:
    """Free text: one step per sentence, and per clause when a sentence chains actions."""
    stripped = text.strip()
    if _LINK.match(stripped):
        return Runbook(id=runbook_id, title=runbook_id, source_format="link", source_text=text, link=stripped)
    clauses: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", " ".join(stripped.split())):
        parts = re.split(
            r",\s+(?=(?:and\s+|then\s+)?(?:if|wait|call|restart|retry|check|escalate|post|open|roll|fail)\b)",
            sentence,
            flags=re.IGNORECASE,
        )
        # Keep "if X, do Y" together: a bare "if X" clause joins the action that follows it.
        merged: list[str] = []
        for part in (p.strip() for p in parts):
            if not part:
                continue
            if merged and re.match(r"^(?:and\s+|then\s+)?if\b", merged[-1], re.I) and not _CONDITION.match(merged[-1]):
                merged[-1] = f"{merged[-1]}, {part}"
            else:
                merged.append(part)
        clauses.extend(merged)
    steps =[_prose_step(i, clause) for i, clause in enumerate(clauses, start=1)]
    title = (clauses[0][:60] + "…") if clauses and len(clauses[0]) > 60 else (clauses[0] if clauses else runbook_id)
    return Runbook(id=runbook_id, title=title, source_format="text", source_text=text, steps=steps)


def detect_format(text: str) -> str:
    stripped = text.strip()
    try:
        doc = yaml.safe_load(stripped)
        if isinstance(doc, dict) and isinstance(doc.get("steps"), list):
            return "yaml"
    except yaml.YAMLError:
        pass
    lines = stripped.splitlines()
    if any(_HEADING.match(line) for line in lines) or sum(bool(_LIST_ITEM.match(line)) for line in lines) >= 2:
        return "markdown"
    return "text"


def parse_runbook(text: str, runbook_id: str = "runbook", fmt: str | None = None) -> Runbook:
    """Parse with the named parser, or detect the format. Never raises on odd input:
    the worst case is a runbook with no steps that is shown as text."""
    try:
        return PARSERS.get(fmt or detect_format(text))(text, runbook_id)
    except Exception:
        return Runbook(id=runbook_id, title=runbook_id, source_format="text", source_text=text)


def render_runbook(runbook: Runbook, event: Event) -> dict[str, Any]:
    """The runbook with values from the event filled in, ready for a channel to show."""
    return {
        "id": runbook.id,
        "title": render(runbook.title, event),
        "link": runbook.link,
        "text": render(runbook.source_text, event) if not runbook.steps else None,
        "steps": [
            {
                **step.model_dump(),
                "text": render(step.text, event),
                "condition": render(step.condition, event) if step.condition else None,
                "params": render_deep(step.params, event),
            }
            for step in runbook.steps
        ],
    }
