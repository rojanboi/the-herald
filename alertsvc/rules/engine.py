"""Condition trees: validate, compile to a function, explain a decision.

A condition is data:

    {"all": [ {"field": "payload.desk", "op": "eq", "value": "rates"},
              {"any": [ ... ]},
              {"not": { ... }} ]}

Shorthand `{"type": "WORKFLOW_FAILED", "error.code": "TIMEOUT"}` means all-equal,
which is the form runbooks use in `matches`.
"""
from __future__ import annotations

from typing import Any, Callable

from alertsvc.normalize import MISSING, Event
from alertsvc.rules.operators import LIST_AWARE, OPERATORS

Predicate = Callable[[Event], bool]
_GROUP_KEYS = ("all", "any", "not")


class RuleError(ValueError):
    """The condition tree is not valid. The message is safe to show in the UI."""


def expand(tree: Any) -> dict[str, Any]:
    """Return the tree in full form, validating it on the way."""
    if not isinstance(tree, dict) or not tree:
        raise RuleError("A condition must be a non-empty object")
    groups = [k for k in _GROUP_KEYS if k in tree]
    if groups:
        if len(tree) != 1:
            raise RuleError(f"A group has exactly one of all / any / not, found: {sorted(tree)}")
        key = groups[0]
        if key == "not":
            return {"not": expand(tree["not"])}
        children = tree[key]
        if not isinstance(children, list) or not children:
            raise RuleError(f"'{key}' needs a non-empty list of conditions")
        return {key: [expand(child) for child in children]}
    if "field" in tree:
        op = tree.get("op", "eq")
        if op not in OPERATORS:
            raise RuleError(f"Unknown operator '{op}'. Known: {', '.join(OPERATORS.names())}")
        if not isinstance(tree["field"], str) or not tree["field"]:
            raise RuleError("'field' must be a non-empty path")
        if "value" not in tree and op != "exists":
            raise RuleError(f"Condition on '{tree['field']}' needs a value")
        return {"field": tree["field"], "op": op, "value": tree.get("value", True)}
    # Shorthand map: every key is a field that must equal its value.
    return {"all": [{"field": str(k), "op": "eq", "value": v} for k, v in tree.items()]}


def _leaf(event: Event, field: str, op: str, expected: Any) -> tuple[bool, Any]:
    actual = event.get(field)
    if op == "exists":
        present = actual is not MISSING and actual is not None
        return present == bool(expected), actual
    if actual is MISSING or actual is None:
        return False, actual  # every operator except `exists` is false on a missing field; use `not` to invert
    fn = OPERATORS.get(op)
    if isinstance(actual, list) and op not in LIST_AWARE:
        return any(fn(item, expected) for item in actual), actual
    return fn(actual, expected), actual


def compile_condition(tree: Any) -> Predicate:
    """Validate once, then return a fast function to call per event."""
    return _compile(expand(tree))


def _compile(node: dict[str, Any]) -> Predicate:
    if "all" in node:
        parts = [_compile(child) for child in node["all"]]
        return lambda event: all(part(event) for part in parts)
    if "any" in node:
        parts = [_compile(child) for child in node["any"]]
        return lambda event: any(part(event) for part in parts)
    if "not" in node:
        inner = _compile(node["not"])
        return lambda event: not inner(event)
    field, op, expected = node["field"], node["op"], node["value"]
    return lambda event: _leaf(event, field, op, expected)[0]


def explain(tree: Any, event: Event) -> dict[str, Any]:
    """The same tree with `passed` on every node and `actual` on every leaf.

    Drives "Why did this match?" and "Why didn't it match?" in the UI.
    """
    return _explain(expand(tree), event)


def _explain(node: dict[str, Any], event: Event) -> dict[str, Any]:
    if "all" in node:
        children = [_explain(child, event) for child in node["all"]]
        return {"all": children, "passed": all(c["passed"] for c in children)}
    if "any" in node:
        children = [_explain(child, event) for child in node["any"]]
        return {"any": children, "passed": any(c["passed"] for c in children)}
    if "not" in node:
        inner = _explain(node["not"], event)
        return {"not": inner, "passed": not inner["passed"]}
    passed, actual = _leaf(event, node["field"], node["op"], node["value"])
    return {**node, "actual": None if actual is MISSING else actual, "missing": actual is MISSING, "passed": passed}


def failing_leaves(explained: dict[str, Any]) -> list[dict[str, Any]]:
    """The conditions that stopped a match, for a one-line "failed because" message."""
    if explained["passed"]:
        return []
    for key in ("all", "any"):
        if key in explained:
            return [leaf for child in explained[key] for leaf in failing_leaves(child)]
    if "not" in explained:
        return [{"not": explained["not"], "passed": False}]
    return [explained]


def index_key(tree: Any) -> tuple[str, Any] | None:
    """A cheap pre-filter: one top-level equality the event must satisfy, if the rule has one."""
    node = expand(tree)
    leaves = node.get("all", [node] if "field" in node else [])
    for leaf in leaves:
        if "field" in leaf and leaf["op"] == "eq" and isinstance(leaf["value"], (str, int, float)):
            if not isinstance(leaf["value"], bool):
                return leaf["field"], leaf["value"]
    return None


class RuleSet:
    """All enabled rules, compiled, and bucketed so an event is tested against few of them."""

    def __init__(self, rules: list[Any], version: int = 0) -> None:
        self.version = version
        self.rules = [rule for rule in rules if rule.enabled]
        self._always: list[tuple[Any, Predicate]] = []
        self._buckets: dict[str, dict[Any, list[tuple[Any, Predicate]]]] = {}
        self._resolvers = [
            (rule, compile_condition(rule.resolve_when)) for rule in self.rules if getattr(rule, "resolve_when", None)
        ]
        for rule in self.rules:
            predicate = compile_condition(rule.when)
            key = index_key(rule.when)
            if key is None:
                self._always.append((rule, predicate))
            else:
                field, value = key
                self._buckets.setdefault(field, {}).setdefault(_bucket(value), []).append((rule, predicate))

    def resolves(self, event: Event) -> list[Any]:
        """Rules for which this event says "the problem went away" (their `resolve_when` matches)."""
        return [rule for rule, predicate in self._resolvers if predicate(event)]

    def match(self, event: Event, skip: list[Any] | None = None) -> list[Any]:
        """Rules this event fires. Pass the result of `resolves` as `skip` so a
        "resolved" event closes an alert without also raising it again."""
        skipped = {id(rule) for rule in skip or ()}
        return [rule for rule in self._match(event) if id(rule) not in skipped]

    def _match(self, event: Event) -> list[Any]:
        candidates = list(self._always)
        for field, by_value in self._buckets.items():
            actual = event.get(field)
            if actual is MISSING:
                continue
            for value in actual if isinstance(actual, list) else [actual]:
                candidates.extend(by_value.get(_bucket(value), ()))
        return [rule for rule, predicate in candidates if predicate(event)]


def _bucket(value: Any) -> Any:
    """Bucket numbers and numeric strings together, the way `eq` compares them."""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return float(value)
    return value if isinstance(value, str) else str(value)
