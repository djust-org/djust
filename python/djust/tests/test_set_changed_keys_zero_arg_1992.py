"""#1992 — zero-arg ``set_changed_keys()`` for the DB/external-only mutation case.

A handler that mutates ONLY external state (a DB row) and touches no public
``self.*`` attribute produces zero re-render — auto change-detection sees
nothing changed and auto-skips, even though ``get_context_data()`` (which
re-queries the DB) would return different HTML. ``set_changed_keys("attr")``
existed (#1981) but required naming an attr; there was no way to say "just
re-render, nothing on self changed". The zero-arg form forces the re-render
without naming a key.

Runs the REAL production event path (``ViewRuntime.dispatch_event``) — the test
client calls ``render_with_diff`` directly and bypasses the ``pre==post`` skip,
so it would FALSELY show the hatch as unnecessary (reproduction fidelity,
#1650/#1638 — same as #1981).

Non-tautology (#1468/#1200): ``test_db_only_without_hatch_is_skipped`` IS the
gate-off of ``test_db_only_with_zero_arg_renders`` — same DB-only mutation,
minus the zero-arg call. The first NOOPs, the second RENDERS; the delta is
exactly the zero-arg hatch.
"""

import pytest

from djust import LiveView
from djust.decorators import event_handler
from djust.tests.test_transport_behavioral_parity import (
    _EventSpineMixin,
    _event_runtime_with_view,
)


class _DbOnlyView(_EventSpineMixin, LiveView):
    """A DB-backed value read fresh each render (via a private, in-place-mutated
    holder — invisible to change detection, standing in for a re-queried DB
    row), with NO public ``self.*`` attr ever changing in the handler."""

    def mount(self, request, **kwargs):
        self._external = {"n": 0}  # stands in for a DB row

    def get_context_data(self, **kwargs):
        return {"n": self._external["n"]}  # re-"queried" each render

    @event_handler()
    def bump_no_hatch(self, **kwargs):
        self._external["n"] += 1  # DB-only change, no self.* — gate-off baseline

    @event_handler()
    def bump_zero_arg(self, **kwargs):
        self._external["n"] += 1  # identical DB-only change...
        self.set_changed_keys()  # ...plus the zero-arg force-render hatch


def _updates(transport):
    return [f for f in transport.sent if f.get("type") in ("patch", "html_update")]


def _noops(transport):
    return [f for f in transport.sent if f.get("type") == "noop"]


class TestZeroArgSetChangedKeys1992:
    @pytest.mark.asyncio
    async def test_db_only_without_hatch_is_skipped(self):
        """Gate-off baseline: a DB-only mutation with no hatch auto-skips
        (``_snapshot_assigns`` sees no changed public attr)."""
        runtime, transport = _event_runtime_with_view(_DbOnlyView())
        runtime.view_instance._external = {"n": 0}

        await runtime.dispatch_event({"type": "event", "event": "bump_no_hatch", "params": {}})

        assert not _updates(transport), (
            "a DB-only mutation is invisible to auto-diff; the event must "
            f"auto-skip, got {transport.sent!r}"
        )
        assert _noops(transport), f"expected a noop, got {transport.sent!r}"

    @pytest.mark.asyncio
    async def test_db_only_with_zero_arg_renders(self):
        """``set_changed_keys()`` (zero-arg) forces the re-render the auto-skip
        would drop — same DB-only mutation as the gate-off test, plus the
        zero-arg hatch, now renders."""
        runtime, transport = _event_runtime_with_view(_DbOnlyView())
        runtime.view_instance._external = {"n": 0}

        await runtime.dispatch_event({"type": "event", "event": "bump_zero_arg", "params": {}})

        assert _updates(transport), (
            "zero-arg set_changed_keys() must force a render for a DB-only "
            f"change with no changed public attr, got {transport.sent!r}"
        )

    def test_zero_arg_sets_force_flag_without_changed_keys(self):
        """Unit: the zero-arg form sets ``_force_full_html`` but adds no keys
        (there are none to add)."""
        v = _DbOnlyView()
        v.set_changed_keys()
        assert v._force_full_html is True
        assert getattr(v, "_changed_keys", None) in (None, set())

    def test_keyed_form_unchanged(self):
        """Regression: the existing keyed form still marks the named key."""
        v = _DbOnlyView()
        v.set_changed_keys("rows")
        assert v._changed_keys == {"rows"}
        assert v._force_full_html is True
