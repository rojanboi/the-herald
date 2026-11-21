"""Runbook actions. Adding one is a decorated async function plus a risk level.

Risk decides approval and cannot be lowered by a runbook or by an LLM draft:
a `high` action always waits for a human, whatever the step says.

The targets here are mocks (see the README table). `http_get` is real and guarded.
"""
from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

import httpx

from alertsvc.registry import Registry


@dataclass(frozen=True)
class Action:
    name: str
    risk: str  # "low" | "high"
    run: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
    description: str = ""


ACTIONS: Registry[Action] = Registry("action")


def action(name: str, risk: str = "low", description: str = "") -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(fn: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]) -> Callable[..., Any]:
        ACTIONS.register(name)(Action(name=name, risk=risk, run=fn, description=description))
        return fn

    return decorator


def effective_approval(action_name: str | None, step_approval: str = "none") -> str:
    """The stricter of what the step asks for and what the action's risk demands."""
    if action_name and action_name in ACTIONS and ACTIONS.get(action_name).risk == "high" and step_approval == "none":
        return "required"
    return step_approval


class UnsafeUrl(ValueError):
    pass


def check_url(url: str, allowed_hosts: set[str] | None = None) -> str:
    """Refuse URLs that could reach internal systems (SSRF).

    Hosts on the allowlist pass. Anything else must be http(s) and must not resolve
    to a private, loopback, link-local or reserved address.
    """
    if allowed_hosts is None:
        allowed_hosts = {h.strip() for h in os.environ.get("ACTION_ALLOWED_HOSTS", "").split(",") if h.strip()}
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UnsafeUrl("only http and https URLs with a host are allowed")
    if parts.username or parts.password:
        raise UnsafeUrl("URLs with credentials are not allowed")
    host = parts.hostname.lower()
    if host in allowed_hosts:
        return url
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, parts.port or 80, proto=socket.IPPROTO_TCP)
        except OSError:
            raise UnsafeUrl(f"cannot resolve host '{host}'") from None
        addresses = [ipaddress.ip_address(info[4][0]) for info in infos]
    for address in addresses:
        if not address.is_global:
            raise UnsafeUrl(f"host '{host}' resolves to a non-public address and is not on the allowlist")
    return url


@action("http_get", risk="low", description="GET a URL and report the status")
async def http_get(params: dict[str, Any]) -> dict[str, Any]:
    url = check_url(str(params["url"]))
    async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
        response = await client.get(url)
    return {"ok": response.status_code < 400, "status": response.status_code, "body": response.text[:500]}


@action("webhook", risk="high", description="POST a JSON body to a URL")
async def webhook(params: dict[str, Any]) -> dict[str, Any]:
    url = check_url(str(params["url"]))
    async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
        response = await client.post(url, json=params.get("body", {}))
    return {"ok": response.status_code < 400, "status": response.status_code}


@action("retry_workflow", risk="low", description="Ask the workflow engine to retry a workflow (mock)")
async def retry_workflow(params: dict[str, Any]) -> dict[str, Any]:
    base = os.environ.get("MOCK_SYSTEMS_URL")
    if base:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(f"{base}/workflows/{params['workflowId']}/retry")
        return {"ok": response.status_code < 400, "status": response.status_code, "body": response.text[:500]}
    return {"ok": True, "simulated": True, "message": f"retry requested for {params.get('workflowId')}"}


@action("restart_service", risk="high", description="Restart a service instance (mock)")
async def restart_service(params: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "simulated": True, "message": f"restart requested for {params.get('service')}"}


@action("open_ticket", risk="low", description="Open a ticket in the tracker (mock)")
async def open_ticket(params: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "simulated": True, "ticket": "OPS-0001", "summary": params.get("summary")}


@action("escalate", risk="high", description="Notify another team about this alert")
async def escalate(params: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "simulated": True, "message": f"escalated to {params.get('team')}"}


@action("wait", risk="low", description="Pause before the next step")
async def wait(params: dict[str, Any]) -> dict[str, Any]:
    await asyncio.sleep(min(float(params.get("seconds", 0)), 5.0))  # capped in the demo
    return {"ok": True, "waited_seconds": params.get("seconds", 0)}
