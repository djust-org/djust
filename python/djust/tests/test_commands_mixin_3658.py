"""#3658 (TC9): ``CommandsMixin``, the one outbound queue of a view.

``push_event``, ``put_flash`` / ``clear_flash``, ``page_title`` / ``page_meta``,
``set_layout`` and ``live_patch`` / ``live_redirect`` all queue on
``view._outbound``. A drain is sorted stably by kind rank (push_event, flash,
page_metadata, layout, navigation); within a kind the queue order is kept.
"""

from __future__ import annotations

import pytest

from djust import LiveView
from djust._surviving_work import SURVIVING_OWNER, ViewDisposedError
from djust.mixins.commands import EFFECT_RANKS, CommandsMixin
from djust.mixins.flash import FlashMixin
from djust.runtime import _discard_turn_effects


def _view() -> LiveView:
    return LiveView()


def _queue_everything(view: LiveView) -> None:
    """One of each kind, interleaved against the rank order."""
    view.live_redirect("/next/")
    view.set_layout("layouts/a.html")
    view.page_title = "Inbox (3)"
    view.put_flash("info", "Saved")
    view.push_event("first", {"n": 1})
    view.live_patch(params={"page": 2})
    view.set_layout("layouts/b.html")
    view.clear_flash("error")
    view.page_meta = {"description": "d"}
    view.push_event("second")


class TestOrder:
    def test_drain_sorts_by_rank_and_keeps_fifo_within_a_kind(self):
        view = _view()
        _queue_everything(view)
        assert view._drain_outbound() == [
            ("push_event", {"event": "first", "payload": {"n": 1}}),
            ("push_event", {"event": "second", "payload": {}}),
            ("flash", {"action": "put", "level": "info", "message": "Saved"}),
            ("flash", {"action": "clear", "level": "error"}),
            ("page_metadata", {"action": "title", "value": "Inbox (3)"}),
            ("page_metadata", {"action": "meta", "name": "description", "content": "d"}),
            ("layout", {"path": "layouts/b.html"}),
            ("navigation", {"action": "live_redirect", "path": "/next/", "replace": False}),
            ("navigation", {"action": "live_patch", "replace": False, "params": {"page": 2}}),
        ]
        assert view._drain_outbound() == []
        assert view._outbound == []

    def test_rank_table(self):
        assert sorted(EFFECT_RANKS, key=EFFECT_RANKS.__getitem__)[:5] == [
            "push_event",
            "flash",
            "page_metadata",
            "layout",
            "navigation",
        ]
        assert EFFECT_RANKS["navigate"] == EFFECT_RANKS["navigation"]

    def test_set_layout_last_wins(self):
        view = _view()
        view.set_layout("a.html")
        view.push_event("x")
        view.set_layout("b.html")
        assert view._pending("layout") == [{"path": "b.html"}]
        assert [k for _s, k, _p in view._outbound] == ["push_event", "layout"]

    def test_kind_filtered_drain_leaves_the_rest_in_order(self):
        view = _view()
        _queue_everything(view)
        assert view._drain_outbound(("flash", "push_event")) == [
            ("push_event", {"event": "first", "payload": {"n": 1}}),
            ("push_event", {"event": "second", "payload": {}}),
            ("flash", {"action": "put", "level": "info", "message": "Saved"}),
            ("flash", {"action": "clear", "level": "error"}),
        ]
        assert [k for _s, k, _p in view._outbound] == [
            "navigation",
            "page_metadata",
            "navigation",
            "layout",
            "page_metadata",
        ]
        assert view._has_pending("navigation")
        assert not view._has_pending("flash")

    def test_pending_does_not_drain(self):
        view = _view()
        view.push_event("x", {"a": 1})
        assert view._pending("push_event") == [{"event": "x", "payload": {"a": 1}}]
        assert view._has_pending()
        assert view._pending("push_event") == [{"event": "x", "payload": {"a": 1}}]

    def test_unknown_kind_refused(self):
        with pytest.raises(ValueError):
            _view()._enqueue("navigate", {"to": "/"})


class TestLifecycle:
    def test_discard_turn_effects_empties_everything(self):
        view = _view()
        _queue_everything(view)
        _discard_turn_effects(view)
        assert view._outbound == []

    def test_disposed_view_refuses_push_event(self):
        view = _view()
        view._djust_child_disposed = True
        token = SURVIVING_OWNER.set(view)
        try:
            with pytest.raises(ViewDisposedError):
                view.push_event("late")
        finally:
            SURVIVING_OWNER.reset(token)
        assert view._outbound == []

    def test_standalone_mixin_queues_without_liveview(self):
        class Plain(FlashMixin):
            pass

        plain = Plain()
        plain.put_flash("info", "hi")
        assert isinstance(plain, CommandsMixin)
        assert plain._drain_flash() == [{"action": "put", "level": "info", "message": "hi"}]

    def test_a_view_whose_init_did_not_run_still_queues(self):
        view = LiveView.__new__(LiveView)
        view.push_event("x")
        assert view._drain_push_events() == [("x", {})]


class TestBridgeDrains:
    """The per-kind drains keep their 1.x return shapes (WP-B removes them)."""

    def test_shapes(self):
        view = _view()
        _queue_everything(view)
        assert view._drain_push_events() == [("first", {"n": 1}), ("second", {})]
        assert view._drain_flash() == [
            {"action": "put", "level": "info", "message": "Saved"},
            {"action": "clear", "level": "error"},
        ]
        assert view._drain_page_metadata() == [
            {"action": "title", "value": "Inbox (3)"},
            {"action": "meta", "name": "description", "content": "d"},
        ]
        assert view._drain_pending_layout() == "layouts/b.html"
        assert view._drain_pending_layout() is None
        assert view._drain_navigation() == [
            {"type": "live_redirect", "path": "/next/", "replace": False},
            {"type": "live_patch", "replace": False, "params": {"page": 2}},
        ]
        assert view._outbound == []

    def test_clear_push_events_drops_only_push_events(self):
        view = _view()
        _queue_everything(view)
        assert view.clear_push_events() == [("first", {"n": 1}), ("second", {})]
        assert not view._has_pending("push_event")
        assert view._has_pending("flash")

    def test_legacy_attribute_views(self):
        view = _view()
        _queue_everything(view)
        assert view._pending_push_events == [("first", {"n": 1}), ("second", {})]
        assert len(view._pending_flash) == 2
        assert view._pending_layout == "layouts/b.html"
        assert view._pending_navigation[0] == {
            "type": "live_redirect",
            "path": "/next/",
            "replace": False,
        }
        view._pending_push_events = []
        view._pending_layout = None
        view._pending_navigation = None
        assert not view._has_pending("push_event")
        assert not view._has_pending("layout")
        assert not view._has_pending("navigation")
        assert view._has_pending("flash")
