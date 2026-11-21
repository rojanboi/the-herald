from __future__ import annotations

import time

import pytest

from alertsvc import security
from alertsvc.normalize import normalise
from alertsvc.runbooks import check_url, detect_format, effective_approval, parse_runbook, render_runbook
from alertsvc.runbooks.actions import UnsafeUrl

# ---- the three sample runbooks from the brief ----------------------------------------


def test_yaml_runbook_keeps_actions_conditions_and_approvals(config, events):
    runbook = config.runbooks["settlement-timeout"]
    assert runbook.source_format == "yaml"
    assert runbook.matches == {"type": "WORKFLOW_FAILED", "error.code": "TIMEOUT"}
    check, retry, escalate = runbook.steps
    assert (check.kind, check.action, check.text) == ("check", "http_get", "Is counterparty-service healthy?")
    assert (retry.kind, retry.action, retry.approval) == ("action", "retry_workflow", "none")
    assert (escalate.action, escalate.condition, escalate.approval) == ("escalate", "retry fails twice", "required")

    rendered = render_runbook(runbook, events["workflow_failed"])
    assert rendered["steps"][1]["params"] == {"workflowId": "WF-20261023-00042"}
    assert rendered["steps"][0]["params"] == {"url": "http://counterparty-service/health"}


def test_markdown_runbook_becomes_steps_with_conditions_and_approval(config):
    runbook = config.runbooks["payments-high-error-rate"]
    assert (runbook.source_format, runbook.title, len(runbook.steps)) == ("markdown", "HighErrorRate on payments-api", 4)
    failover, rollback = runbook.steps[1], runbook.steps[2]
    assert failover.condition == "one region"
    assert failover.approval == "role:team-lead"
    assert rollback.condition == "all regions"
    assert all(step.kind == "manual" and step.action is None for step in runbook.steps)


def test_free_text_runbook_is_split_into_steps(config):
    runbook = config.runbooks["pricing-latency"]
    assert runbook.source_format == "text"
    texts = [step.text for step in runbook.steps]
    assert texts == [
        "When pricing latency goes over a second, it's usually the cache",
        "Restart the pricing-engine instance named in the alert",
        "wait 2 minutes",
        "Call the pricing on-call person",
    ]
    restart, wait, call = runbook.steps[1:]
    assert restart.suggest == "restart_service" and restart.action is None
    assert (wait.kind, wait.params) == ("wait", {"seconds": 120})
    assert (call.condition, call.suggest) == ("latency is still high", "escalate")


# ---- formats we have not seen ---------------------------------------------------------


@pytest.mark.parametrize(
    "text,fmt,steps",
    [
        ("- [ ] Check the queue depth\n- [x] Drain the queue\n- [ ] Tell the desk", "markdown", 3),
        ("1) Stop the feed\n2) Wait 30 seconds\n3) Start the feed", "markdown", 3),
        ("https://wiki.example.com/runbooks/ledger-lag", "text", 0),
        ("steps:\n  - Check the dashboard\n  - action: open_ticket\n    summary: 'Lag on {{service}}'", "yaml", 2),
        ("", "text", 0),
        ("{{{{ not: [valid", "text", 1),
    ],
)
def test_unseen_runbook_formats_never_fail(text, fmt, steps):
    assert detect_format(text) == fmt
    runbook = parse_runbook(text, "unseen")
    assert len(runbook.steps) == steps
    render_runbook(runbook, normalise({"service": "ledger"}))


def test_link_only_runbook_is_kept_as_a_link():
    runbook = parse_runbook("https://wiki.example.com/runbooks/ledger-lag", "ledger")
    assert (runbook.source_format, runbook.link) == ("link", "https://wiki.example.com/runbooks/ledger-lag")


def test_missing_variables_are_shown_not_hidden():
    runbook = parse_runbook("1. Restart {{service}} on {{host}}\n2. Tell {{team}}", "x")
    rendered = render_runbook(runbook, normalise({"service": "ledger"}))
    assert rendered["steps"][0]["text"] == "Restart ledger on ⟨missing: host⟩"


# ---- approvals and actions -------------------------------------------------------------


def test_a_risky_action_cannot_be_marked_as_needing_no_approval():
    assert effective_approval("escalate", "none") == "required"
    assert effective_approval("restart_service", "none") == "required"
    assert effective_approval("restart_service", "role:team-lead") == "role:team-lead"
    assert effective_approval("retry_workflow", "none") == "none"
    assert effective_approval("http_get", "required") == "required"


@pytest.mark.parametrize(
    "url",
    ["http://169.254.169.254/latest/meta-data/", "http://127.0.0.1:5432/", "http://localhost/admin",
     "http://10.0.0.8/", "http://[::1]/", "file:///etc/passwd", "ftp://example.com/", "http://user:pw@example.com/"],
)
def test_actions_refuse_internal_and_odd_urls(url):
    with pytest.raises(UnsafeUrl):
        check_url(url, allowed_hosts=set())


def test_allowlisted_internal_host_is_permitted():
    assert check_url("http://counterparty-service/health", allowed_hosts={"counterparty-service"})


def test_reply_parsing():
    assert security.parse_reply("YES 4821") == ("approve", "4821")
    assert security.parse_reply(" ack #4821 ") == ("ack", "4821")
    assert security.parse_reply("ACK 2") == ("ack", "2")
    assert security.parse_reply("no 123456") == ("reject", "123456")
    for junk in ("yes", "YES 12", "please approve 4821 now", "", "DROP TABLE 4821"):
        assert security.parse_reply(junk) is None


def test_approval_code_is_bound_to_the_approval_and_the_approver():
    stored = security.hash_code("secret", "4821", approval_id=7, approver="person:marcus")
    assert stored == security.hash_code("secret", "4821", 7, "person:marcus")
    assert stored != security.hash_code("secret", "4821", 7, "person:priya")
    assert stored != security.hash_code("secret", "4821", 8, "person:marcus")
    assert len(security.new_code()) == 4 and security.new_code(6).isdigit()


# ---- webhook signatures, payload limits, audit chain -----------------------------------


def test_webhook_signature_and_replay_window():
    body, now = b'{"type":"X"}', int(time.time())
    signature = security.sign("s3cret", body, now)
    assert security.verify_signature("s3cret", body, str(now), signature)
    assert not security.verify_signature("wrong", body, str(now), signature)
    assert not security.verify_signature("s3cret", body + b" ", str(now), signature)
    old = now - 3600
    assert not security.verify_signature("s3cret", body, str(old), security.sign("s3cret", body, old))
    assert not security.verify_signature("s3cret", body, None, None)


def test_depth_limit_stops_early():
    nested: dict = {}
    cursor = nested
    for _ in range(200):
        cursor["a"] = {}
        cursor = cursor["a"]
    assert security.depth(nested) > security.MAX_DEPTH
    assert security.depth({"a": [1, {"b": 2}]}) == 4


def test_audit_chain_detects_tampering():
    entries, prev = [], security.GENESIS
    for index in range(3):
        body = {"at": f"2026-11-20T10:0{index}:00Z", "actor": "person:priya", "action": "ack", "target": f"alert:{index}"}
        digest = security.entry_hash(prev, body)
        entries.append({**body, "prev_hash": prev, "hash": digest})
        prev = digest
    assert security.verify_chain(entries) is None
    entries[1]["actor"] = "person:someone-else"
    assert security.verify_chain(entries) == 1


# ---- LLM guardrails (the provider call is not part of the skeleton) --------------------


def test_llm_draft_cannot_invent_actions_or_lower_approval():
    from alertsvc.llm import accept_runbook_draft

    draft = {"title": "Pricing latency", "steps": [
        {"text": "Restart the engine", "kind": "action", "action": "restart_service", "params": {"service": "{{logger}}"},
         "approval": "none"},
        {"text": "Wipe the cache", "kind": "action", "action": "rm_rf_everything"},
        {"text": "Check the dashboard", "kind": "manual"},
    ]}
    runbook = accept_runbook_draft(draft, "pricing-latency", "free text")
    assert (runbook.parsed_by, runbook.reviewed) == ("llm", False)
    assert runbook.steps[0].approval == "required"
    assert (runbook.steps[1].action, runbook.steps[1].kind) == (None, "manual")
    with pytest.raises(Exception):
        accept_runbook_draft({"steps": [{"text": "x", "run_shell": "rm -rf /"}]}, "x", "")


def test_llm_prompts_carry_schema_shape_not_values():
    from alertsvc.llm import schema_shape

    shape = schema_shape({"payload": {"notional": 25000000, "account": "GB00 1234"}, "api_key": "abc"}, keep_values=True)
    assert shape["payload.notional"] == "int, e.g. 25000000"
    assert shape["payload.account"] == "str" and shape["api_key"] == "str"
    assert set(schema_shape({"a": {"b": "secret value"}}).values()) == {"str"}


# ---- incident practice (docs/INCIDENT-PLAYBOOK.md) --------------------------------------


def test_playbook_runbook_templates_parse_into_steps():
    from pathlib import Path

    templates = sorted((Path(__file__).resolve().parent.parent / "samples" / "runbooks").glob("*.yaml"))
    assert len(templates) == 3
    for path in templates:
        runbook = parse_runbook(path.read_text(), path.stem)
        assert runbook.source_format == "yaml" and runbook.id == path.stem and len(runbook.steps) >= 6
        assert {step.kind for step in runbook.steps} <= {"check", "action", "manual", "wait"}
        for step in runbook.steps:  # a risky action in a template still needs a human
            if step.action in ("restart_service", "escalate", "webhook"):
                assert effective_approval(step.action, step.approval) != "none"
    outage = parse_runbook(next(p for p in templates if p.stem == "critical-service-outage").read_text(), "x")
    restart = next(step for step in outage.steps if step.action == "restart_service")
    assert restart.approval == "role:team-lead" and restart.params["service"] == "{{service}}"


def test_status_update_and_postmortem_are_built_from_the_alert_and_its_audit_trail():
    from datetime import datetime, timezone

    from alertsvc import incident

    t = lambda minute: datetime(2026, 10, 23, 9, minute, tzinfo=timezone.utc)  # noqa: E731
    alert = {
        "id": 42, "owner": "team:settlements", "severity": "critical", "status": "resolved",
        "title": "Settlement failed at validate-counterparty", "body": "TIMEOUT: Counterparty service did not respond",
        "count": 500, "first_seen": t(14), "last_seen": t(20), "acked_at": t(16), "resolved_at": t(40),
        "resolved_by": "system:auto-resolve", "incident_manager": "person:priya",
        "runbook": {"title": "settlement-timeout", "steps": [
            {"id": 1, "text": "Is counterparty-service healthy?"}, {"id": 2, "text": "Run retry_workflow"},
            {"id": 3, "text": "Run escalate"}]},
        "step_state": {"1": {"state": "done"}, "2": {"state": "done"}},
    }
    timeline = [
        {"at": t(16), "actor": "person:priya", "action": "ack", "detail": {"via": "sms"}},
        {"at": t(18), "actor": "person:priya", "action": "action_run", "detail": {"action": "retry_workflow"}},
        {"at": t(25), "actor": "person:priya", "action": "note", "detail": {"text": "Counterparty firewall change at 09:10"}},
        {"at": t(30), "actor": "person:marcus", "action": "approved", "detail": {"action": "escalate", "via": "sms"}},
        {"at": t(40), "actor": "system:auto-resolve", "action": "resolve", "detail": {}},
    ]
    draft = incident.postmortem_draft(alert, timeline, lead="marcus")
    assert "# Postmortem draft: AL-0042" in draft
    assert "Time to acknowledge: 2 min" in draft and "Time to recover: 26 min" in draft
    assert "| 09:16 UTC | the incident manager | acknowledged the alert: via sms |" in draft
    assert "| 09:30 UTC | the team lead | approved the request: escalate |" in draft
    assert "priya" not in draft and "marcus" not in draft  # blameless: roles, not names
    assert "Counterparty firewall change" in draft
    assert "person:priya" in incident.postmortem_draft(alert, timeline, lead="marcus", names=True)

    open_alert = {**alert, "status": "acked", "resolved_at": None}
    update = incident.status_update(open_alert, now=t(20))
    assert update.startswith("AL-0042 · CRITICAL · Settlement failed")
    assert "Next update by 09:50 UTC." in update and "Who is affected: unknown" in update
    assert "Next step: Run escalate" in update
    assert incident.SEVERITY_GUIDE["critical"]["level"] == 1 and not incident.SEVERITY_GUIDE["warning"]["postmortem"]
