"""Fake SMS / phone gateway, and the mock systems that runbook actions call.

Stands in for a real provider. It accepts the same kind of request (form fields To / From /
Body), shows what was "sent" on a phone-like page, can be switched "down" to demo retries,
and posts replies back to Herald the way a provider's inbound webhook would.

    uvicorn alertsvc.gateway.main:app --port 8090
"""
from __future__ import annotations

import html
import itertools
import os
import time
from collections import deque
from typing import Any

import httpx
from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse

app = FastAPI(title="Herald fake gateway")
API_URL = os.environ.get("API_URL", "http://localhost:8080")
messages: deque[dict[str, Any]] = deque(maxlen=500)
state = {"down": False, "received": 0, "rejected": 0}
_ids = itertools.count(1)


def _accept(kind: str, to: str, text: str) -> dict[str, str]:
    if state["down"]:
        state["rejected"] += 1
        raise HTTPException(503, "gateway is down (simulated)")
    state["received"] += 1
    sid = f"{'SM' if kind == 'sms' else 'CA'}{next(_ids):08d}"
    messages.appendleft({"sid": sid, "kind": kind, "to": to, "text": text, "at": time.time()})
    return {"sid": sid, "status": "queued"}


@app.post("/sms")
async def sms(To: str = Form(...), Body: str = Form(...), From: str = Form("HERALD")) -> dict[str, str]:
    return _accept("sms", To, Body)


@app.post("/call")
async def call(To: str = Form(...), Say: str = Form(...), From: str = Form("HERALD")) -> dict[str, str]:
    return _accept("call", To, Say)


@app.get("/messages")
async def list_messages(limit: int = 50) -> dict[str, Any]:
    return {**state, "messages": list(messages)[:limit]}


@app.post("/control")
async def control(down: bool = Form(...)) -> dict[str, Any]:
    """Switch the simulated outage on or off."""
    state["down"] = down
    return state


@app.post("/reply")
async def reply(From: str = Form(...), Body: str = Form(...)) -> dict[str, Any]:
    """A person replies to a text. Forwarded to Herald's inbound webhook."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.post(f"{API_URL}/inbound/sms", data={"From": From, "Body": Body})
    messages.appendleft({"sid": f"IN{next(_ids):08d}", "kind": "reply", "to": From, "text": Body, "at": time.time()})
    return response.json()


# --- mock systems for runbook actions (reached as http://counterparty-service/...) ---


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "counterparty-service (mock)"}


@app.post("/workflows/{workflow_id}/retry")
async def retry(workflow_id: str) -> dict[str, str]:
    return {"workflowId": workflow_id, "status": "retry accepted (mock)"}


@app.get("/", response_class=HTMLResponse)
async def page() -> str:
    rows = "".join(
        f"<li class='{m['kind']}'><b>{html.escape(m['kind'].upper())}</b> to {html.escape(m['to'])}"
        f"<br>{html.escape(m['text'])}</li>"
        for m in list(messages)[:30]
    )
    banner = "<p class='down'>Gateway is DOWN (simulated): sends are being refused</p>" if state["down"] else ""
    return f"""<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="3">
<title>Fake gateway</title>
<style>body{{font:15px system-ui;max-width:520px;margin:24px auto;padding:0 16px}}
li{{list-style:none;border:1px solid #ccc;border-radius:12px;padding:10px;margin:8px 0}}
.call{{background:#eef6ff}}.reply{{background:#f1fbef;margin-left:60px}}.down{{color:#b00020;font-weight:600}}
form{{display:flex;gap:8px;margin:8px 0}}input{{flex:1;padding:8px}}button{{padding:8px 12px}}</style>
<h1>Fake SMS and phone gateway</h1>{banner}
<p>Accepted {state['received']}, refused {state['rejected']}.</p>
<form method="post" action="/control"><input type="hidden" name="down" value="{str(not state['down']).lower()}">
<button>{'Bring gateway back up' if state['down'] else 'Take gateway down'}</button></form>
<form method="post" action="/reply"><input name="From" placeholder="+447700900003" required>
<input name="Body" placeholder="YES 4821 or ACK 17" required><button>Reply</button></form>
<ul style="padding:0">{rows}</ul>"""
