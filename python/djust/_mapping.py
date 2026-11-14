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

from typing import Any, ItemsView, KeysView, ValuesView

__all__ = ["mapping_items", "mapping_keys", "mapping_values"]


def _shadowed(value: Any, name: str) -> bool:
    return isinstance(value, dict) and isinstance(getattr(type(value), name, None), property)


def mapping_items(value: Any) -> ItemsView[Any, Any]:
    """``value.items()``, or ``dict.items(value)`` when a field shadows it."""
    if _shadowed(value, "items"):
        return dict.items(value)
    return value.items()  # type: ignore[no-any-return]


def mapping_values(value: Any) -> ValuesView[Any]:
    """``value.values()``, or ``dict.values(value)`` when a field shadows it."""
    if _shadowed(value, "values"):
        return dict.values(value)
    return value.values()  # type: ignore[no-any-return]


def mapping_keys(value: Any) -> KeysView[Any]:
    """``value.keys()``, or ``dict.keys(value)`` when a field shadows it."""
    if _shadowed(value, "keys"):
        return dict.keys(value)
    return value.keys()  # type: ignore[no-any-return]
