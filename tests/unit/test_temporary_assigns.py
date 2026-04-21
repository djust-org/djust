"""Regression coverage for ``LiveView.temporary_assigns``.

The feature has shipped since earlier releases (``_initialize_temporary_assigns``
and ``_reset_temporary_assigns`` on :class:`djust.live_view.LiveView`) and is
exercised indirectly by several suites, but lacked a dedicated test file.

These tests focus on the reset-after-render semantics and the initialization
lifecycle — the memory-optimization guarantees Phoenix LiveView users rely on.
"""

from __future__ import annotations

from djust.live_view import LiveView


class _ListView(LiveView):
    temporary_assigns = {"messages": [], "notifications": []}


class _MixedView(LiveView):
    temporary_assigns = {
        "items": [],
        "flags": {"seen": False},
        "tags": set(),
        "counter": 0,
    }


def test_reset_restores_default_values_after_render():
    view = _ListView()
    view._initialize_temporary_assigns()
    view.messages = ["hello", "world"]
    view.notifications = [{"id": 1}]

    view._reset_temporary_assigns()

    assert view.messages == []
    assert view.notifications == []


def test_reset_returns_independent_copies_per_type():
    """Defaults must be cloned — no shared mutable state between cycles."""
    view = _MixedView()
    view._initialize_temporary_assigns()

    # Mutate the defaults through the live attributes; after reset, the
    # originals on the class must remain untouched.
    view.items = [1, 2, 3]
    view.flags = {"seen": True}
    view.tags = {"x", "y"}
    view.counter = 99

    view._reset_temporary_assigns()

    assert view.items == [] and view.items is not _MixedView.temporary_assigns["items"]
    assert view.flags == {"seen": False}
    assert view.flags is not _MixedView.temporary_assigns["flags"]
    assert view.tags == set() and view.tags is not _MixedView.temporary_assigns["tags"]
    assert view.counter == 0  # scalar — same object is fine


def test_initialize_is_idempotent():
    view = _ListView()
    view._initialize_temporary_assigns()
    view.messages.append("from-mount")

    # Second call must not wipe the user-populated list.
    view._initialize_temporary_assigns()
    assert view.messages == ["from-mount"]


def test_initialize_preserves_preexisting_attribute():
    view = _ListView()
    view.messages = ["already-set"]
    view._initialize_temporary_assigns()
    assert view.messages == ["already-set"]


def test_instance_level_temporary_assigns_honored():
    """A subclass / instance can override the class-level mapping and reset
    still works against the instance's copy."""

    class _Per(LiveView):
        temporary_assigns = {"a": []}

    view = _Per()
    view.temporary_assigns = {"a": [], "b": []}
    view._initialize_temporary_assigns()

    view.a = [1]
    view.b = [2]
    view._reset_temporary_assigns()
    assert view.a == [] and view.b == []


def test_reset_noop_when_temporary_assigns_empty():
    class _Empty(LiveView):
        temporary_assigns: dict = {}

    view = _Empty()
    view.extra = [1, 2, 3]
    view._reset_temporary_assigns()
    # Untouched — no declared temp assigns.
    assert view.extra == [1, 2, 3]
