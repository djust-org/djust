"""S1 SPIKE ONLY: tracked ``list`` / ``dict`` subclasses (mutation option i).

``track(value, view, name)`` returns a deep copy of *value* in which every
plain ``list`` and ``dict`` is replaced by a subclass whose mutating methods
record *name* as assigned on *view*. Not for merging: see
docs/reviews/2026-11-12-s1-assignment-tracking-spike.md for what it misses.
"""

from __future__ import annotations

import weakref
from typing import Any


class _Owner:
    __slots__ = ("ref", "name")

    def __init__(self, view: Any, name: str) -> None:
        self.ref = weakref.ref(view)
        self.name = name

    def touched(self) -> None:
        view = self.ref()
        if view is not None:
            from ._state import record_assignment

            record_assignment(view, self.name)


def _mutator(base: type, method: str, wrap_args: bool):
    orig = getattr(base, method)

    def call(self, *args, **kwargs):
        owner = self._djust_owner
        if wrap_args and owner is not None:
            args = tuple(_track(a, owner) for a in args)
            kwargs = {k: _track(v, owner) for k, v in kwargs.items()}
        result = orig(self, *args, **kwargs)
        if owner is not None:
            owner.touched()
        return result

    call.__name__ = method
    return call


class TrackedList(list):
    __slots__ = ("_djust_owner",)


class TrackedDict(dict):
    __slots__ = ("_djust_owner",)


for _name, _wrap in (
    ("append", True),
    ("extend", True),
    ("insert", True),
    ("__setitem__", True),
    ("__iadd__", True),
    ("pop", False),
    ("remove", False),
    ("clear", False),
    ("sort", False),
    ("reverse", False),
    ("__delitem__", False),
    ("__imul__", False),
):
    setattr(TrackedList, _name, _mutator(list, _name, _wrap))
for _name, _wrap in (
    ("__setitem__", True),
    ("update", True),
    ("setdefault", True),
    ("__ior__", True),
    ("pop", False),
    ("popitem", False),
    ("clear", False),
    ("__delitem__", False),
):
    setattr(TrackedDict, _name, _mutator(dict, _name, _wrap))


def _track(value: Any, owner: _Owner) -> Any:
    t = type(value)
    if t is list or t is TrackedList:
        out = TrackedList(_track(v, owner) for v in value)
        out._djust_owner = owner
        return out
    if t is dict or t is TrackedDict:
        out2 = TrackedDict((k, _track(v, owner)) for k, v in value.items())
        out2._djust_owner = owner
        return out2
    return value


def track(value: Any, view: Any, name: str) -> Any:
    return _track(value, _Owner(view, name))
