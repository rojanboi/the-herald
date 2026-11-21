"""The contracts: rules, runbooks, teams, sources. One definition, used by the config
loader, the API (so they appear in OpenAPI) and the worker."""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from alertsvc.normalize import normalise_severity
from alertsvc.rules.engine import RuleError, expand

_DURATION = re.compile(r"^(\d+)\s*(s|m|h|d)$")
_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def duration_seconds(text: str | int | None) -> int | None:
    """`5m` -> 300. Plain numbers are seconds."""
    if text is None:
        return None
    if isinstance(text, int):
        return text
    match = _DURATION.match(text.strip().lower())
    if not match:
        raise ValueError(f"'{text}' is not a duration like 30s, 5m, 2h or 1d")
    return int(match.group(1)) * _UNIT[match.group(2)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Present(Model):
    """How the alert looks."""

    title: str = "{{@type}}"
    body: str = ""
    fields: list[str] = Field(default_factory=list, description="Event fields to show on the alert")
    highlight: list[str] = Field(default_factory=list, description="Fields to emphasise (shown in the rule colour)")
    colour: str | None = None


class Group(Model):
    """Events with the same values for `by` become one alert with a counter."""

    by: list[str] = Field(default_factory=lambda: ["@type", "@service"])
    window: str | None = Field(default=None, description="Notify at most once per window, e.g. 5m")

    @field_validator("window")
    @classmethod
    def _window(cls, value: str | None) -> str | None:
        duration_seconds(value)
        return value


class Hours(Model):
    start: str = Field(alias="from", pattern=r"^\d{2}:\d{2}$")
    end: str = Field(alias="to", pattern=r"^\d{2}:\d{2}$")
    tz: str = "UTC"


class Notify(Model):
    """Who is told, and how. `channels: preferences` uses the recipient's own settings."""

    to: str = Field(description="team:<id>, team:<id>/on-call or person:<id>")
    channels: list[str] | Literal["preferences"] = "preferences"
    hours: Hours | None = Field(default=None, description="Use `channels` only inside these hours")
    otherwise: list[str] = Field(default_factory=list, alias="else", description="Channels outside `hours`")

    @field_validator("to")
    @classmethod
    def _to(cls, value: str) -> str:
        if not re.match(r"^(team:[\w-]+(/on-call)?|person:[\w-]+)$", value):
            raise ValueError("use team:<id>, team:<id>/on-call or person:<id>")
        return value


class Rule(Model):
    id: str
    owner: str = Field(description="team:<id> or person:<id>")
    name: str
    enabled: bool = True
    version: int = 1
    source: Literal["file", "ui"] = "file"
    when: dict[str, Any]
    severity: str | None = Field(default=None, description="Fixed severity; empty means take it from the event")
    present: Present = Field(default_factory=Present)
    group: Group = Field(default_factory=Group)
    notify: list[Notify] = Field(default_factory=list)
    runbook: str | None = None
    resolve_when: dict[str, Any] | None = Field(
        default=None, description="An event matching this (and the same group key) closes the alert"
    )

    @field_validator("when", "resolve_when")
    @classmethod
    def _condition(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        try:
            return expand(value)
        except RuleError as error:
            raise ValueError(str(error)) from None

    @field_validator("severity")
    @classmethod
    def _severity(cls, value: str | None) -> str | None:
        if value is None:
            return None
        name = normalise_severity(value)
        if name is None:
            raise ValueError(f"'{value}' is not a known severity")
        return name


Approval = str  # "none" | "required" | "role:<name>"


class Step(Model):
    id: int
    text: str
    kind: Literal["check", "action", "manual", "wait"] = "manual"
    action: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    condition: str | None = Field(default=None, description="When to do this step, in the author's words")
    approval: Approval = "none"
    suggest: str | None = Field(default=None, description="An action the parser guessed; never runs until reviewed")


class Runbook(Model):
    id: str
    title: str
    source_format: Literal["yaml", "markdown", "text", "link"] = "text"
    source_text: str = ""
    matches: dict[str, Any] | None = None
    steps: list[Step] = Field(default_factory=list)
    parsed_by: Literal["parser", "llm", "human"] = "parser"
    reviewed: bool = False
    link: str | None = None


class Person(Model):
    id: str
    name: str
    email: str | None = None
    phone: str | None = None
    tz: str = "UTC"
    preferences: dict[str, list[str]] = Field(default_factory=dict, description="severity -> channels")


class OnCall(Model):
    rota: list[str]
    start: str = Field(description="ISO date the first person's shift began")
    shift_days: int = 7


class Team(Model):
    id: str
    name: str
    members: list[str] = Field(default_factory=list)
    lead: str | None = None
    email: str | None = None
    on_call: OnCall | None = None
    preferences: dict[str, list[str]] = Field(default_factory=dict, description="severity -> channels")


class Source(Model):
    id: str
    name: str | None = Field(default=None, description="What people call this platform")
    icon: str | None = Field(default=None, description="workflow | monitoring | trade | logs | webhook")
    token_sha256: str | None = Field(default=None, description="Hash of the token issued when the platform was connected from the UI")
    managed: Literal["file", "ui"] = "file"
    type: Literal["webhook", "kafka", "logtail", "http_poll"]
    topic: str | None = None
    path: str | None = None
    url: str | None = Field(default=None, description="For http_poll: the API to poll")
    interval: str = Field(default="30s", description="For http_poll: how often")
    visible_to: list[str] = Field(
        default_factory=list, description="Team ids that may see this source's raw events; empty means everyone"
    )
    partition_key: str | None = Field(
        default=None, description="Field whose value keeps related events in order, e.g. workflowId"
    )
    token_env: str | None = Field(default=None, description="Environment variable holding the webhook token")
    pattern: str | None = Field(default=None, description="Regex with named groups for text log lines")
    map: dict[str, Any] = Field(default_factory=dict, description="Alias overrides: type, severity, timestamp, service")
