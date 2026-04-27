"""Unit tests for djust.time_travel (v0.6.1).

Covers:
    1  — ring buffer append / history order
    2  — ring buffer caps at max
    3  — ring buffer overflow drops oldest (FIFO)
    4  — record_event_start no-ops when time_travel_enabled=False
    5  — record_event_start no-ops when view lacks _capture_snapshot_state
    6  — record_event_end sets state_after and appends
    7  — restore_snapshot ("before") uses safe_setattr (blocks dunder)
    8  — restore_snapshot ("after") applies state_after
    9  — concurrent append thread-safety
    10 — buffer isolation between view instances
    11 — max_events <= 0 raises ValueError
    12 — EventSnapshot.to_dict has expected keys
    13 — error path: snapshot captured with error=str
    14 — history() returns list of dicts, not EventSnapshot objects
    15 — JSON-serializable-only state (non-JSON fields silently skipped)
    16 — C501 info + C502 error fire when configured
"""

from __future__ import annotations

import threading
from typing import Any, Dict

import pytest

from djust.time_travel import (
    EventSnapshot,
    TimeTravelBuffer,
    record_event_end,
    record_event_start,
    restore_snapshot,
)


class _FakeView:
    """Stand-in for a LiveView with the surface time_travel needs."""

    time_travel_enabled = True

    def __init__(self, max_events: int = 5, enabled: bool = True):
        self.time_travel_enabled = enabled
        self._time_travel_buffer = TimeTravelBuffer(max_events=max_events)
        self.count = 0
        self.name = "alice"

    def _capture_snapshot_state(self) -> Dict[str, Any]:
        # Mirror LiveView's filtering: public, JSON-safe keys only.
        import json

        out: Dict[str, Any] = {}
        for k, v in self.__dict__.items():
            if k.startswith("_"):
                continue
            if k in ("time_travel_enabled",):
                continue
            if callable(v):
                continue
            try:
                json.dumps(v)
            except (TypeError, ValueError, OverflowError):
                continue
            out[k] = v
        return out


# ---------------------------------------------------------------------------
# 1 — ring buffer append / history order
# ---------------------------------------------------------------------------


def test_append_and_history_order():
    buf = TimeTravelBuffer(max_events=3)
    for i in range(3):
        buf.append(
            EventSnapshot(
                event_name="ev%d" % i,
                params={},
                ref=None,
                ts=float(i),
                state_before={},
            )
        )
    hist = buf.history()
    assert [h["event_name"] for h in hist] == ["ev0", "ev1", "ev2"]
    assert len(buf) == 3


# ---------------------------------------------------------------------------
# 2 — ring buffer caps at max
# ---------------------------------------------------------------------------


def test_buffer_caps_at_max():
    buf = TimeTravelBuffer(max_events=2)
    for i in range(5):
        buf.append(
            EventSnapshot(
                event_name="ev%d" % i,
                params={},
                ref=None,
                ts=float(i),
                state_before={},
            )
        )
    assert len(buf) == 2


# ---------------------------------------------------------------------------
# 3 — overflow drops oldest (FIFO)
# ---------------------------------------------------------------------------


def test_overflow_drops_oldest():
    buf = TimeTravelBuffer(max_events=2)
    for i in range(4):
        buf.append(
            EventSnapshot(
                event_name="ev%d" % i,
                params={},
                ref=None,
                ts=float(i),
                state_before={},
            )
        )
    names = [h["event_name"] for h in buf.history()]
    assert names == ["ev2", "ev3"]


# ---------------------------------------------------------------------------
# 4 — record_event_start no-ops when time_travel_enabled=False
# ---------------------------------------------------------------------------


def test_record_start_noop_when_disabled():
    view = _FakeView(enabled=False)
    snap = record_event_start(view, "increment", {}, None)
    assert snap is None


# ---------------------------------------------------------------------------
# 5 — record_event_start no-ops when view lacks _capture_snapshot_state
# ---------------------------------------------------------------------------


def test_record_start_noop_when_view_lacks_capture_method():
    class Bare:
        time_travel_enabled = True
        _time_travel_buffer = TimeTravelBuffer()

    snap = record_event_start(Bare(), "foo", {}, None)
    assert snap is None


# ---------------------------------------------------------------------------
# 6 — record_event_end sets state_after and appends
# ---------------------------------------------------------------------------


def test_record_end_sets_state_after_and_appends():
    view = _FakeView()
    snap = record_event_start(view, "increment", {"n": 1}, ref=42)
    assert snap is not None
    assert snap.state_before["count"] == 0
    view.count = 7
    record_event_end(view, snap)
    assert len(view._time_travel_buffer) == 1
    stored = view._time_travel_buffer.history()[0]
    assert stored["state_before"]["count"] == 0
    assert stored["state_after"]["count"] == 7
    assert stored["ref"] == 42
    assert stored["error"] is None


# ---------------------------------------------------------------------------
# 7 — restore_snapshot ("before") uses safe_setattr (blocks dunder)
# ---------------------------------------------------------------------------


def test_restore_snapshot_blocks_dunder():
    view = _FakeView()
    snap = EventSnapshot(
        event_name="evil",
        params={},
        ref=None,
        ts=0.0,
        state_before={"__class__": object, "count": 99},
    )
    # __class__ is rejected by safe_setattr; count is accepted.
    ok = restore_snapshot(view, snap, which="before")
    assert ok is False  # at least one key failed
    assert view.count == 99  # public key applied
    assert view.__class__ is _FakeView  # dunder NOT overwritten


# ---------------------------------------------------------------------------
# 8 — restore_snapshot ("after") applies state_after
# ---------------------------------------------------------------------------


def test_restore_snapshot_after():
    view = _FakeView()
    snap = EventSnapshot(
        event_name="e",
        params={},
        ref=None,
        ts=0.0,
        state_before={"count": 1},
        state_after={"count": 99},
    )
    ok = restore_snapshot(view, snap, which="after")
    assert ok is True
    assert view.count == 99


def test_restore_snapshot_rejects_bad_which():
    view = _FakeView()
    snap = EventSnapshot(event_name="e", params={}, ref=None, ts=0.0, state_before={})
    with pytest.raises(ValueError):
        restore_snapshot(view, snap, which="sideways")


def test_restore_removes_ghost_attrs():
    """Restoring {a:1} over {a:5,b:10} must leave {a:1}, not {a:1,b:10}."""
    view = _FakeView()
    # Current live state: count + name (from _FakeView.__init__), plus
    # a ghost we want gone after restore.
    view.count = 5
    view.ghost = "should-be-gone"
    snap = EventSnapshot(
        event_name="restore_test",
        params={},
        ref=None,
        ts=0.0,
        state_before={"count": 1, "name": "alice"},
    )
    ok = restore_snapshot(view, snap, which="before")
    assert ok is True
    assert view.count == 1
    assert not hasattr(view, "ghost")


def test_restore_preserves_private_and_framework_attrs():
    """Ghost-attr cleanup must not touch _private or framework attrs."""
    view = _FakeView()
    view._private_thing = "keep me"
    # Simulate a framework-internal public attribute (rare but possible).
    view.template_name = "foo.html"
    snap = EventSnapshot(
        event_name="restore_test",
        params={},
        ref=None,
        ts=0.0,
        state_before={"count": 0, "name": "alice"},
    )
    restore_snapshot(view, snap, which="before")
    # Private key never removed.
    assert view._private_thing == "keep me"
    # Framework-internal attr (if listed in _FRAMEWORK_INTERNAL_ATTRS)
    # must survive even though it's not in the snapshot.
    assert view.template_name == "foo.html"


# ---------------------------------------------------------------------------
# 9 — concurrent append thread-safety
# ---------------------------------------------------------------------------


def test_concurrent_append_thread_safe():
    buf = TimeTravelBuffer(max_events=1000)
    N_THREADS = 8
    PER_THREAD = 50

    def worker(tid: int):
        for i in range(PER_THREAD):
            buf.append(
                EventSnapshot(
                    event_name="t%d-%d" % (tid, i),
                    params={},
                    ref=None,
                    ts=0.0,
                    state_before={},
                )
            )

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(N_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(buf) == N_THREADS * PER_THREAD


# ---------------------------------------------------------------------------
# 10 — buffer isolation between view instances
# ---------------------------------------------------------------------------


def test_buffers_isolated_between_instances():
    v1 = _FakeView()
    v2 = _FakeView()
    snap1 = record_event_start(v1, "a", {}, None)
    record_event_end(v1, snap1)
    # v2's buffer stays empty.
    assert len(v1._time_travel_buffer) == 1
    assert len(v2._time_travel_buffer) == 0
    assert v1._time_travel_buffer is not v2._time_travel_buffer


# ---------------------------------------------------------------------------
# 11 — max_events <= 0 raises
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [0, -1, -100])
def test_max_events_non_positive_raises(bad):
    with pytest.raises(ValueError):
        TimeTravelBuffer(max_events=bad)


def test_max_events_non_int_raises():
    with pytest.raises(ValueError):
        TimeTravelBuffer(max_events=1.5)


# ---------------------------------------------------------------------------
# 12 — EventSnapshot.to_dict has expected keys
# ---------------------------------------------------------------------------


def test_snapshot_to_dict_keys():
    snap = EventSnapshot(
        event_name="e",
        params={"a": 1},
        ref=3,
        ts=1.0,
        state_before={"x": 1},
        state_after={"x": 2},
        error="boom",
    )
    d = snap.to_dict()
    assert set(d.keys()) == {
        "event_name",
        "params",
        "ref",
        "ts",
        "state_before",
        "state_after",
        "error",
    }
    assert d["error"] == "boom"


# ---------------------------------------------------------------------------
# 13 — error path: snapshot captured with error=str
# ---------------------------------------------------------------------------


def test_error_path_records_truncated_message():
    view = _FakeView()
    snap = record_event_start(view, "fail", {}, None)
    view.count = 42
    long_err = "x" * 500
    record_event_end(view, snap, error=long_err)
    stored = view._time_travel_buffer.history()[0]
    # record_event_end defense-in-depth truncates to ERROR_MESSAGE_MAX_CHARS (200).
    from djust.time_travel import ERROR_MESSAGE_MAX_CHARS

    assert stored["error"] == "x" * ERROR_MESSAGE_MAX_CHARS
    assert len(stored["error"]) == ERROR_MESSAGE_MAX_CHARS
    assert stored["state_after"]["count"] == 42


def test_error_coercion_from_non_string():
    """record_event_end coerces non-string error values via str()."""
    view = _FakeView()
    snap = record_event_start(view, "fail", {}, None)

    class _Boom(Exception):
        def __str__(self):
            return "boom-str"

    record_event_end(view, snap, error=_Boom())
    stored = view._time_travel_buffer.history()[0]
    assert stored["error"] == "boom-str"


# ---------------------------------------------------------------------------
# 14 — history() returns list of dicts, not EventSnapshot objects
# ---------------------------------------------------------------------------


def test_history_returns_dicts():
    view = _FakeView()
    snap = record_event_start(view, "e", {}, None)
    record_event_end(view, snap)
    hist = view._time_travel_buffer.history()
    assert isinstance(hist, list)
    assert isinstance(hist[0], dict)
    assert not isinstance(hist[0], EventSnapshot)


# ---------------------------------------------------------------------------
# 15 — JSON-serializable-only state (non-JSON fields silently skipped)
# ---------------------------------------------------------------------------


def test_json_serializable_only():
    view = _FakeView()

    class NotJson:
        pass

    view.bad = NotJson()  # Non-serializable — filtered by _capture_snapshot_state
    view.good = [1, 2, 3]
    snap = record_event_start(view, "e", {}, None)
    assert snap is not None
    assert "good" in snap.state_before
    assert "bad" not in snap.state_before


# ---------------------------------------------------------------------------
# 16 — C501 + C502 checks fire when configured
# ---------------------------------------------------------------------------


def test_c502_fires_when_max_events_non_positive():
    from django.test import override_settings

    from djust.checks import check_time_travel_debugging
    from djust.config import config

    saved = config.get("time_travel_max_events", 100)
    try:
        config.set("time_travel_max_events", 0)
        with override_settings(DEBUG=True):
            results = check_time_travel_debugging(app_configs=None)
        ids = [r.id for r in results]
        assert "djust.C502" in ids
    finally:
        config.set("time_travel_max_events", saved)


def test_c501_fires_when_globally_enabled():
    from django.test import override_settings

    from djust.checks import check_time_travel_debugging
    from djust.config import config

    saved_e = config.get("time_travel_enabled", False)
    saved_m = config.get("time_travel_max_events", 100)
    try:
        config.set("time_travel_enabled", True)
        config.set("time_travel_max_events", 100)
        with override_settings(DEBUG=True):
            results = check_time_travel_debugging(app_configs=None)
        ids = [r.id for r in results]
        assert "djust.C501" in ids
        assert "djust.C502" not in ids
    finally:
        config.set("time_travel_enabled", saved_e)
        config.set("time_travel_max_events", saved_m)


def test_checks_silent_when_debug_false():
    from django.test import override_settings

    from djust.checks import check_time_travel_debugging
    from djust.config import config

    saved_e = config.get("time_travel_enabled", False)
    saved_m = config.get("time_travel_max_events", 100)
    try:
        config.set("time_travel_enabled", True)
        config.set("time_travel_max_events", 0)  # would fire C502 if DEBUG=True
        with override_settings(DEBUG=False):
            results = check_time_travel_debugging(app_configs=None)
        assert results == []
    finally:
        config.set("time_travel_enabled", saved_e)
        config.set("time_travel_max_events", saved_m)


# ---------------------------------------------------------------------------
# Fix B regression — LiveView._capture_snapshot_state must deep-copy mutable
# values so in-place mutations after the snapshot (e.g. self.items.append(...))
# do not retroactively rewrite state_before / state_after on prior snapshots.
# The same fix protects v0.6.0 state-snapshot users.
# ---------------------------------------------------------------------------


def test_snapshot_breaks_reference_aliasing_on_lists():
    """state_before/state_after must not alias mutable view attrs."""
    from djust.live_view import LiveView

    class V(LiveView):
        time_travel_enabled = True
        template = "<div dj-root>items</div>"

        def mount(self, request, **kwargs):
            self.items = ["a", "b"]

        def get_context_data(self, **kwargs):
            return {"items": self.items}

    view = V()
    view.mount(None)
    # Install a buffer so record_event_end actually appends.
    view._time_travel_buffer = TimeTravelBuffer(max_events=10)
    snap = record_event_start(view, "test", {}, None)
    assert snap is not None
    # Mutate AFTER start, before end — old (aliased) impl would mutate
    # state_before["items"] in place.
    view.items.append("c")
    record_event_end(view, snap)

    stored = view._time_travel_buffer.history()[0]
    assert stored["state_before"]["items"] == [
        "a",
        "b",
    ], f"state_before was aliased: {stored['state_before']['items']!r}"
    assert stored["state_after"]["items"] == ["a", "b", "c"]


def test_snapshot_breaks_reference_aliasing_on_dicts():
    from djust.live_view import LiveView

    class V(LiveView):
        time_travel_enabled = True
        template = "<div dj-root>metrics</div>"

        def mount(self, request, **kwargs):
            self.metrics = {"count": 0}

        def get_context_data(self, **kwargs):
            return {"metrics": self.metrics}

    view = V()
    view.mount(None)
    view._time_travel_buffer = TimeTravelBuffer(max_events=10)
    snap = record_event_start(view, "t", {}, None)
    assert snap is not None
    view.metrics["count"] = 999
    record_event_end(view, snap)

    stored = view._time_travel_buffer.history()[0]
    assert stored["state_before"]["metrics"] == {"count": 0}
    assert stored["state_after"]["metrics"] == {"count": 999}


def test_capture_snapshot_state_deep_copies_nested_list():
    """Direct test of LiveView._capture_snapshot_state deep-copy semantics.

    Exercises the v0.6.0 state-snapshot path too: the same method backs
    both time-travel and the state-snapshot roundtrip.
    """
    from djust.live_view import LiveView

    class V(LiveView):
        template = "<div dj-root>x</div>"

        def mount(self, request, **kwargs):
            self.nested = [{"x": 1}, {"x": 2}]

        def get_context_data(self, **kwargs):
            return {"nested": self.nested}

    view = V()
    view.mount(None)
    captured = view._capture_snapshot_state()
    # Mutate the live attr in place — captured copy must not change.
    view.nested.append({"x": 3})
    view.nested[0]["x"] = 99
    assert captured["nested"] == [{"x": 1}, {"x": 2}]


# ===========================================================================
# v0.9.0 #1041: component-level time-travel
# ===========================================================================


class _FakeComponent:
    """Stand-in for a LiveComponent — same surface time_travel walks."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class _ParentWithComponents:
    """LiveView-like parent that registers components in ``self._components``,
    same registry layout the production ``_assign_component_ids`` populates.
    """

    time_travel_enabled = True

    def __init__(self):
        self.title = "Parent"
        self.count = 0
        # Two components — Phase-2 multi-component scenario.
        self._components = {
            "alpha": _FakeComponent(active="q1", multiple=False),
            "beta": _FakeComponent(value=10, label="ten"),
        }
        self._time_travel_buffer = TimeTravelBuffer(max_events=20)

    def _capture_snapshot_state(self):
        # Borrow LiveView's real implementation. The parent helper
        # calls ``self._capture_components_snapshot()`` so we also need
        # to expose that on this stub.
        from djust.live_view import LiveView

        return LiveView._capture_snapshot_state(self)

    def _capture_components_snapshot(self):
        from djust.live_view import LiveView

        return LiveView._capture_components_snapshot(self)


class TestComponentLevelTimeTravel:
    """v0.9.0 #1041 — component-level time-travel."""

    def test_capture_includes_components_under_reserved_key(self):
        """``_capture_snapshot_state`` adds a ``__components__`` key when
        ``self._components`` is non-empty."""
        view = _ParentWithComponents()
        snap = view._capture_snapshot_state()
        assert "__components__" in snap
        assert set(snap["__components__"].keys()) == {"alpha", "beta"}
        assert snap["__components__"]["alpha"] == {"active": "q1", "multiple": False}
        assert snap["__components__"]["beta"] == {"value": 10, "label": "ten"}
        # Parent state also captured.
        assert snap["title"] == "Parent"
        assert snap["count"] == 0

    def test_capture_omits_components_key_when_registry_empty(self):
        """A view without components produces a snapshot WITHOUT the
        ``__components__`` key (no namespace pollution)."""
        view = _ParentWithComponents()
        view._components = {}
        snap = view._capture_snapshot_state()
        assert "__components__" not in snap

    def test_capture_skips_private_and_callable_component_attrs(self):
        """Component ``_private`` attrs and methods are not captured."""

        class _ComponentWithPrivates:
            def __init__(self):
                self.public = "yes"
                self._private = "no"
                self.method = lambda: "no"

        view = _ParentWithComponents()
        view._components = {"x": _ComponentWithPrivates()}
        snap = view._capture_snapshot_state()
        assert snap["__components__"]["x"] == {"public": "yes"}

    def test_restore_dispatches_per_component_state(self):
        """``restore_snapshot`` applies each component's state to the
        matching component in ``view._components``."""
        view = _ParentWithComponents()
        before = view._capture_snapshot_state()

        # Mutate parent and component state.
        view.count = 5
        view._components["alpha"].active = "q2"
        view._components["beta"].value = 999

        # Build a snapshot wrapping the captured "before" state.
        snap = EventSnapshot(
            event_name="mutate",
            params={},
            ref=1,
            ts=0.0,
            state_before=before,
            state_after=view._capture_snapshot_state(),
        )

        ok = restore_snapshot(view, snap, which="before")
        assert ok is True
        # Parent state restored.
        assert view.count == 0
        # Components restored.
        assert view._components["alpha"].active == "q1"
        assert view._components["alpha"].multiple is False
        assert view._components["beta"].value == 10

    def test_restore_unknown_component_id_logs_and_returns_false(self):
        """Snapshot references a component not in current registry —
        log a warning and return ``False``, but other components still
        restore."""
        view = _ParentWithComponents()
        before = view._capture_snapshot_state()
        # Mutate so we have something to restore.
        view._components["alpha"].active = "q3"
        # Inject a phantom component id into the snapshot.
        before["__components__"]["ghost"] = {"foo": "bar"}

        snap = EventSnapshot(
            event_name="evt",
            params={},
            ref=1,
            ts=0.0,
            state_before=before,
            state_after={},
        )
        ok = restore_snapshot(view, snap, which="before")
        # ghost id not in registry → False; alpha still restored.
        assert ok is False
        assert view._components["alpha"].active == "q1"

    def test_restore_does_not_remove_components_absent_from_snapshot(self):
        """Components in current registry but absent from the snapshot
        keep their current state — components are first-class instances,
        not parent-scoped attrs, so the ghost-attr cleanup model
        (used for parent attrs) doesn't apply here."""
        view = _ParentWithComponents()
        before = view._capture_snapshot_state()
        # Add a NEW component after the snapshot.
        view._components["gamma"] = _FakeComponent(extra="present")

        snap = EventSnapshot(
            event_name="evt",
            params={},
            ref=1,
            ts=0.0,
            state_before=before,
            state_after={},
        )
        ok = restore_snapshot(view, snap, which="before")
        assert ok is True
        # gamma survives even though it's not in the snapshot.
        assert "gamma" in view._components
        assert view._components["gamma"].extra == "present"

    def test_state_before_and_after_disconnect_from_live_components(self):
        """Mirrors the parent-state aliasing fix: snapshots must NOT
        share refs with live component attrs. Mutating
        ``view._components[id].mutable`` after capture must not change
        the snapshot."""

        class _MutableComponent:
            def __init__(self):
                self.items = [1, 2, 3]

        view = _ParentWithComponents()
        view._components = {"m": _MutableComponent()}
        snap = view._capture_snapshot_state()
        # Mutate the live component state.
        view._components["m"].items.append(99)
        # Snapshot is untouched.
        assert snap["__components__"]["m"]["items"] == [1, 2, 3]
