"""Render a ui simple class through its ui ``*Component`` twin (#4002, slice 3).

Each simple class that has a twin in this package (Alert, Badge, Button, Card, Dropdown,
Modal, Progress, Spinner) maps its arguments onto the twin's and returns the twin's markup,
so each widget has one renderer. Each simple class's docstring gives its argument mapping;
``docs/website/guides/migrating-to-2.0.md`` ("Markup that changed") the markup differences.
"""

from __future__ import annotations

from typing import Any


def plain_text(value: Any) -> str:
    """``value`` as a plain ``str``, never a marked-safe one.

    The simple classes escape every text value, whatever it is marked as; the twins escape
    with ``conditional_escape``, which lets a marked-safe value through. Handing the twin a
    plain string keeps the simple class's contract.
    """
    if value is None:
        return ""
    # ``str.__str__`` copies a ``str`` subclass (``SafeString``) into an exact ``str``;
    # ``str(value)`` and ``"%s" % value`` hand a ``SafeString`` back unchanged.
    return str.__str__(str(value))


def render_twin(twin_cls: type, component_id: str, **kwargs: Any) -> str:
    """Build ``twin_cls`` with a stable ``component_id`` and return its markup."""
    return twin_cls(component_id=component_id, **kwargs).render()
