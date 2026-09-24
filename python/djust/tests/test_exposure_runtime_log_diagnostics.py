"""ADR-038 E1: raw log calls inside runtime turns.

A runtime turn opens a diagnostic scope that tracks the owner's policy, but a
raw ``logger`` call does not consult ``diagnostics_allowed()`` — only
``handle_exception`` does. These sites now log through
``_exposure_diagnostics.log_failure``. Views are explicit from mount: flipping
the policy mid-session fails fresh event authorization first, which would never
reach the site under test.
"""

import json
import logging

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import override_settings

from djust import LiveView, event_handler
from djust.decorators import state
from djust.websocket import LiveViewConsumer

from .test_exposure_runtime import make_request


class LayoutFailureView(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root>{{ count }}</div>"
    count = state(0, persist="server")

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)

    @event_handler()
    def swap(self, **kwargs):
        self.set_layout("exposure_layout.html")


async def _drain(socket, quiet=0.5):
    # receive_nothing checks for a quiet socket without the timeout path of
    # receive_json_from, which cancels the application under test.
    frames = []
    while not await socket.receive_nothing(timeout=quiet):
        frames.append(await socket.receive_json_from(timeout=3))
    return frames


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize("policy", ["legacy", "explicit"])
async def test_layout_render_failure_log_is_value_free_for_explicit_views(
    monkeypatch, caplog, debug, policy
):
    """``ViewRuntime._flush_pending_layout`` renders the layout an event asked for
    with ``set_layout`` and logged a failure with ``logger.exception``. Under
    DEBUG it also re-raises; the runtime's protected catch already keeps an
    explicit view's client frame generic, which this test also pins."""
    import django.template.loader as loader

    def fail(*args, **kwargs):
        raise ValueError("LAYOUT_RENDER_SENTINEL")

    monkeypatch.setattr(loader, "render_to_string", fail)
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    monkeypatch.setattr(LayoutFailureView, "exposure_policy", policy)
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=debug, DJUST_TENANTS=None, DJUST_CONFIG={}
    ):
        request = await sync_to_async(make_request)()
        socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        socket.scope.update(session=request.session, user=request.user, tenant=None)
        assert (await socket.connect())[0]
        await socket.receive_json_from(timeout=3)
        try:
            await socket.send_json_to(
                {"type": "mount", "view": __name__ + ".LayoutFailureView", "url": "/l/"}
            )
            assert (await socket.receive_json_from(timeout=3))["type"] == "mount"
            caplog.clear()
            with caplog.at_level(logging.DEBUG):
                await socket.send_json_to({"type": "event", "event": "swap", "params": {}})
                frames = await _drain(socket)

            if policy == "legacy":
                # Control: the layout path ran and legacy logging is unchanged.
                assert "set_layout('exposure_layout.html') — template rendering raised" in (
                    caplog.text
                )
                assert "LAYOUT_RENDER_SENTINEL" in caplog.text
            else:
                assert "LAYOUT_RENDER_SENTINEL" not in caplog.text
                assert "Protected view operation failed" in caplog.text
                assert "LAYOUT_RENDER_SENTINEL" not in json.dumps(frames)
        finally:
            await socket.disconnect()


def _fail_deferred(arg):
    raise ValueError("DEFER_EXC_SENTINEL")


class DeferFailureView(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root>{{ count }}</div>"
    count = state(0, persist="server")

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)

    @event_handler()
    def later(self, **kwargs):
        import functools

        # A partial has no __qualname__, so the log's repr(callback)
        # fallback carries its bound argument — a second value channel.
        self.defer(functools.partial(_fail_deferred, "DEFER_ARG_SENTINEL"))


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["legacy", "explicit"])
async def test_deferred_callback_failure_log_is_value_free_for_explicit_views(
    monkeypatch, caplog, policy
):
    """``ViewRuntime._flush_deferred`` runs ``self.defer(...)`` callables and
    logged a failure with the exception, its traceback and ``repr(callback)``."""
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    monkeypatch.setattr(DeferFailureView, "exposure_policy", policy)
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=True, DJUST_TENANTS=None, DJUST_CONFIG={}
    ):
        request = await sync_to_async(make_request)()
        socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        socket.scope.update(session=request.session, user=request.user, tenant=None)
        assert (await socket.connect())[0]
        await socket.receive_json_from(timeout=3)
        try:
            await socket.send_json_to(
                {"type": "mount", "view": __name__ + ".DeferFailureView", "url": "/d/"}
            )
            assert (await socket.receive_json_from(timeout=3))["type"] == "mount"
            caplog.clear()
            with caplog.at_level(logging.DEBUG):
                await socket.send_json_to({"type": "event", "event": "later", "params": {}})
                await _drain(socket)

            if policy == "legacy":
                assert "Deferred callback" in caplog.text
                assert "DEFER_EXC_SENTINEL" in caplog.text
                assert "DEFER_ARG_SENTINEL" in caplog.text
            else:
                assert "DEFER_EXC_SENTINEL" not in caplog.text
                assert "DEFER_ARG_SENTINEL" not in caplog.text
                assert "Protected view operation failed" in caplog.text
        finally:
            await socket.disconnect()


PRESENCE_KEY_CALLS = []


class PresenceKeyFailureView(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root>{{ count }}</div>"
    count = state(0, persist="server")

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)

    def get_presence_key(self):
        PRESENCE_KEY_CALLS.append(True)
        raise ValueError("PRESENCE_KEY_SENTINEL")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["legacy", "explicit"])
async def test_presence_key_failure_at_mount_is_value_free_for_explicit_views(
    monkeypatch, caplog, policy
):
    """Mount wiring (``on_view_mounted``) calls the overridable
    ``get_presence_key`` and logged its failure with the exception."""
    PRESENCE_KEY_CALLS.clear()
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    monkeypatch.setattr(PresenceKeyFailureView, "exposure_policy", policy)
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=True, DJUST_TENANTS=None, DJUST_CONFIG={}
    ):
        request = await sync_to_async(make_request)()
        socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        socket.scope.update(session=request.session, user=request.user, tenant=None)
        assert (await socket.connect())[0]
        await socket.receive_json_from(timeout=3)
        try:
            caplog.clear()
            with caplog.at_level(logging.DEBUG):
                await socket.send_json_to(
                    {"type": "mount", "view": __name__ + ".PresenceKeyFailureView", "url": "/p/"}
                )
                await _drain(socket)
            assert PRESENCE_KEY_CALLS, "get_presence_key never ran; the test would be vacuous"
            if policy == "legacy":
                assert "Error setting up presence group: PRESENCE_KEY_SENTINEL" in caplog.text
            else:
                assert "PRESENCE_KEY_SENTINEL" not in caplog.text
                assert "Protected view operation failed" in caplog.text
        finally:
            await socket.disconnect()


class FullHtmlView(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root>{{ count }}</div>"
    count = state(0, persist="server")

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)

    @event_handler()
    def refresh(self, **kwargs):
        self.count += 1
        self._force_full_html = True


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["legacy", "explicit"])
async def test_full_html_signal_receiver_failure_is_value_free_for_explicit_views(
    monkeypatch, caplog, policy
):
    """``on_render_emitted`` sends the ``full_html_update`` Django signal with
    ``send``, so an application receiver's exception reaches its catch, which
    logged it with ``exc_info``."""
    from djust.signals import full_html_update

    received = []

    def receiver(sender, **kwargs):
        received.append(sender)
        raise ValueError("SIGNAL_RECEIVER_SENTINEL")

    full_html_update.connect(receiver, weak=False)
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    monkeypatch.setattr(FullHtmlView, "exposure_policy", policy)
    try:
        with override_settings(
            LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=True, DJUST_TENANTS=None, DJUST_CONFIG={}
        ):
            request = await sync_to_async(make_request)()
            socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
            socket.scope.update(session=request.session, user=request.user, tenant=None)
            assert (await socket.connect())[0]
            await socket.receive_json_from(timeout=3)
            try:
                await socket.send_json_to(
                    {"type": "mount", "view": __name__ + ".FullHtmlView", "url": "/f/"}
                )
                await _drain(socket)
                received.clear()
                caplog.clear()
                with caplog.at_level(logging.DEBUG):
                    await socket.send_json_to({"type": "event", "event": "refresh", "params": {}})
                    await _drain(socket)
                assert received, "the signal never fired; the test would be vacuous"
                if policy == "legacy":
                    assert "full-HTML-update signal emit failed" in caplog.text
                    assert "SIGNAL_RECEIVER_SENTINEL" in caplog.text
                else:
                    assert "SIGNAL_RECEIVER_SENTINEL" not in caplog.text
                    assert "Protected view operation failed" in caplog.text
            finally:
                await socket.disconnect()
    finally:
        full_html_update.disconnect(receiver)


class PersistFailureView(LiveView):
    exposure_policy = "legacy"
    # Legacy views persist after an event only with snapshot opt-in.
    enable_state_snapshot = True
    template = "<div dj-root>{{ count }}</div>"
    count = state(0, persist="server")

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["legacy", "explicit"])
async def test_post_event_state_save_failure_is_value_free_for_explicit_views(
    monkeypatch, caplog, policy
):
    """``_persist_state_after_event`` logged a failed save with
    ``logger.exception``. Explicit saves project declared ``persist="server"``
    values and "storage exceptions propagate" (``_exposure_sessions``), so a
    storage error can carry server-only data. The trigger is synthetic — the
    session store's ``aset`` raises after mount — and it is the one write both
    the legacy and the explicit save paths share."""
    from django.contrib.sessions.backends.base import SessionBase

    writes = []

    async def failing_aset(self, key, value):
        writes.append(key)
        raise ValueError("SESSION_STORE_SENTINEL")

    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    monkeypatch.setattr(PersistFailureView, "exposure_policy", policy)
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=True, DJUST_TENANTS=None, DJUST_CONFIG={}
    ):
        request = await sync_to_async(make_request)()
        socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        socket.scope.update(session=request.session, user=request.user, tenant=None)
        assert (await socket.connect())[0]
        await socket.receive_json_from(timeout=3)
        try:
            await socket.send_json_to(
                {"type": "mount", "view": __name__ + ".PersistFailureView", "url": "/s/"}
            )
            await _drain(socket)
            monkeypatch.setattr(SessionBase, "aset", failing_aset)
            caplog.clear()
            with caplog.at_level(logging.DEBUG):
                await socket.send_json_to({"type": "event", "event": "bump", "params": {}})
                await _drain(socket)
            assert writes, "the post-event save never reached the store; vacuous"
            if policy == "legacy":
                assert "Failed to save LiveView state after runtime event" in caplog.text
                assert "SESSION_STORE_SENTINEL" in caplog.text
            else:
                assert "SESSION_STORE_SENTINEL" not in caplog.text
                assert "Protected view operation failed" in caplog.text
        finally:
            await socket.disconnect()


class TimeTravelPushView(LiveView):
    exposure_policy = "legacy"
    time_travel_enabled = True
    template = "<div dj-root>{{ count }}</div>"
    count = state(0, persist="server")

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["legacy", "explicit"])
async def test_time_travel_push_failure_is_value_free_for_explicit_views(
    monkeypatch, caplog, policy
):
    """The consumer's ``_maybe_push_tt_event`` (DEBUG-only) logged a failed push
    with ``logger.exception``. Synthetic trigger: the recorded snapshot's
    ``to_dict`` raises; the test records that it was reached."""
    from djust.time_travel import EventSnapshot

    calls = []

    def failing_to_dict(self):
        calls.append(True)
        raise ValueError("TT_PUSH_SENTINEL")

    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    monkeypatch.setattr(TimeTravelPushView, "exposure_policy", policy)
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=True, DJUST_TENANTS=None, DJUST_CONFIG={}
    ):
        request = await sync_to_async(make_request)()
        socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        socket.scope.update(session=request.session, user=request.user, tenant=None)
        assert (await socket.connect())[0]
        await socket.receive_json_from(timeout=3)
        try:
            await socket.send_json_to(
                {"type": "mount", "view": __name__ + ".TimeTravelPushView", "url": "/tt/"}
            )
            await _drain(socket)
            monkeypatch.setattr(EventSnapshot, "to_dict", failing_to_dict)
            caplog.clear()
            with caplog.at_level(logging.DEBUG):
                await socket.send_json_to({"type": "event", "event": "bump", "params": {}})
                await _drain(socket)
            assert calls, "the time-travel push never ran; the test would be vacuous"
            if policy == "legacy":
                assert "time_travel: failed to push event frame" in caplog.text
                assert "TT_PUSH_SENTINEL" in caplog.text
            else:
                assert "TT_PUSH_SENTINEL" not in caplog.text
                assert "Protected view operation failed" in caplog.text
        finally:
            await socket.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize("restricted", [False, True])
async def test_scoped_component_render_failure_honours_a_restricted_turn(
    monkeypatch, caplog, restricted
):
    """Unit-level evidence for ``ViewRuntime._render_scoped_component`` (ADR-032).

    Its catch logged a failed component render with ``exc_info`` before falling
    back to the full render. An end-to-end explicit-view reproduction needs bound
    components under the explicit policy, which is ADR-038 E2 work; this drives
    the method inside a diagnostic scope restricted to a nonlegacy owner, exactly
    as a runtime turn sets it up, and checks the log honours it.
    """
    from types import SimpleNamespace

    from djust._exposure_diagnostics import diagnostic_scope, restrict_diagnostics

    from .test_component_scoped_render_2917 import OpaquePage, _mounted

    view, runtime, _transport = _mounted(OpaquePage)
    component = view.nav
    calls = []

    def failing_render(self, *args, **kwargs):
        calls.append(True)
        raise ValueError("SCOPED_RENDER_SENTINEL")

    monkeypatch.setattr(type(component), "render", failing_render)
    with caplog.at_level(logging.DEBUG):
        with diagnostic_scope():
            if restricted:
                restrict_diagnostics(SimpleNamespace(exposure_policy="explicit"))
            result = await runtime._render_scoped_component(view, component)

    assert result is None, "a failed scoped render falls back to the full render (D6)"
    assert calls, "the component render never ran; the test would be vacuous"
    if restricted:
        assert "SCOPED_RENDER_SENTINEL" not in caplog.text
        assert "Protected view operation failed" in caplog.text
    else:
        assert "Scoped render of component 'nav' failed; full render" in caplog.text
        assert "SCOPED_RENDER_SENTINEL" in caplog.text


LOADER_CALLS = []


def _failing_loader():
    LOADER_CALLS.append(True)
    raise ValueError("ASSIGN_ASYNC_SENTINEL")


class AssignAsyncView(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root>{{ count }}</div>"
    count = state(0, persist="server")
    # Declared so the runner may store the errored AsyncResult on an explicit
    # view; otherwise that setattr could fail first and skip the log line.
    data = state(None)

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)

    @event_handler()
    def load(self, **kwargs):
        self.assign_async("data", _failing_loader)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["legacy", "explicit"])
async def test_assign_async_loader_failure_is_value_free_for_explicit_views(
    monkeypatch, caplog, policy
):
    """``assign_async``'s runners log a failed loader's exception text. They run
    as background tasks, and ``_execute_async_task`` opens no diagnostic scope,
    so the log needs its own owner check."""
    import asyncio

    LOADER_CALLS.clear()
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    monkeypatch.setattr(AssignAsyncView, "exposure_policy", policy)
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=True, DJUST_TENANTS=None, DJUST_CONFIG={}
    ):
        request = await sync_to_async(make_request)()
        socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        socket.scope.update(session=request.session, user=request.user, tenant=None)
        assert (await socket.connect())[0]
        await socket.receive_json_from(timeout=3)
        try:
            await socket.send_json_to(
                {"type": "mount", "view": __name__ + ".AssignAsyncView", "url": "/aa/"}
            )
            await _drain(socket)
            caplog.clear()
            with caplog.at_level(logging.DEBUG):
                await socket.send_json_to({"type": "event", "event": "load", "params": {}})
                await _drain(socket)
                for _ in range(40):
                    if "assign_async loader for data raised" in caplog.text or (
                        LOADER_CALLS and "Protected view operation failed" in caplog.text
                    ):
                        break
                    await asyncio.sleep(0.05)
            assert LOADER_CALLS, "the loader never ran; the test would be vacuous"
            if policy == "legacy":
                assert "assign_async loader for data raised: ASSIGN_ASYNC_SENTINEL" in caplog.text
            else:
                assert "ASSIGN_ASYNC_SENTINEL" not in caplog.text
                assert "Protected view operation failed" in caplog.text
        finally:
            await socket.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["legacy", "explicit"])
async def test_sse_deferred_callback_failure_is_value_free_for_explicit_views(caplog, policy):
    """The third ``_flush_deferred`` twin, ``sse._flush_deferred_to_sse``, logged
    the exception, traceback and ``repr(callback)``. Driven directly: it is a
    module function over a view, and the view is the only owner it has."""
    import functools
    from types import SimpleNamespace

    from djust.sse import _flush_deferred_to_sse

    def fail(arg):
        raise ValueError("SSE_DEFER_EXC_SENTINEL")

    callbacks = [(functools.partial(fail, "SSE_DEFER_ARG_SENTINEL"), (), {})]
    view = SimpleNamespace(exposure_policy=policy, _drain_deferred=lambda: list(callbacks))
    with caplog.at_level(logging.DEBUG):
        await _flush_deferred_to_sse(view)
    if policy == "legacy":
        assert "SSE_DEFER_EXC_SENTINEL" in caplog.text
        assert "SSE_DEFER_ARG_SENTINEL" in caplog.text
    else:
        assert "SSE_DEFER_EXC_SENTINEL" not in caplog.text
        assert "SSE_DEFER_ARG_SENTINEL" not in caplog.text
        assert "Protected view operation failed" in caplog.text
