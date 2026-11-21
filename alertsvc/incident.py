"""Incident practice on top of alerts: what a severity means, the status update a responder
should post, and a postmortem draft built from the audit trail.

The structure follows common incident-management practice (see docs/INCIDENT-PLAYBOOK.md):
one named person in charge, regular updates in a fixed shape, and a blameless review after
serious alerts. Everything here is a pure function over data the service already stores.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

# Shown next to the severity picker in the rule editor, so teams choose levels the same way.
SEVERITY_GUIDE: dict[str, dict[str, Any]] = {
    "critical": {
        "level": 1,
        "meaning": "A service or business process is down for everyone, or money, data or a regulatory duty is at risk.",
        "response": "Page now, any hour. One person takes charge. Updates every 30 minutes. Postmortem required.",
        "update_every_minutes": 30,
        "postmortem": True,
    },
    "error": {
        "level": 2,
        "meaning": "Down for some users, or a core function is badly impaired.",
        "response": "Page now, any hour. Updates every 60 minutes. Postmortem required.",
        "update_every_minutes": 60,
        "postmortem": True,
    },
    "warning": {
        "level": 3,
        "meaning": "Degraded but usable, or there is a workaround.",
        "response": "Handled by the owning team in working hours. Update when something changes.",
        "update_every_minutes": 180,
        "postmortem": False,
    },
    "info": {
        "level": None,
        "meaning": "Worth knowing, nothing to do right now.",
        "response": "Shown in the feed only. Nobody is paged.",
        "update_every_minutes": None,
        "postmortem": False,
    },
    "debug": {
        "level": None,
        "meaning": "Diagnostic detail.",
        "response": "Not notified unless a rule asks for it.",
        "update_every_minutes": None,
        "postmortem": False,
    },
}


def alert_key(alert_id: int) -> str:
    """A short, stable name to quote in every update, text and ticket."""
    return f"AL-{alert_id:04d}"


def _time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _clock(value: Any) -> str:
    moment = _time(value)
    return moment.strftime("%H:%M UTC") if moment else "unknown"


def _minutes(start: Any, end: Any) -> str:
    a, b = _time(start), _time(end)
    if not a or not b:
        return "unknown"
    seconds = max((b - a).total_seconds(), 0)
    return f"{seconds / 60:.0f} min" if seconds >= 90 else f"{seconds:.0f} s"


def _steps(alert: dict[str, Any]) -> tuple[list[str], str | None]:
    """(what has been done, the next step not yet done)."""
    state = alert.get("step_state") or {}
    done, upcoming = [], None
    for step in (alert.get("runbook") or {}).get("steps", []):
        current = (state.get(str(step["id"])) or {}).get("state")
        if current in ("done", "failed", "rejected", "awaiting_approval"):
            done.append(f"{step['text']} ({current.replace('_', ' ')})")
        elif upcoming is None:
            upcoming = step["text"]
    return done, upcoming


def status_update(alert: dict[str, Any], now: datetime) -> str:
    """A ready-to-edit update in a fixed shape: what, impact, status, next steps, next update.

    Unknowns are written as "unknown" rather than left out, so readers can tell the
    difference between "not affected" and "not yet known".
    """
    guide = SEVERITY_GUIDE.get(alert["severity"], SEVERITY_GUIDE["info"])
    done, upcoming = _steps(alert)
    if alert["status"] == "resolved":
        status = f"Resolved at {_clock(alert.get('resolved_at'))}."
    elif alert["status"] == "acked":
        status = f"Being worked on since {_clock(alert.get('acked_at'))}."
    else:
        status = "Not yet acknowledged."
    every = guide["update_every_minutes"]
    next_update = (
        "No further updates: resolved." if alert["status"] == "resolved"
        else f"Next update by {_clock(now + timedelta(minutes=every))}." if every
        else "Next update when something changes."
    )
    lines = [
        f"{alert_key(alert['id'])} · {alert['severity'].upper()} · {alert['title']}",
        "",
        f"What is happening: {alert.get('body') or 'unknown'}",
        f"Since: {_clock(alert.get('first_seen'))} (seen {alert.get('count', 1)} time(s), last at {_clock(alert.get('last_seen'))})",
        "Who is affected: unknown — fill in",
        f"In charge: {alert.get('incident_manager') or 'nobody yet'}",
        f"Current status: {status}",
        "Done so far:" if done else "Done so far: nothing yet",
        *[f"  - {item}" for item in done],
        f"Next step: {upcoming or 'unknown — fill in'}",
        next_update,
    ]
    return "\n".join(lines)


def _role(actor: str, alert: dict[str, Any], lead: str | None) -> str:
    """Blameless by default: the review talks about roles and systems, not named people."""
    if actor.startswith("system:"):
        return "Herald (automatic)"
    if actor == alert.get("incident_manager"):
        return "the incident manager"
    if lead and actor == f"person:{lead}":
        return "the team lead"
    return "a responder"


_ACTION_WORDS = {
    "ack": "acknowledged the alert",
    "resolve": "resolved the alert",
    "step_done": "completed a manual step",
    "action_run": "ran an action",
    "approval_requested": "asked for approval",
    "approved": "approved the request",
    "rejected": "rejected the request",
    "note": "noted",
    "incident_manager": "took charge",
}


def postmortem_draft(
    alert: dict[str, Any], timeline: list[dict[str, Any]], lead: str | None = None, names: bool = False
) -> str:
    """A postmortem to start from, not to publish as is.

    Facts the service knows (times, what was done, in what order) are filled in. Judgement
    (impact, causes, what to change) is left as prompts for the people who were there.
    """
    key = alert_key(alert["id"])
    done, _ = _steps(alert)
    rows = []
    for entry in timeline:
        who = entry["actor"] if names else _role(entry["actor"], alert, lead)
        what = _ACTION_WORDS.get(entry["action"], entry["action"].replace("_", " "))
        detail = entry.get("detail") or {}
        extra = detail.get("text") or detail.get("action") or (f"via {detail['via']}" if detail.get("via") else "")
        rows.append(f"| {_clock(entry['at'])} | {who} | {what}{': ' + str(extra) if extra else ''} |")
    not_in_runbook = [e for e in timeline if e["action"] == "note"]
    lines = [
        f"# Postmortem draft: {key} {alert['title']}",
        "",
        "> Draft generated from the audit trail. Blameless: it describes what the system and the roles did, "
        "not who was at fault. Edit before sharing.",
        "",
        "## Summary",
        f"{alert['severity'].capitalize()} alert for {alert['owner']}: {alert.get('body') or alert['title']}. "
        f"Seen {alert.get('count', 1)} time(s).",
        "",
        "## Impact",
        "_Who was affected, what did they see, how many, for how long? Any money, data or regulatory effect?_",
        "",
        "## Detection",
        f"- First seen: {_clock(alert.get('first_seen'))}",
        f"- Time to acknowledge: {_minutes(alert.get('first_seen'), alert.get('acked_at'))}",
        "- _Did we know before the people affected did? Could the rule have fired earlier?_",
        "",
        "## Response",
        "| Time | Who | What |",
        "|---|---|---|",
        *(rows or ["| | | _no recorded actions_ |"]),
        "",
        "## Recovery",
        f"- Resolved: {_clock(alert.get('resolved_at'))} by "
        f"{'Herald (automatic)' if str(alert.get('resolved_by', '')).startswith('system:') else 'a responder'}",
        f"- Time to recover: {_minutes(alert.get('first_seen'), alert.get('resolved_at'))}",
        "",
        "## Causes",
        "- _What directly triggered it?_",
        "- _What should change so this whole class of problem stops? (ask \"why\" until you reach it)_",
        "",
        "## Runbook",
        f"- Runbook used: {(alert.get('runbook') or {}).get('title') or 'none attached'}",
        *[f"- Step taken: {item}" for item in done],
        *[f"- Not in the runbook, noted during the response: {(e.get('detail') or {}).get('text', '')}" for e in not_in_runbook],
        "- _What should be added to, or removed from, the runbook?_",
        "",
        "## Follow-up actions",
        "| Action (start with a verb, be specific) | Owner | Due | Type (priority / improvement) |",
        "|---|---|---|---|",
        "| | | | |",
    ]
    return "\n".join(lines)
