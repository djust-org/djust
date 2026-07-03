"""#1981 — ``LiveView.set_changed_keys`` escape hatch for in-place nested mutation.

djust's auto change-detection (``_snapshot_assigns``) deliberately does NOT
deep-copy state, so an in-place mutation of a nested container (e.g.
``self.rows[0]["cards"].append(x)``) is invisible and the event auto-skips
(0 patches / noop). ``set_changed_keys`` is the sanctioned escape hatch: it
marks keys changed and forces a re-render.

These tests run the REAL production event path (``ViewRuntime.dispatch_event``
via the ADR-022 spine), NOT ``LiveViewTestClient`` — the test client calls
``render_with_diff`` directly and bypasses the ``pre==post`` skip, so it would
FALSELY show the hatch as unnecessary (reproduction-fidelity, #1650/#1638).

Non-tautology (#1468/#1200): ``test_in_place_without_hatch_is_skipped`` IS the
gate-off of ``test_in_place_with_hatch_renders`` — same in-place mutation, minus
the ``set_changed_keys`` call. The first NOOPs, the second RENDERS; the delta is
exactly the hatch.
"""

import pytest

from djust import LiveView
from djust.decorators import event_handler
from djust.tests.test_transport_behavioral_parity import (
    _EventSpineMixin,
    _event_runtime_with_view,
)


class _InPlaceView(_EventSpineMixin, LiveView):
    """A nested-unhashable attr (``rows`` = list of dicts holding a nested list,
    the shape that defeats ``_snapshot_assigns``' shallow fingerprint) mutated
    in place, with and without the escape hatch."""

    def mount(self, request, **kwargs):
        self.count = 0
        self.rows = [{"cards": ["a"]}, {"cards": []}]

    def get_context_data(self, **kwargs):
        return {"count": self.count, "rows": self.rows}

    @event_handler()
    def move_no_hatch(self, **kwargs):
        moved = self.rows[0]["cards"].pop(0)  # in-place nested mutation
        self.rows[1]["cards"].append(moved)  # (same list/dict objects)

    @event_handler()
    def move_with_hatch(self, **kwargs):
        moved = self.rows[0]["cards"].pop(0)  # identical in-place mutation...
        self.rows[1]["cards"].append(moved)
        self.set_changed_keys("rows")  # ...plus the escape hatch

    @event_handler()
    def move_with_raw_changed_keys(self, **kwargs):
        moved = self.rows[0]["cards"].pop(0)  # identical in-place mutation...
        self.rows[1]["cards"].append(moved)
        self._changed_keys = {"rows"}  # ...raw flag only — documented as ineffective


def _updates(transport):
    return [f for f in transport.sent if f.get("type") in ("patch", "html_update")]


def _noops(transport):
    return [f for f in transport.sent if f.get("type") == "noop"]


class TestSetChangedKeys1981:
    @pytest.mark.asyncio
    async def test_in_place_without_hatch_is_skipped(self):
        """By-design: an in-place nested mutation with no hatch auto-skips
        (``_snapshot_assigns`` can't see it) → noop, no render. This is the
        gate-off baseline for the next test."""
        runtime, transport = _event_runtime_with_view(_InPlaceView())
        runtime.view_instance.count = 0
        runtime.view_instance.rows = [{"cards": ["a"]}, {"cards": []}]

        await runtime.dispatch_event({"type": "event", "event": "move_no_hatch", "params": {}})

        assert not _updates(transport), (
            "in-place nested mutation is invisible to auto-diff; the event must "
            f"auto-skip, got {transport.sent!r}"
        )
        assert _noops(transport), f"expected a noop, got {transport.sent!r}"

    @pytest.mark.asyncio
    async def test_raw_changed_keys_alone_is_still_skipped(self):
        """Mechanism pin (#1982 review 🔴): ``_changed_keys`` is excluded from the
        assigns snapshot (``_FRAMEWORK_INTERNAL_ATTRS``), so setting it DIRECTLY
        — same in-place mutation, no ``_force_full_html`` — still auto-skips.
        This falsification-tests the docstring claim ("setting _changed_keys
        directly does NOT help") AND proves the with-hatch render below is
        attributable to ``_force_full_html``, not a snapshot leak of the flag
        itself. Gate-off: remove ``_changed_keys``/``_force_full_html`` from
        ``_FRAMEWORK_INTERNAL_ATTRS`` → the raw flag perturbs the fingerprint →
        this RENDERS instead of nooping → FAILS."""
        runtime, transport = _event_runtime_with_view(_InPlaceView())
        runtime.view_instance.count = 0
        runtime.view_instance.rows = [{"cards": ["a"]}, {"cards": []}]

        await runtime.dispatch_event(
            {"type": "event", "event": "move_with_raw_changed_keys", "params": {}}
        )

        assert not _updates(transport), (
            "raw _changed_keys assignment must NOT bypass the skip (it is "
            "snapshot-excluded; only set_changed_keys()'s _force_full_html "
            f"does), got {transport.sent!r}"
        )
        assert _noops(transport), f"expected a noop, got {transport.sent!r}"

    @pytest.mark.asyncio
    async def test_in_place_with_hatch_renders(self):
        """``set_changed_keys`` forces the re-render the auto-skip would drop —
        the same mutation as the gate-off test, plus the hatch, now renders.
        Paired with ``test_raw_changed_keys_alone_is_still_skipped``, the render
        here is isolated to the ``_force_full_html`` mechanism."""
        runtime, transport = _event_runtime_with_view(_InPlaceView())
        runtime.view_instance.count = 0
        runtime.view_instance.rows = [{"cards": ["a"]}, {"cards": []}]

        await runtime.dispatch_event({"type": "event", "event": "move_with_hatch", "params": {}})

        assert _updates(transport), (
            "set_changed_keys must force a render on the production path even "
            f"when the mutation is an undetectable in-place change, got {transport.sent!r}"
        )

    def test_set_changed_keys_is_public_and_marks_state(self):
        """The method the truncation warnings + docstring advertise actually
        exists, accepts a str or an iterable, and sets the render-forcing state."""
        v = _InPlaceView()
        assert callable(getattr(v, "set_changed_keys", None)), (
            "set_changed_keys must be a public LiveView method (the "
            "_snapshot_assigns truncation warnings advise calling it)"
        )
        v.set_changed_keys("rows")
        assert v._changed_keys == {"rows"}
        assert v._force_full_html is True

        v2 = _InPlaceView()
        v2.set_changed_keys(["a", "b"])  # iterable form
        assert v2._changed_keys == {"a", "b"}
        assert v2._force_full_html is True

    def test_set_changed_keys_unions_with_existing(self):
        """Repeated calls accumulate (don't clobber) — a handler can mark several
        keys across helper calls in one turn."""
        v = _InPlaceView()
        v.set_changed_keys("a")
        v.set_changed_keys(["b", "c"])
        assert v._changed_keys == {"a", "b", "c"}
