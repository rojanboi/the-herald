"""Acceptance tests taken straight from the brief: its four example rules against its
three sample events (plus the pricing log line the Pricing rule implies)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from alertsvc.normalize import normalise
from alertsvc.routing import alert_severity, group_key, plan
from alertsvc.rules import RuleSet, compile_condition, explain, failing_leaves
from alertsvc.templating import render

SETTLEMENTS = "settlements-order-settlement-failed"
PRICING = "pricing-engine-warnings"
RATES = "rates-large-trade-rejected"
ANNA = "anna-payments-critical"

EXPECTED = {
    "workflow_failed": {SETTLEMENTS},
    "metric_breach": {ANNA},
    "trade_rejected": {RATES},
    "pricing_log": {PRICING},
    "workflow_succeeded": set(),
    "unseen_margin_call": set(),
}


def test_config_loads_without_errors(config):
    assert config.errors == []
    assert {SETTLEMENTS, PRICING, RATES, ANNA} <= {rule.id for rule in config.rules}
    assert set(config.runbooks) == {"settlement-timeout", "payments-high-error-rate", "pricing-latency"}


@pytest.mark.parametrize("event_name,expected", EXPECTED.items())
def test_each_rule_matches_only_its_event(config, events, event_name, expected):
    matched = {rule.id for rule in RuleSet(config.rules).match(events[event_name])}
    assert matched == expected


def test_indexed_ruleset_agrees_with_plain_evaluation(config, events):
    ruleset = RuleSet(config.rules)
    for event in events.values():
        plain = {rule.id for rule in config.rules if compile_condition(rule.when)(event)}
        assert {rule.id for rule in ruleset.match(event)} == plain


def test_rates_title_is_rendered_from_the_event(rules, events):
    title = render(rules[RATES].present.title, events["trade_rejected"])
    assert title == "Rejected EUR 25000000 — LIMIT_BREACH"
    assert render("{{payload.notional | number}}", events["trade_rejected"]) == "25,000,000"


def test_settlements_alert_shows_step_and_error_in_red(rules, events):
    rule, event = rules[SETTLEMENTS], events["workflow_failed"]
    assert rule.present.colour == "red"
    assert rule.present.highlight == ["step", "error.message"]
    assert render(rule.present.title, event) == "Settlement failed at validate-counterparty"
    assert render(rule.present.body, event) == "TIMEOUT: Counterparty service did not respond in 5000 ms"


def test_settlements_phones_and_texts_whoever_is_on_call(config, rules):
    monday = datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc)
    assert set(plan(rules[SETTLEMENTS], "critical", config.directory, monday)) == {
        ("person:priya", "phone"),
        ("person:priya", "sms"),
        ("team:settlements", "ui"),
    }
    next_week = datetime(2026, 11, 23, 10, 0, tzinfo=timezone.utc)
    assert ("person:marcus", "phone") in plan(rules[SETTLEMENTS], "critical", config.directory, next_week)


@pytest.mark.parametrize(
    "london_time,channel",
    [("07:59", "email"), ("08:00", "sms"), ("19:59", "sms"), ("20:00", "email"), ("23:30", "email")],
)
def test_anna_gets_sms_in_working_hours_and_email_otherwise(config, rules, london_time, channel):
    hour, minute = map(int, london_time.split(":"))
    # 20 November is winter time, so London is UTC.
    now = datetime(2026, 11, 20, hour, minute, tzinfo=timezone.utc)
    channels = {c for recipient, c in plan(rules[ANNA], "critical", config.directory, now) if recipient == "person:anna"}
    assert channels == {channel, "ui"}


def test_anna_window_follows_london_time_in_summer(config, rules):
    # 07:30 UTC on 1 July is 08:30 in London, so it is inside the window.
    now = datetime(2026, 7, 1, 7, 30, tzinfo=timezone.utc)
    assert ("person:anna", "sms") in plan(rules[ANNA], "critical", config.directory, now)


@pytest.mark.parametrize(
    "notional,matches",
    [(25_000_000, True), (10_000_001, True), (10_000_000, False), (2_000_000, False), ("25000000", True), ("n/a", False)],
)
def test_rates_notional_boundary(rules, notional, matches):
    event = normalise(
        {"meta": {"schema": "trade.rejected.v2"}, "payload": {"desk": "rates", "notional": notional, "currency": "EUR"}}
    )
    assert compile_condition(rules[RATES].when)(event) is matches


def test_other_desks_and_other_schemas_do_not_match(rules):
    base = {"meta": {"schema": "trade.rejected.v2"}, "payload": {"desk": "rates", "notional": 25_000_000}}
    fx = {**base, "payload": {**base["payload"], "desk": "fx"}}
    accepted = {**base, "meta": {"schema": "trade.accepted.v2"}}
    v3 = {**base, "meta": {"schema": "trade.rejected.v3"}}
    predicate = compile_condition(rules[RATES].when)
    assert not predicate(normalise(fx))
    assert not predicate(normalise(accepted))
    assert predicate(normalise(v3))


def test_explain_names_the_condition_that_failed(rules):
    event = normalise({"meta": {"schema": "trade.rejected.v2"}, "payload": {"desk": "rates", "notional": 2_000_000}})
    explained = explain(rules[RATES].when, event)
    assert explained["passed"] is False
    failed = failing_leaves(explained)
    assert [(leaf["field"], leaf["op"], leaf["actual"]) for leaf in failed] == [("payload.notional", "gt", 2_000_000)]


def test_500_identical_errors_share_one_group_key(rules, events):
    rule = rules[SETTLEMENTS]
    keys = {group_key(rule, normalise(dict(events["workflow_failed"].raw))) for _ in range(500)}
    assert len(keys) == 1
    other = normalise({**events["workflow_failed"].raw, "workflowId": "WF-20261023-00043"})
    assert group_key(rule, other) not in keys


def test_pricing_groups_per_instrument(rules, events):
    rule = rules[PRICING]
    eurusd = events["pricing_log"]
    another_engine = normalise({**eurusd.raw, "logger": "pricing-engine-7"})
    gbpusd = normalise({**eurusd.raw, "instrument": "GBPUSD"})
    assert group_key(rule, eurusd) == group_key(rule, another_engine)
    assert group_key(rule, eurusd) != group_key(rule, gbpusd)
    assert rule.group.window == "5m"
    assert alert_severity(rule, eurusd) == "warning"


def test_resolved_event_closes_instead_of_firing(config, events, rules):
    ruleset = RuleSet(config.rules)
    resolved = events["metric_resolved"]
    closing = ruleset.resolves(resolved)
    assert [rule.id for rule in closing] == [ANNA]
    assert ruleset.match(resolved, skip=closing) == []
    assert group_key(rules[ANNA], resolved) == group_key(rules[ANNA], events["metric_breach"])

    succeeded = events["workflow_succeeded"]
    assert [rule.id for rule in ruleset.resolves(succeeded)] == [SETTLEMENTS]
    assert group_key(rules[SETTLEMENTS], succeeded) == group_key(rules[SETTLEMENTS], events["workflow_failed"])
