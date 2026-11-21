"""Name -> implementation registries.

Every extension point (sources, rule operators, channels, runbook parsers, runbook
actions) is one Registry. Adding a new kind of thing is one decorated function or class.
"""
from __future__ import annotations

from typing import Callable, Generic, TypeVar

T = TypeVar("T")


class Registry(Generic[T]):
    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._items: dict[str, T] = {}

    def register(self, name: str) -> Callable[[T], T]:
        def decorator(obj: T) -> T:
            if name in self._items:
                raise ValueError(f"{self.kind} '{name}' is already registered")
            self._items[name] = obj
            return obj

        return decorator

    def get(self, name: str) -> T:
        try:
            return self._items[name]
        except KeyError:
            known = ", ".join(sorted(self._items)) or "none"
            raise KeyError(f"Unknown {self.kind} '{name}'. Known: {known}") from None

    def names(self) -> list[str]:
        return sorted(self._items)

    def __contains__(self, name: object) -> bool:
        return name in self._items
