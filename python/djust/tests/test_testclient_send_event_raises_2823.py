"""Tests for #2823 — ``LiveViewTestClient.send_event()`` must raise (not
silently return a failure envelope) when the requested event handler does
not exist.

Reproduces the issue's own repro: a view with a real ``bump`` handler; calling
``send_event`` with the typo'd name ``bmup`` previously returned
``{"success": False, "error": "No handler found for event: bmup", ...}``
with no exception — a test suite that doesn't inspect the return value (the
downstream app reviewed had ~40 such calls, none inspected) is structurally
blind to a renamed/typo'd handler.

Fix: ``send_event`` now raises :class:`~djust.testing.NoHandlerFoundError` by
default (``raise_on_missing=True``), matching the production WS consumer's
behavior. ``raise_on_missing=False`` is the escape hatch for a test that
deliberately wants to probe the error-envelope shape.
"""

from __future__ import annotations

import pytest

from djust import LiveView
from djust.testing import LiveViewTestClient, NoHandlerFoundError


class SimpleView(LiveView):
    template = "<div>n={{ n }}</div>"

    def mount(self, request, **kwargs):
        self.n = 0

    def bump(self, **kwargs):
        self.n += 1

    def get_context_data(self, **kwargs):
        return {"n": self.n}


def test_existing_handler_still_succeeds():
    """Sanity: a real handler still works and returns success=True."""
    client = LiveViewTestClient(SimpleView).mount()

    result = client.send_event("bump")

    assert result["success"] is True
    assert client.view_instance.n == 1


def test_typo_handler_name_raises_by_default():
    """The issue's own repro: 'bmup' (typo of 'bump') must raise, not return
    a silently-ignorable envelope. Red before the fix (returned a dict)."""
    client = LiveViewTestClient(SimpleView).mount()

    with pytest.raises(NoHandlerFoundError, match="No handler found for event: bmup"):
        client.send_event("bmup")


def test_typo_handler_name_escape_hatch_returns_envelope():
    """raise_on_missing=False keeps the old envelope-return behavior for a
    test that deliberately probes the error-envelope shape."""
    client = LiveViewTestClient(SimpleView).mount()

    result = client.send_event("bmup", raise_on_missing=False)

    assert result["success"] is False
    assert "No handler found for event: bmup" in result["error"]
    # No exception raised; state unchanged.
    assert client.view_instance.n == 0


def test_param_validation_failure_still_returns_envelope_unchanged():
    """Explicitly-unchanged sibling branch: a validation failure (not a
    missing handler) still returns the envelope regardless of
    raise_on_missing — the issue only asked about the missing-handler case
    (#2823 scope)."""

    class TypedView(LiveView):
        template = "<div></div>"

        def mount(self, request, **kwargs):
            pass

        def set_count(self, count: int, **kwargs):
            self.count = count

    client = LiveViewTestClient(TypedView).mount()

    # An uncoercible value trips validate_handler_params, not the
    # missing-handler branch — must NOT raise NoHandlerFoundError.
    result = client.send_event("set_count", count="not-an-int")

    assert result["success"] is False
    assert "error" in result


# --- gate-off self-test (#1468/#2135) ---------------------------------------
# Names the test that goes red when ONLY that mechanism is removed:
# - `test_typo_handler_name_raises_by_default` fails if the `raise` is
#   removed/reverted to a bare `return {...}`.
# - `test_typo_handler_name_escape_hatch_returns_envelope` fails if the
#   `raise_on_missing=False` escape hatch is removed (send_event would raise
#   unconditionally instead).
# The two mechanisms are independently reachable: the first never passes
# `raise_on_missing`, the second always does — so one cannot silently shadow
# the other.
