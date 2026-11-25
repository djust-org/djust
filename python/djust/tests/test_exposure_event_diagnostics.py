"""Foreground and deferred failures cannot grant exception exposure.

Contract (ADR-038 D-a, revised 2026-09-22): in production (``DEBUG=False``) an
explicit view's event failure is value-free in the log, the error frame and
the traceback ring. Under ``DEBUG=True`` the failure reads like Django's DEBUG
output. ``LIVEVIEW_CONFIG["log_exception_detail"]`` (#4180) opts the server log
(and only it) into the detail in production.
"""

import json
from collections import deque

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings

from djust import LiveView, event_handler
from djust.decorators import state
from djust.observability import tracebacks

from .test_exposure_runtime import make_request, mount


class UnprintableFailure(Exception):
    def __str__(self):
        raise AssertionError("Protected exception stringification must not run")


class EventFailureView(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>{{ count }}</div>"
    count = state(0, persist="server")

    def mount(self, request, **kwargs):
        self._fail_render = False
        self._called = False
        self._rendered_failure = False
        self.set_activity_visible("diagnostics", False)

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)

    @event_handler()
    def explode(self, stage: str):
        self._called = True
        self.count += 1
        if stage == "opaque":
            raise UnprintableFailure()
        if stage == "handler":
            raise ValueError("EVENT_DIAGNOSTIC_SENTINEL")
        self._fail_render = True
        self._force_full_html = True

    @event_handler()
    def reveal(self):
        self.set_activity_visible("diagnostics", True)
        self._skip_render = True

    def render_with_diff(self, *args, **kwargs):
        if self._fail_render:
            self._rendered_failure = True
            raise ValueError("EVENT_DIAGNOSTIC_SENTINEL")
        return super().render_with_diff(*args, **kwargs)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize("detail", [False, True], ids=["default", "log_exception_detail"])
@pytest.mark.parametrize("stage", ["handler", "render"])
@pytest.mark.parametrize("route", ["foreground", "deferred"])
async def test_event_failure_destinations(monkeypatch, caplog, debug, detail, stage, route):
    monkeypatch.setattr(tracebacks, "_buffer", deque(maxlen=50))
    request = await sync_to_async(make_request)()
    with override_settings(DEBUG=debug, LIVEVIEW_CONFIG={"log_exception_detail": detail}):
        runtime, transport = await mount(
            request, EventFailureView, view=__name__ + ".EventFailureView"
        )
        view = runtime.view_instance
        transport.sent.clear()
        caplog.clear()
        if route == "foreground":
            await runtime.dispatch_event(
                {"type": "event", "event": "explode", "params": {"stage": stage}, "ref": 17}
            )
        else:
            # Production redispatch borrows the authorized outer event turn.
            # Exercise that caller boundary so persistence cannot fail merely
            # because the test skipped authorization setup.
            await runtime.dispatch_event(
                {
                    "type": "event",
                    "event": "explode",
                    "params": {"stage": stage, "_activity": "diagnostics"},
                    "ref": 17,
                }
            )
            assert not view._called
            transport.sent.clear()
            await runtime.dispatch_event({"type": "event", "event": "reveal", "params": {}})
    assert view._called
    # The sentinel is raised by the handler, or by the render if it runs.
    raised = stage == "handler" or view._rendered_failure
    assert raised == (stage != "opaque"), "the sentinel site was not reached"
    # The server log carries it under DEBUG or the opt-in; nothing else does.
    assert ("EVENT_DIAGNOSTIC_SENTINEL" in caplog.text) == (debug or detail)
    has_error_frame = route != "deferred" or stage not in {"handler", "opaque"}
    if has_error_frame:
        assert any(frame.get("type") == "error" for frame in transport.sent)
    assert ("EVENT_DIAGNOSTIC_SENTINEL" in json.dumps(transport.sent)) == (
        debug and has_error_frame
    )
    assert ("EVENT_DIAGNOSTIC_SENTINEL" in json.dumps(tracebacks.get_recent_tracebacks(50))) == (
        debug and has_error_frame
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["foreground", "deferred"])
async def test_protected_handler_exception_is_not_stringified(monkeypatch, caplog, route):
    """In production a protected failure is never stringified. Under DEBUG the
    owner is allowed details (D-a, revised); that path is pinned by
    ``test_event_failure_destinations`` with a printable exception, since this
    one raises on ``str()``."""
    await test_event_failure_destinations(monkeypatch, caplog, False, False, "opaque", route)
