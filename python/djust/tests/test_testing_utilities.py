"""Tests for v0.5.1 LiveViewTestClient Phoenix-parity assertions.

Covers assert_push_event, assert_patch, assert_redirect, render_async,
follow_redirect, assert_stream_insert, and trigger_info.
"""

from __future__ import annotations

import pytest

from djust.decorators import event_handler
from djust.live_view import LiveView
from djust.testing import LiveViewTestClient


# ─────────────────────────────────────────────────────────────────────────────
# assert_push_event
# ─────────────────────────────────────────────────────────────────────────────


def test_assert_push_event_matches_by_name():
    class V(LiveView):
        @event_handler
        def save(self, **kwargs):
            self.push_event("flash", {"type": "success", "message": "saved"})

    c = LiveViewTestClient(V).mount()
    c.send_event("save")
    c.assert_push_event("flash")


def test_assert_push_event_matches_payload_subset():
    class V(LiveView):
        @event_handler
        def save(self, **kwargs):
            self.push_event("flash", {"type": "success", "message": "saved"})

    c = LiveViewTestClient(V).mount()
    c.send_event("save")
    # Only asserting one key — subset match tolerates the other.
    c.assert_push_event("flash", {"type": "success"})


def test_assert_push_event_fails_on_wrong_name():
    class V(LiveView):
        @event_handler
        def save(self, **kwargs):
            self.push_event("flash", {"type": "success"})

    c = LiveViewTestClient(V).mount()
    c.send_event("save")
    with pytest.raises(AssertionError, match="Expected push_event"):
        c.assert_push_event("toast")


def test_assert_push_event_fails_on_payload_mismatch():
    class V(LiveView):
        @event_handler
        def save(self, **kwargs):
            self.push_event("flash", {"type": "error"})

    c = LiveViewTestClient(V).mount()
    c.send_event("save")
    with pytest.raises(AssertionError):
        c.assert_push_event("flash", {"type": "success"})


# ─────────────────────────────────────────────────────────────────────────────
# assert_patch
# ─────────────────────────────────────────────────────────────────────────────


def test_assert_patch_matches_path():
    class V(LiveView):
        @event_handler
        def filter(self, **kwargs):
            self.live_patch(params={"page": 2}, path="/items/")

    c = LiveViewTestClient(V).mount()
    c.send_event("filter")
    c.assert_patch("/items/")


def test_assert_patch_matches_params_subset():
    class V(LiveView):
        @event_handler
        def filter(self, **kwargs):
            self.live_patch(params={"page": 2, "category": "books"}, path="/items/")

    c = LiveViewTestClient(V).mount()
    c.send_event("filter")
    c.assert_patch("/items/", {"page": 2})


def test_assert_patch_fails_when_redirect_queued_instead():
    class V(LiveView):
        @event_handler
        def go(self, **kwargs):
            self.live_redirect("/detail/")

    c = LiveViewTestClient(V).mount()
    c.send_event("go")
    with pytest.raises(AssertionError, match="Expected a live_patch"):
        c.assert_patch("/detail/")


# ─────────────────────────────────────────────────────────────────────────────
# assert_redirect
# ─────────────────────────────────────────────────────────────────────────────


def test_assert_redirect_matches_path():
    class V(LiveView):
        @event_handler
        def save_and_exit(self, **kwargs):
            self.live_redirect("/dashboard/")

    c = LiveViewTestClient(V).mount()
    c.send_event("save_and_exit")
    c.assert_redirect("/dashboard/")


def test_assert_redirect_fails_on_wrong_path():
    class V(LiveView):
        @event_handler
        def go(self, **kwargs):
            self.live_redirect("/home/")

    c = LiveViewTestClient(V).mount()
    c.send_event("go")
    with pytest.raises(AssertionError):
        c.assert_redirect("/dashboard/")


# ─────────────────────────────────────────────────────────────────────────────
# render_async
# ─────────────────────────────────────────────────────────────────────────────


def test_render_async_no_pending_tasks_is_noop():
    class V(LiveView):
        @event_handler
        def nop(self, **kwargs):
            return None

    c = LiveViewTestClient(V).mount()
    c.send_event("nop")
    c.render_async()  # must not raise


def test_render_async_runs_pending_sync_callback():
    class V(LiveView):
        value = 0

        def mount(self, request=None, **kwargs):
            self.value = 0

        def _heavy_work(self):
            self.value = 42

        @event_handler
        def kick_off(self, **kwargs):
            self.start_async(self._heavy_work, name="work")

    c = LiveViewTestClient(V).mount()
    c.send_event("kick_off")
    # Task is queued but hasn't run yet — the test client doesn't drive the WS
    # consumer's async loop.
    assert c.view_instance.value == 0
    c.render_async()
    assert c.view_instance.value == 42


def test_render_async_drains_so_recursive_tasks_are_not_auto_rerun():
    """One call to render_async runs the current batch; tasks scheduled by
    those callbacks require a second call. Matches production semantics."""
    tracker = {"runs": 0}

    class V(LiveView):
        def mount(self, request=None, **kwargs):
            pass

        def _first(self):
            tracker["runs"] += 1
            self.start_async(self._second, name="second")

        def _second(self):
            tracker["runs"] += 1

        @event_handler
        def kick_off(self, **kwargs):
            self.start_async(self._first, name="first")

    c = LiveViewTestClient(V).mount()
    c.send_event("kick_off")
    c.render_async()
    assert tracker["runs"] == 1  # _first ran; _second requeued but not run
    c.render_async()
    assert tracker["runs"] == 2


# ─────────────────────────────────────────────────────────────────────────────
# assert_stream_insert
# ─────────────────────────────────────────────────────────────────────────────


def test_assert_stream_insert_matches_by_name():
    class V(LiveView):
        def mount(self, request=None, **kwargs):
            self.stream("msgs", [])

        @event_handler
        def add(self, **kwargs):
            self.stream_insert("msgs", {"id": 1, "text": "hi"})

    c = LiveViewTestClient(V).mount()
    c.send_event("add")
    c.assert_stream_insert("msgs")


def test_assert_stream_insert_matches_dict_subset():
    class V(LiveView):
        def mount(self, request=None, **kwargs):
            self.stream("msgs", [])

        @event_handler
        def add(self, **kwargs):
            self.stream_insert("msgs", {"id": 1, "text": "hi", "ts": 123})

    c = LiveViewTestClient(V).mount()
    c.send_event("add")
    c.assert_stream_insert("msgs", {"id": 1})


def test_assert_stream_insert_fails_on_wrong_stream():
    class V(LiveView):
        def mount(self, request=None, **kwargs):
            self.stream("msgs", [])

        @event_handler
        def add(self, **kwargs):
            self.stream_insert("msgs", {"id": 1})

    c = LiveViewTestClient(V).mount()
    c.send_event("add")
    with pytest.raises(AssertionError):
        c.assert_stream_insert("notifications")


# ─────────────────────────────────────────────────────────────────────────────
# trigger_info
# ─────────────────────────────────────────────────────────────────────────────


def test_trigger_info_invokes_handle_info():
    class V(LiveView):
        notifications = 0

        def mount(self, request=None, **kwargs):
            self.notifications = 0

        def handle_info(self, message):
            if message.get("type") == "db_notify":
                self.notifications += 1

    c = LiveViewTestClient(V).mount()
    c.trigger_info({"type": "db_notify", "channel": "orders"})
    assert c.view_instance.notifications == 1


def test_trigger_info_records_event_with_duration():
    class V(LiveView):
        def mount(self, request=None, **kwargs):
            pass

        def handle_info(self, message):
            pass

    c = LiveViewTestClient(V).mount()
    result = c.trigger_info({"type": "x"})
    assert result["success"] is True
    assert "duration_ms" in result
    history = c.get_event_history()
    assert any(e.get("type") == "handle_info" for e in history)


def test_trigger_info_noop_default_is_callable():
    """Every LiveView inherits a no-op handle_info from NotificationMixin;
    trigger_info on a view that hasn't overridden it just returns success."""

    class V(LiveView):
        def mount(self, request=None, **kwargs):
            pass

    c = LiveViewTestClient(V).mount()
    result = c.trigger_info({"type": "x"})
    assert result["success"] is True


def test_trigger_info_captures_handler_exception():
    class V(LiveView):
        def mount(self, request=None, **kwargs):
            pass

        def handle_info(self, message):
            raise RuntimeError("boom")

    c = LiveViewTestClient(V).mount()
    result = c.trigger_info({"type": "x"})
    assert result["success"] is False
    assert "boom" in result["error"]


# ─────────────────────────────────────────────────────────────────────────────
# _require_mounted guards
# ─────────────────────────────────────────────────────────────────────────────


def test_assertions_require_mount():
    class V(LiveView):
        pass

    c = LiveViewTestClient(V)  # not mounted
    for method, args in [
        ("assert_push_event", ("evt",)),
        ("assert_patch", ("/x/",)),
        ("assert_redirect", ("/y/",)),
        ("render_async", ()),
        ("follow_redirect", ()),
        ("assert_stream_insert", ("s",)),
        ("trigger_info", ({"type": "x"},)),
    ]:
        with pytest.raises(RuntimeError, match="not mounted"):
            getattr(c, method)(*args)


# ─────────────────────────────────────────────────────────────────────────────
# Integration — chain assertions
# ─────────────────────────────────────────────────────────────────────────────


def test_combined_push_event_patch_and_stream():
    class V(LiveView):
        def mount(self, request=None, **kwargs):
            self.stream("items", [])

        @event_handler
        def create(self, **kwargs):
            self.stream_insert("items", {"id": 7, "name": "new"})
            self.push_event("flash", {"message": "created"})
            self.live_patch(params={"highlight": "7"}, path="/items/")

    c = LiveViewTestClient(V).mount()
    c.send_event("create")
    c.assert_stream_insert("items", {"id": 7})
    c.assert_push_event("flash", {"message": "created"})
    c.assert_patch("/items/", {"highlight": "7"})
