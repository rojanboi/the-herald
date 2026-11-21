"""Delivery channels. A channel is one async function: `send(payload) -> provider id`.

It raises on failure; the outbox retries, backs off and falls back. Swapping the fake
SMS gateway for a real provider means replacing the body of one function here.

`payload` keys: alert_id, title, body, severity, count, recipient, email, phone,
fields, runbook (already rendered for this event), url, and optionally `text` to send as-is.
"""
from __future__ import annotations

import html
from email.message import EmailMessage
from typing import Any, Awaitable, Callable

import aiosmtplib
import httpx

from alertsvc.registry import Registry
from alertsvc.settings import settings

class Undeliverable(Exception):
    """Retrying cannot help (for example the recipient has no phone number): fall back at once."""


Channel = Callable[[dict[str, Any]], Awaitable[str | None]]
CHANNELS: Registry[Channel] = Registry("channel")

_LABEL = {"critical": "CRIT", "error": "ERR", "warning": "WARN", "info": "INFO", "debug": "DEBUG"}
_client: httpx.AsyncClient | None = None


def http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=5.0, limits=httpx.Limits(max_connections=200))
    return _client


def sms_text(payload: dict[str, Any], limit: int = 160) -> str:
    """Short on purpose: severity, title, how to acknowledge, a link. Never the payload itself."""
    if payload.get("text"):
        return str(payload["text"])[:limit]
    label = _LABEL.get(payload.get("severity", ""), "ALERT")
    count = f" x{payload['count']}" if payload.get("count", 1) > 1 else ""
    tail = f" Reply ACK {payload['alert_id']}. {payload.get('url', '')}".rstrip()
    head = f"[{label}]{count} "
    room = limit - len(head) - len(tail)
    title = str(payload.get("title", ""))
    if len(title) > room:
        title = title[: max(room - 1, 0)] + "…"
    return f"{head}{title}{tail}"


def voice_script(payload: dict[str, Any]) -> str:
    return (
        f"This is Herald with a {payload.get('severity', '')} alert. {payload.get('title', '')}. "
        "Press 1 to acknowledge. Press 2 to escalate."
    )


def email_bodies(payload: dict[str, Any]) -> tuple[str, str]:
    """Plain and HTML bodies with the full runbook. Every event value is escaped."""
    e = html.escape
    lines = [str(payload.get("body", "")), ""]
    rows = ""
    for key, value in (payload.get("fields") or {}).items():
        lines.append(f"{key}: {value}")
        rows += f"<tr><td>{e(str(key))}</td><td>{e(str(value))}</td></tr>"
    runbook = payload.get("runbook") or {}
    steps_html = ""
    if runbook.get("steps"):
        lines += ["", f"Runbook: {runbook.get('title', '')}"]
        for step in runbook["steps"]:
            note = f" (if {step['condition']})" if step.get("condition") else ""
            gate = " [needs approval]" if step.get("approval", "none") != "none" else ""
            lines.append(f"  {step['id']}. {step['text']}{note}{gate}")
            steps_html += f"<li>{e(step['text'])}{e(note)}{e(gate)}</li>"
    elif runbook.get("text"):
        lines += ["", "Runbook:", runbook["text"]]
        steps_html = f"<li>{e(runbook['text'])}</li>"
    if runbook.get("link"):
        lines.append(f"Runbook link: {runbook['link']}")
    lines += ["", f"Open: {payload.get('url', '')}"]
    page = (
        f"<h2>{e(str(payload.get('title', '')))}</h2><p>{e(str(payload.get('body', '')))}</p>"
        f"<table>{rows}</table>"
        + (f"<h3>{e(str(runbook.get('title', 'Runbook')))}</h3><ol>{steps_html}</ol>" if steps_html else "")
        + f"<p><a href=\"{e(str(payload.get('url', '')))}\">Open in Herald</a></p>"
    )
    return "\n".join(lines), page


@CHANNELS.register("ui")
async def send_ui(payload: dict[str, Any]) -> str | None:
    """The live feed is pushed when the alert is written, so there is nothing to send here."""
    return None


@CHANNELS.register("email")
async def send_email(payload: dict[str, Any]) -> str | None:
    if not payload.get("email"):
        raise Undeliverable(f"no email address for {payload.get('recipient')}")
    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = payload["email"]
    label = _LABEL.get(payload.get("severity", ""), "ALERT")
    message["Subject"] = f"[{label}] {payload.get('title', '')}"[:200].replace("\n", " ")
    plain, page = email_bodies(payload)
    message.set_content(plain)
    message.add_alternative(page, subtype="html")
    await aiosmtplib.send(message, hostname=settings.smtp_host, port=settings.smtp_port, timeout=5)
    return None


async def _gateway(path: str, form: dict[str, str]) -> str | None:
    response = await http().post(f"{settings.gateway_url}{path}", data=form)
    response.raise_for_status()
    return response.json().get("sid")


@CHANNELS.register("sms")
async def send_sms(payload: dict[str, Any]) -> str | None:
    # The form fields mirror a typical SMS provider (To / From / Body), so a real one drops in here.
    if not payload.get("phone"):
        raise Undeliverable(f"no phone number for {payload.get('recipient')}")
    return await _gateway("/sms", {"To": payload["phone"], "From": "HERALD", "Body": sms_text(payload)})


@CHANNELS.register("phone")
async def send_phone(payload: dict[str, Any]) -> str | None:
    if not payload.get("phone"):
        raise Undeliverable(f"no phone number for {payload.get('recipient')}")
    return await _gateway("/call", {"To": payload["phone"], "From": "HERALD", "Say": voice_script(payload)})
