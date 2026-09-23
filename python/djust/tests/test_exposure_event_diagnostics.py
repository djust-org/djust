"""Foreground and deferred failures cannot grant exception exposure.

Contract (ADR-038 D-a, revised 2026-09-22): in production (``DEBUG=False``) a
nonlegacy owner's event failure is value-free in the log, the error frame and
the traceback ring, and a policy change during the turn cannot grant details.
Under ``DEBUG=True`` every owner's failure reads like Django's DEBUG output,
exactly as a legacy owner's does.
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
        self._change_policy_in_render = False
        self._final_policy = self.exposure_policy

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)

    @event_handler()
    def explode(self, stage: str):
        self._called = True
        self.count += 1
        self._change_policy_in_render = stage == "render_transition"
        if not self._change_policy_in_render:
            self.exposure_policy = self._final_policy
        if stage == "opaque":
            raise UnprintableFailure()
        if stage == "handler":
            raise ValueError("EVENT_DIAGNOSTIC_SENTINEL")
        self._fail_render = True
        self._force_full_html = True

    def render_with_diff(self, *args, **kwargs):
        if self._fail_render:
            self._rendered_failure = True
            if self._change_policy_in_render:
                self.exposure_policy = self._final_policy
            raise ValueError("EVENT_DIAGNOSTIC_SENTINEL")
        return super().render_with_diff(*args, **kwargs)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize("stage", ["handler", "render"])
@pytest.mark.parametrize("route", ["foreground", "deferred"])
@pytest.mark.parametrize(
    "initial,final",
    [
        ("explicit", "explicit"),
        ("explicit", "legacy"),
        ("legacy", "explicit"),
        ("legacy", "legacy"),
    ],
)
async def test_event_failure_destinations(monkeypatch, caplog, debug, stage, route, initial, final):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    monkeypatch.setattr(EventFailureView, "exposure_policy", initial)
    monkeypatch.setattr(tracebacks, "_buffer", deque(maxlen=50))
    request = await sync_to_async(make_request)()
    with override_settings(DEBUG=debug):
        runtime, transport = await mount(
            request, EventFailureView, view=__name__ + ".EventFailureView"
        )
        view = runtime.view_instance
        view._final_policy = final
        transport.sent.clear()
        caplog.clear()
        if route == "foreground":
            await runtime.dispatch_event(
                {"type": "event", "event": "explode", "params": {"stage": stage}, "ref": 17}
            )
        elif route == "deferred":
            await runtime._dispatch_single_event(view, "explode", {"stage": stage}, event_ref=17)
        else:
            assert route == "direct_render"
            view._fail_render = True
            view._change_policy_in_render = True
            await runtime._render_and_send(event_name="explode", force_html=True, event_ref=17)
    assert view._rendered_failure if route == "direct_render" else view._called
    legacy = initial == final == "legacy"
    # The sentinel is raised by the handler, or by the render if it runs. A
    # legacy->explicit switch in the handler fails the explicit state save
    # (fresh authorization) before the render, so that failure never exists.
    raised = stage == "handler" or view._rendered_failure
    save_first = (stage, route, initial, final) == ("render", "foreground", "legacy", "explicit")
    assert raised == (stage != "opaque" and not save_first), "the sentinel site was not reached"
    # Details are allowed for a legacy owner, and for every owner under DEBUG.
    allowed = (legacy or debug) and raised
    assert ("EVENT_DIAGNOSTIC_SENTINEL" in caplog.text) == allowed
    has_error_frame = route != "deferred" or stage not in {"handler", "opaque"}
    if has_error_frame:
        assert any(frame.get("type") == "error" for frame in transport.sent)
    assert ("EVENT_DIAGNOSTIC_SENTINEL" in json.dumps(transport.sent)) == (
        allowed and debug and has_error_frame
    )
    assert ("EVENT_DIAGNOSTIC_SENTINEL" in json.dumps(tracebacks.get_recent_tracebacks(50))) == (
        allowed and has_error_frame
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["foreground", "deferred"])
@pytest.mark.parametrize(
    "initial,final", [("explicit", "explicit"), ("explicit", "legacy"), ("legacy", "explicit")]
)
async def test_protected_handler_exception_is_not_stringified(
    monkeypatch, caplog, route, initial, final
):
    """In production a protected failure is never stringified. Under DEBUG the
    owner is allowed details (D-a, revised), so the exception is formatted like
    a legacy owner's; that path is pinned by ``test_event_failure_destinations``
    with a printable exception, since this one raises on ``str()``."""
    debug = False
    await test_event_failure_destinations(
        monkeypatch, caplog, debug, "opaque", route, initial, final
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize("route", ["foreground", "deferred", "direct_render"])
@pytest.mark.parametrize(
    "initial,final",
    [
        ("explicit", "explicit"),
        ("explicit", "legacy"),
        ("legacy", "explicit"),
        ("legacy", "legacy"),
    ],
)
async def test_policy_change_inside_render_restricts_diagnostics(
    monkeypatch, caplog, debug, route, initial, final
):
    await test_event_failure_destinations(
        monkeypatch, caplog, debug, "render_transition", route, initial, final
    )
