"""From a matched rule to a list of (recipient, channel): who is told, and how.

Pure functions over the team directory, so working hours, on-call and preferences
are unit-testable without a database.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from alertsvc.models import Hours, Notify, Person, Rule, Team
from alertsvc.normalize import MISSING, SEVERITY_RANK, Event

DEFAULT_PREFERENCES = {"critical": ["phone", "sms"], "error": ["sms", "email"], "warning": ["email"], "info": ["ui"]}
# If a channel is dead after all retries, the alert moves to the next one. The UI is the last resort.
FALLBACK = {"phone": "sms", "sms": "email", "email": "ui"}


@dataclass
class Directory:
    teams: dict[str, Team] = field(default_factory=dict)
    people: dict[str, Person] = field(default_factory=dict)

    def on_call(self, team_id: str, now: datetime) -> str | None:
        team = self.teams.get(team_id)
        if not team or not team.on_call or not team.on_call.rota:
            return team.lead if team else None
        elapsed = (now.date() - date.fromisoformat(team.on_call.start)).days
        shift = max(elapsed, 0) // team.on_call.shift_days
        return team.on_call.rota[shift % len(team.on_call.rota)]

    def preferences(self, recipient: str) -> dict[str, list[str]]:
        kind, _, ident = recipient.partition(":")
        holder = self.people.get(ident) if kind == "person" else self.teams.get(ident)
        return {**DEFAULT_PREFERENCES, **(holder.preferences if holder else {})}


def within_hours(hours: Hours, now: datetime) -> bool:
    """True from `from` (inclusive) to `to` (exclusive) in the window's time zone. Handles overnight windows."""
    local = now.astimezone(ZoneInfo(hours.tz)).time().replace(tzinfo=None)
    start, end = time.fromisoformat(hours.start), time.fromisoformat(hours.end)
    if start <= end:
        return start <= local < end
    return local >= start or local < end


def channels_for(target: Notify, recipient: str, severity: str, directory: Directory, now: datetime) -> list[str]:
    if target.hours is not None and not within_hours(target.hours, now):
        chosen = target.otherwise
    elif target.channels == "preferences":
        chosen = directory.preferences(recipient).get(severity, ["ui"])
    else:
        chosen = target.channels
    return list(dict.fromkeys(chosen))


def plan(rule: Rule, severity: str, directory: Directory, now: datetime | None = None) -> list[tuple[str, str]]:
    """Every (recipient, channel) pair this rule notifies right now, without duplicates."""
    now = now or datetime.now(timezone.utc)
    out: dict[tuple[str, str], None] = {}
    for target in rule.notify:
        recipient = target.to
        if recipient.endswith("/on-call"):
            team_id = recipient.split(":", 1)[1].removesuffix("/on-call")
            person = directory.on_call(team_id, now)
            recipient = f"person:{person}" if person else f"team:{team_id}"
        for channel in channels_for(target, recipient, severity, directory, now):
            out[(recipient, channel)] = None
    return list(out)


def alert_severity(rule: Rule, event: Event) -> str:
    """The rule's fixed severity, else the event's, else info."""
    if rule.severity:
        return rule.severity
    found = event.attrs.get("severity")
    return found if found in SEVERITY_RANK else "info"


def group_key(rule: Rule, event: Event) -> str:
    """Events with the same key are the same alert. The dedup fingerprint."""
    parts = [rule.id]
    for path in rule.group.by:
        value = event.get(path)
        parts.append("" if value is MISSING else str(value))
    return hashlib.sha1("\x1f".join(parts).encode()).hexdigest()[:20]
