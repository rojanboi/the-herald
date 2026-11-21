"""Rule operators. Adding one is a decorated function: `fn(actual, expected) -> bool`."""
from __future__ import annotations

import fnmatch
import re
from functools import lru_cache
from typing import Any, Callable

from alertsvc.normalize import severity_rank
from alertsvc.registry import Registry

Operator = Callable[[Any, Any], bool]
OPERATORS: Registry[Operator] = Registry("operator")

# Operators that look at a list as a whole. All others are true if any element passes.
LIST_AWARE = {"contains", "exists"}
MAX_REGEX_LENGTH = 500


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _compare(actual: Any, expected: Any, fn: Callable[[Any, Any], bool]) -> bool:
    a, e = _number(actual), _number(expected)
    if a is not None and e is not None:
        return fn(a, e)
    if isinstance(actual, str) and isinstance(expected, str):
        return fn(actual, expected)
    return False


@OPERATORS.register("eq")
def op_eq(actual: Any, expected: Any) -> bool:
    if isinstance(actual, bool) or isinstance(expected, bool):
        return str(actual).lower() == str(expected).lower()
    if isinstance(actual, str) and isinstance(expected, str):
        return actual == expected
    a, e = _number(actual), _number(expected)
    if a is not None and e is not None:
        return a == e
    return actual == expected


@OPERATORS.register("ne")
def op_ne(actual: Any, expected: Any) -> bool:
    return not op_eq(actual, expected)


@OPERATORS.register("gt")
def op_gt(actual: Any, expected: Any) -> bool:
    return _compare(actual, expected, lambda a, e: a > e)


@OPERATORS.register("gte")
def op_gte(actual: Any, expected: Any) -> bool:
    return _compare(actual, expected, lambda a, e: a >= e)


@OPERATORS.register("lt")
def op_lt(actual: Any, expected: Any) -> bool:
    return _compare(actual, expected, lambda a, e: a < e)


@OPERATORS.register("lte")
def op_lte(actual: Any, expected: Any) -> bool:
    return _compare(actual, expected, lambda a, e: a <= e)


@OPERATORS.register("in")
def op_in(actual: Any, expected: Any) -> bool:
    options = expected if isinstance(expected, (list, tuple, set)) else [expected]
    return any(op_eq(actual, option) for option in options)


@OPERATORS.register("contains")
def op_contains(actual: Any, expected: Any) -> bool:
    if isinstance(actual, (list, tuple)):
        return any(op_eq(item, expected) for item in actual)
    if isinstance(actual, dict):
        return expected in actual
    return str(expected) in str(actual)


@OPERATORS.register("glob")
def op_glob(actual: Any, expected: Any) -> bool:
    return fnmatch.fnmatchcase(str(actual), str(expected))


@lru_cache(maxsize=1024)
def _regex(pattern: str) -> re.Pattern[str]:
    if len(pattern) > MAX_REGEX_LENGTH:
        raise ValueError("regex is too long")
    return re.compile(pattern)


@OPERATORS.register("regex")
def op_regex(actual: Any, expected: Any) -> bool:
    return _regex(str(expected)).search(str(actual)) is not None


@OPERATORS.register("exists")
def op_exists(actual: Any, expected: Any) -> bool:
    # The engine handles missing fields before calling operators; a present value exists.
    return actual is not None


@OPERATORS.register("severity_gte")
def op_severity_gte(actual: Any, expected: Any) -> bool:
    a, e = severity_rank(actual), severity_rank(expected)
    return a is not None and e is not None and a >= e
