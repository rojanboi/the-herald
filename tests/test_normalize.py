from __future__ import annotations

import pytest

from alertsvc.normalize import (
    MISSING,
    flatten,
    get_path,
    normalise,
    normalise_severity,
    parse_log_line,
    parse_timestamp,
    schema_fingerprint,
    unpack,
)
from alertsvc.rules import RuleError, compile_condition, expand


def test_aliases_are_found_in_all_three_sample_schemas(events):
    workflow, metric, trade = events["workflow_failed"], events["metric_breach"], events["trade_rejected"]
    assert (workflow.get("@type"), workflow.get("@severity"), workflow.get("@service")) == (
        "WORKFLOW_FAILED", "error", "order-settlement")
    assert (metric.get("@type"), metric.get("@severity"), metric.get("@service")) == (
        "HighErrorRate", "critical", "payments-api")
    assert (trade.get("@type"), trade.get("@severity"), trade.get("@service")) == (
        "trade.rejected.v2", MISSING, "trade-gateway")
    assert workflow.get("@timestamp") == pytest.approx(1792746842.331)
    assert trade.get("@timestamp") == pytest.approx(1792833245.0)


def test_unseen_schema_needs_no_config_and_one_mapping_line_adds_severity(events):
    event = events["unseen_margin_call"]
    assert event.get("@type") == "com.example.risk.margin_call.raised"
    assert event.get("data.shortfall.amount") == 1850000.5
    assert event.get("data.positions.symbol") == ["XYZ", "ABC"]
    assert event.get("@severity") is MISSING
    mapped = normalise(event.raw, mapping={"severity": "data.priority"})
    assert mapped.get("@severity") == "critical"
    # Rules work on the raw paths straight away, including through lists.
    rule = {"all": [{"field": "data.shortfall.amount", "op": "gt", "value": 1_000_000},
                    {"field": "data.tags", "op": "contains", "value": "intraday"},
                    {"field": "data.positions.exposure", "op": "gt", "value": 9_000_000}]}
    assert compile_condition(rule)(event)


@pytest.mark.parametrize(
    "value,expected",
    [("WARN", "warning"), ("warning", "warning"), ("ERROR", "error"), ("critical", "critical"), ("FATAL", "critical"),
     ("sev2", "error"), ("P1", "critical"), (3, None), ("4", None), ("banana", None), (None, None)],
)
def test_severity_vocabularies(value, expected):
    assert normalise_severity(value) == expected


def test_numeric_severity_needs_a_scale_or_a_map():
    assert normalise_severity(3, scale="syslog") == "error"
    assert normalise_severity("4", scale="syslog") == "warning"
    assert normalise({"sev": 2}, mapping={"severity": "sev", "severity_scale": "syslog"}).get("@severity") == "critical"
    assert normalise_severity(1, {1: "info", 5: "critical"}) == "info"
    assert normalise_severity("5", {"5": "critical"}) == "critical"


@pytest.mark.parametrize(
    "value,expected",
    [("2026-10-23T09:13:00Z", 1792746780.0), (1792833245000, 1792833245.0), (1792833245, 1792833245.0),
     ("1792833245", 1792833245.0), ("not a date", None)],
)
def test_timestamps(value, expected):
    assert parse_timestamp(value) == expected


def test_get_path_handles_dotted_keys_lists_and_missing():
    doc = {"http.status": 500, "a": {"b": [{"c": 1}, {"c": 2}]}, "error.code": {"x": 1}}
    assert get_path(doc, "http.status") == 500
    assert get_path(doc, "a.b.0.c") == 1
    assert get_path(doc, "a.b.c") == [1, 2]
    assert get_path(doc, "a.b.*.c") == [1, 2]
    assert get_path(doc, "error.code.x") == 1
    assert get_path(doc, "a.nope") is MISSING
    assert flatten(doc)["a.b.1.c"] == 2


def test_alertmanager_batch_becomes_one_event_per_alert():
    batch = {
        "receiver": "herald", "groupKey": "{}:{alertname=\"HighErrorRate\"}", "status": "firing",
        "alerts": [
            {"status": "firing", "labels": {"alertname": "HighErrorRate", "service": "payments-api", "severity": "critical"}},
            {"status": "resolved", "labels": {"alertname": "DiskFull", "service": "ledger", "severity": "warning"}},
        ],
    }
    first, second = (normalise(item) for item in unpack(batch))
    assert (first.get("@type"), first.get("@service"), first.get("@severity")) == ("HighErrorRate", "payments-api", "critical")
    assert (second.get("@type"), second.get("status")) == ("DiskFull", "resolved")
    assert unpack({"a": 1}) == [{"a": 1}]
    assert unpack([{"a": 1}, {"a": 2}]) == [{"a": 1}, {"a": 2}]


def test_text_log_line_becomes_the_same_event_as_its_json_form(events):
    line = "2026-10-23T09:15:01Z WARN pricing-engine-3 instrument=EURUSD latency_ms=1840 Quote latency above threshold"
    assert parse_log_line(line) == events["pricing_log"].raw
    assert parse_log_line('{"level": "INFO", "message": "ok"}') == {"level": "INFO", "message": "ok"}
    assert parse_log_line("???") == {"message": "???"}


def test_schema_fingerprint_ignores_values_and_list_length():
    one = {"a": 1, "items": [{"x": 1}]}
    two = {"a": 99, "items": [{"x": 5}, {"x": 6}]}
    assert schema_fingerprint(one) == schema_fingerprint(two)
    assert schema_fingerprint(one) != schema_fingerprint({"a": 1})


def test_non_object_payloads_are_wrapped():
    assert normalise("disk full").raw == {"message": "disk full"}
    assert normalise(42).raw == {"value": 42}


def test_condition_trees_are_validated():
    assert expand({"type": "X", "error.code": "TIMEOUT"}) == {
        "all": [{"field": "type", "op": "eq", "value": "X"}, {"field": "error.code", "op": "eq", "value": "TIMEOUT"}]}
    for bad in ({}, {"all": []}, {"field": "a", "op": "nope", "value": 1}, {"field": "a", "op": "gt"},
                {"all": [{"field": "a", "value": 1}], "any": [{"field": "b", "value": 2}]}):
        with pytest.raises(RuleError):
            expand(bad)


def test_and_or_not_and_missing_fields():
    event = normalise({"env": "prod", "error": {"code": "REJECTED"}, "amount": 1_500_000})
    tree = {"all": [
        {"field": "amount", "op": "gt", "value": 1_000_000},
        {"any": [{"field": "error.code", "op": "in", "value": ["TIMEOUT", "REJECTED"]},
                 {"field": "env", "op": "eq", "value": "dev"}]},
        {"not": {"field": "env", "op": "eq", "value": "test"}},
        {"field": "nope", "op": "exists", "value": False},
        {"not": {"field": "nope", "op": "eq", "value": "x"}},
    ]}
    assert compile_condition(tree)(event)
    assert not compile_condition({"field": "nope", "op": "gt", "value": 1})(event)
    assert not compile_condition({"field": "nope", "op": "ne", "value": "x"})(event)
    assert not compile_condition({"field": "amount", "op": "gt", "value": "1,000,000"})(event)
    with_headers = normalise({"action": "opened"}, mapping={"headers": {"x-github-event": "pull_request"}})
    assert compile_condition({"field": "@headers.x-github-event", "op": "eq", "value": "pull_request"})(with_headers)
    assert compile_condition({"field": "error.code", "op": "regex", "value": "^REJ"})(event)
