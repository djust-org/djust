"""Reading a ``dict`` whose class replaced one of its methods with a field (#4134).

A component ``TypedState`` is a ``dict`` whose fields are properties, and a field
may be named after a dict method: ``items``, ``values``, ``keys`` ... Then
``state.items`` is the field's value, not the method, and a walker that calls
``value.items()`` on every dict it meets raises ``TypeError``.

These helpers call the instance's method as before, and fall back to the
unbound ``dict`` method only when the class has replaced it with a property.
A dict subclass whose method is a real override (Django's ``MultiValueDict``,
whose ``items()`` yields the last value of each key) keeps its own behaviour.
"""

from __future__ import annotations

import weakref
from typing import Any, ItemsView, KeysView, ValuesView

__all__ = ["mapping_items", "mapping_keys", "mapping_values"]

#: Per dict SUBCLASS: whether ``items`` / ``values`` / ``keys`` is a property there.
#: Decided once per class, not per call: these helpers run for every dict of every
#: context on every turn. Weak, so a class created at runtime is not kept alive.
_SHADOWED: "weakref.WeakKeyDictionary[type, tuple[bool, bool, bool]]" = weakref.WeakKeyDictionary()


def _shadows(cls: type) -> "tuple[bool, bool, bool]":
    """``(items, values, keys)``: which of the three ``cls`` replaced with a property."""
    found = _SHADOWED.get(cls)
    if found is None:
        found = tuple(  # type: ignore[assignment]
            isinstance(getattr(cls, name, None), property) for name in ("items", "values", "keys")
        )
        _SHADOWED[cls] = found  # type: ignore[assignment]
    return found  # type: ignore[return-value]


def mapping_items(value: Any) -> ItemsView[Any, Any]:
    """``value.items()``, or ``dict.items(value)`` when a field shadows it."""
    cls = type(value)
    if cls is not dict and issubclass(cls, dict) and _shadows(cls)[0]:
        return dict.items(value)
    return value.items()  # type: ignore[no-any-return]


def mapping_values(value: Any) -> ValuesView[Any]:
    """``value.values()``, or ``dict.values(value)`` when a field shadows it."""
    cls = type(value)
    if cls is not dict and issubclass(cls, dict) and _shadows(cls)[1]:
        return dict.values(value)
    return value.values()  # type: ignore[no-any-return]


def mapping_keys(value: Any) -> KeysView[Any]:
    """``value.keys()``, or ``dict.keys(value)`` when a field shadows it."""
    cls = type(value)
    if cls is not dict and issubclass(cls, dict) and _shadows(cls)[2]:
        return dict.keys(value)
    return value.keys()  # type: ignore[no-any-return]
