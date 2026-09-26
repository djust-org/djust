"""Explicit embedded events through the real shared runtime and DB sessions."""

import json

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings

from djust import LiveView, event_handler
from djust.decorators import state
from djust.runtime import ViewRuntime
from djust.tests.test_exposure_runtime import make_request
from djust.tests.test_runtime_state_save_tt_1894 import MockTransport

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]


class EventChild(LiveView):
    exposure_policy = "explicit"
    sticky = True
    sticky_id = "menu"
    count = state(1, persist="server")
    secret = state("SERVER_SENTINEL", persist="server")
    template = "<div>Count={{ count }}</div>"

    def mount(self, request, **kwargs):
        self.count = 1
        self._service = object()
        self._handler_calls = 0

    def check_permissions(self, request):
        return request.child_allowed

    @event_handler()
    def increment(self, mode: str = "ok"):
        self._handler_calls += 1
        assert self.request.marker == "fresh"
        assert self._service is not None
        if mode == "error":
            raise RuntimeError("HANDLER_SECRET_SENTINEL")
        self.count += 1
        if mode == "scope":
            self._explicit_child_mount_inputs = '{"object_id": 99}'
        elif mode == "permission":
            self.request.child_allowed = False
        elif mode == "object":
            self.count = 99

    def get_object(self):
        return self.count

    def has_object_permission(self, request, obj):
        return obj < 10

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)


class EventParent(LiveView):
    exposure_policy = "explicit"
    template = (
        "<div dj-root>{% load live_tags %}{% live_render "
        '"djust.tests.test_exposure_child_events.EventChild" sticky=True object_id=1 %}</div>'
    )

    @event_handler()
    def change_child(self, skip: bool = False):
        self._get_child_view("menu").count = 4
        if skip:
            self._skip_render = True


class TransientEventChild(EventChild):
    count = state(1)
    secret = state("TRANSIENT_SENTINEL")


class ConditionalParent(EventParent):
    show_child = state(True, persist="server")
    template = (
        "<div dj-root>{% load live_tags %}{% if show_child %}{% live_render "
        '"djust.tests.test_exposure_child_events.EventChild" sticky=True object_id=1 %}'
        "{% endif %}</div>"
    )

    def get_context_data(self, **kwargs):
        return super().get_context_data(show_child=self.show_child, **kwargs)

    @event_handler()
    def hide_child(self):
        self.show_child = False


@pytest.fixture(autouse=True)
def staged(monkeypatch, settings):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    settings.DJUST_LIVE_RENDER_ALLOWED_MODULES = ["djust.tests.test_exposure_child_events"]


async def mount(session_key=None, *, allowed=True, view_class=EventParent):
    request = await sync_to_async(make_request)(session_key)
    request.marker = "mount"
    request.child_allowed = True
    transport = MockTransport()
    transport.build_request = lambda: request

    async def fresh(view):
        current = await sync_to_async(make_request)(request.session.session_key)
        current.marker = "fresh"
        current.child_allowed = allowed
        return current

    transport.explicit_event_request = fresh
    runtime = ViewRuntime(transport)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=["djust"]):
        await runtime.dispatch_mount(
            {
                "type": "mount",
                "view": view_class.__module__ + "." + view_class.__name__,
                "url": request.path,
            }
        )
    assert not transport.errors, transport.errors
    assert runtime.view_instance is not None
    return runtime, transport, request


async def increment(runtime, mode="ok"):
    await runtime.dispatch_event(
        {"type": "event", "event": "increment", "params": {"view_id": "menu", "mode": mode}}
    )


async def test_child_event_persists_with_fresh_request_without_snapshot_optin():
    runtime, transport, request = await mount()
    child = runtime.view_instance._get_child_view("menu")
    assert not child.enable_state_snapshot
    await increment(runtime)
    assert child.count == 2
    assert not transport.errors, transport.errors
    assert any(frame.get("type") == "embedded_update" for frame in transport.sent)
    assert "SERVER_SENTINEL" not in json.dumps(transport.sent)
    restored, _, _ = await mount(request.session.session_key)
    assert restored.view_instance._get_child_view("menu").count == 2


@pytest.mark.parametrize("skip", [False, True])
async def test_parent_event_persists_child_mutation(skip):
    runtime, transport, request = await mount()
    transport.sent.clear()
    await runtime.dispatch_event(
        {"type": "event", "event": "change_child", "params": {"skip": skip}}
    )
    assert runtime.view_instance._get_child_view("menu").count == 4
    assert not transport.errors, transport.errors
    assert any(frame.get("type") == "noop" for frame in transport.sent) is skip
    if not skip:
        assert "Count=4" in json.dumps(transport.sent)
    restored, _, _ = await mount(request.session.session_key)
    assert restored.view_instance._get_child_view("menu").count == 4


async def test_parent_save_failure_forces_full_html_on_next_success(monkeypatch):
    from django.contrib.sessions.backends.db import SessionStore

    runtime, transport, _ = await mount()
    transport.sent.clear()
    original = SessionStore.save

    # The explicit save runs in one Django-thread hop (#3200), so storage
    # failures come from the sync ``save``.
    def failed_save(self, *args, **kwargs):
        raise OSError("STORAGE_SECRET_SENTINEL")

    monkeypatch.setattr(SessionStore, "save", failed_save)
    event = {"type": "event", "event": "change_child", "params": {}}
    await runtime.dispatch_event(event)
    assert transport.errors
    assert runtime.view_instance._force_full_html
    assert not any(frame.get("type") in ("patch", "html_update") for frame in transport.sent)
    monkeypatch.setattr(SessionStore, "save", original)
    transport.sent.clear()
    await runtime.dispatch_event(event)
    assert any(frame.get("type") == "html_update" for frame in transport.sent)
    assert "Count=4" in json.dumps(transport.sent)
    assert not runtime.view_instance._force_full_html


async def test_parent_render_removes_omitted_child_and_its_stored_state():
    from django.contrib.sessions.backends.db import SessionStore

    runtime, transport, request = await mount(view_class=ConditionalParent)
    child = runtime.view_instance._get_child_view("menu")
    await runtime.dispatch_event({"type": "event", "event": "hide_child", "params": {}})
    assert not transport.errors, transport.errors
    assert runtime.view_instance._get_child_view("menu") is None
    assert child._djust_child_disposed
    stored = await sync_to_async(SessionStore(request.session.session_key).load)()
    assert not any(key.startswith("_djust_explicit_child_") for key in stored)
    await increment(runtime)
    assert child._handler_calls == 0


async def test_failed_render_override_does_not_commit_base_render_pruning(monkeypatch):
    from djust._exposure import ExposureError

    runtime, transport, _ = await mount(view_class=ConditionalParent)
    child = runtime.view_instance._get_child_view("menu")
    original = ConditionalParent.render_with_diff

    def failed_render(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise ExposureError("render override failed")

    monkeypatch.setattr(ConditionalParent, "render_with_diff", failed_render)
    await runtime.dispatch_event({"type": "event", "event": "hide_child", "params": {}})
    assert any(frame.get("type") == "error" for frame in transport.sent)
    assert runtime.view_instance._get_child_view("menu") is child
    assert not getattr(child, "_djust_child_disposed", False)


@pytest.mark.parametrize("failure", ["auth", "object", "storage", "timeout"])
async def test_parent_child_save_failure_has_no_success_ack(failure, monkeypatch, caplog):
    import asyncio

    from django.contrib.sessions.backends.db import SessionStore

    import threading

    from djust import runtime as runtime_module

    runtime, transport, request = await mount()
    transport.sent.clear()
    release = threading.Event()
    if failure == "auth":
        monkeypatch.setattr(EventChild, "check_permissions", lambda self, request: False)
    elif failure == "object":
        monkeypatch.setattr(EventChild, "has_object_permission", lambda self, request, obj: False)
    else:
        original = SessionStore.save
        if failure == "timeout":
            # A deterministic slow store: the save blocks the Django thread
            # until the test releases it, so the deadline always passes first.
            monkeypatch.setattr(runtime_module, "EVENT_STATE_SAVE_TIMEOUT_S", 0.05)

        def failed_save(self, *args, **kwargs):
            if failure == "timeout":
                release.wait(timeout=30)
            raise OSError("STORAGE_SECRET_SENTINEL")

        monkeypatch.setattr(SessionStore, "save", failed_save)
    dispatch = asyncio.ensure_future(
        runtime.dispatch_event({"type": "event", "event": "change_child", "params": {"skip": True}})
    )
    try:
        for _ in range(1000):
            if transport.errors or dispatch.done():
                break
            await asyncio.sleep(0.01)
    finally:
        release.set()
    await asyncio.wait_for(dispatch, timeout=10)
    # The abandoned save still runs; let the Django thread finish it (FIFO).
    await sync_to_async(lambda: None)()
    if runtime._explicit_catch_up is not None:
        # The catch-up turn retries once storage answers; storage still fails
        # here, so it ends in the terminal error instead of a success frame.
        await asyncio.wait_for(runtime._explicit_catch_up, timeout=10)
    assert transport.errors
    # A timed-out save is transient: withheld, but no "reload" (#3200).
    assert bool(transport.errors[0].get("transient")) is (failure == "timeout")
    assert not any(error.get("transient") for error in transport.errors[1:])
    assert not any(
        frame.get("type") in ("noop", "patch", "html_update") for frame in transport.sent
    )
    assert "SECRET_SENTINEL" not in json.dumps(transport.sent)
    assert "SECRET_SENTINEL" not in caplog.text
    if failure in ("storage", "timeout"):
        current_request = runtime.view_instance._get_child_view("menu").request
        assert all(
            value["state"]["values"]["count"] == 1
            for key, value in current_request.session.items()
            if key.startswith("_djust_explicit_child_")
        )
    if failure in ("storage", "timeout"):
        monkeypatch.setattr(SessionStore, "save", original)
    monkeypatch.setattr(EventChild, "check_permissions", lambda self, request: True)
    monkeypatch.setattr(EventChild, "has_object_permission", lambda self, request, obj: True)
    restored, _, _ = await mount(request.session.session_key)
    assert restored.view_instance._get_child_view("menu").count == 1


@pytest.mark.parametrize("changed", [False, True])
async def test_transient_child_event_checks_identity_without_server_grants(monkeypatch, changed):
    monkeypatch.setattr(
        EventParent, "template", EventParent.template.replace("EventChild", "TransientEventChild")
    )
    runtime, transport, _ = await mount()
    child = runtime.view_instance._get_child_view("menu")
    assert not hasattr(child, "_explicit_child_mount_binding")
    if changed:
        child._explicit_child_mount_inputs = '{"object_id": 99}'
    await increment(runtime)
    assert child._handler_calls == (0 if changed else 1)
    assert child.count == (1 if changed else 2)
    assert any(frame.get("type") == "embedded_update" for frame in transport.sent) is not changed


@pytest.mark.parametrize("mutation", ["permission", "inputs", "schema", "registration"])
async def test_child_event_rejects_stale_scope_before_handler(mutation, monkeypatch):
    runtime, transport, request = await mount(allowed=mutation != "permission")
    child = runtime.view_instance._get_child_view("menu")
    if mutation == "inputs":
        child._explicit_child_mount_inputs = '{"object_id": 99}'
    elif mutation == "schema":
        child._explicit_child_schema = "stale"
    elif mutation == "registration":
        child._parent_view = EventParent()
    await increment(runtime)
    assert child.count == 1
    assert child._handler_calls == 0
    assert transport.errors or any(frame.get("type") == "error" for frame in transport.sent)
    assert not any(frame.get("type") == "embedded_update" for frame in transport.sent)
    restored, _, _ = await mount(request.session.session_key)
    assert restored.view_instance._get_child_view("menu").count == 1


@pytest.mark.parametrize("mode", ["error", "scope", "permission", "object", "storage"])
async def test_post_handler_failure_does_not_save_or_send_success(mode, monkeypatch, caplog):
    from django.contrib.sessions.backends.db import SessionStore

    runtime, transport, request = await mount()
    if mode == "storage":
        original = SessionStore.save

        def unavailable(self, *args, **kwargs):
            raise OSError("STORAGE_SECRET_SENTINEL")

        monkeypatch.setattr(SessionStore, "save", unavailable)
    await increment(runtime, mode)
    assert runtime.view_instance._get_child_view("menu")._handler_calls == 1
    assert transport.errors
    assert not any(frame.get("type") == "embedded_update" for frame in transport.sent)
    assert "SECRET_SENTINEL" not in json.dumps(transport.sent)
    assert "SECRET_SENTINEL" not in caplog.text
    if mode == "storage":
        monkeypatch.setattr(SessionStore, "save", original)
    restored, _, _ = await mount(request.session.session_key)
    assert restored.view_instance._get_child_view("menu").count == 1


async def test_render_failure_never_discloses_private_exception(monkeypatch, settings, caplog):
    settings.DEBUG = True
    runtime, transport, _ = await mount()

    def broken(self, **kwargs):
        raise RuntimeError("RENDER_SECRET_SENTINEL")

    monkeypatch.setattr(EventChild, "get_context_data", broken)
    await increment(runtime)
    assert "RENDER_SECRET_SENTINEL" not in json.dumps(transport.sent)
    assert "RENDER_SECRET_SENTINEL" not in caplog.text
    assert transport.errors
    assert not any(frame.get("type") == "embedded_update" for frame in transport.sent)


async def test_child_save_timeout_is_withheld_and_lands_late(monkeypatch):
    """A slow store: the deadline passes, the turn is withheld, the save lands.

    The explicit save is one Django-thread hop (#3200). Sync work cannot be
    cancelled, so the old "cancelled" contract is gone: the deadline stops the
    WAIT, the turn answers with a transient error and no success frame, and
    the blocked write completes once storage responds.
    """
    import asyncio
    import threading

    from django.contrib.sessions.backends.db import SessionStore

    from djust import runtime as runtime_module

    runtime, transport, request = await mount()
    # The conftest raises the save bound for exposure tests (#3130). The save
    # below blocks until released, so a short bound always fires first.
    monkeypatch.setattr(runtime_module, "EVENT_STATE_SAVE_TIMEOUT_S", 0.05)
    release = threading.Event()
    saved = []
    original = SessionStore.save

    def slow_save(self, *args, **kwargs):
        release.wait(timeout=30)
        original(self, *args, **kwargs)
        saved.append(True)

    monkeypatch.setattr(SessionStore, "save", slow_save)
    dispatch = asyncio.ensure_future(increment(runtime))
    try:
        for _ in range(1000):
            if transport.errors or dispatch.done():
                break
            await asyncio.sleep(0.01)
    finally:
        release.set()
    await asyncio.wait_for(dispatch, timeout=10)  # hang guard only
    assert [error.get("transient") for error in transport.errors] == [True]
    assert "reload" not in transport.errors[0]["error"].lower()
    assert not any(frame.get("type") == "embedded_update" for frame in transport.sent)
    # The abandoned save still runs, and the catch-up turn follows it.
    await asyncio.wait_for(runtime._explicit_catch_up, timeout=10)
    assert saved[:1] == [True], "the abandoned save must still land"
    assert any(
        f.get("type") == "html_update" and f.get("source") == "async" for f in transport.sent
    ), "the catch-up turn must send full HTML"
    monkeypatch.setattr(SessionStore, "save", original)
    restored, _, _ = await mount(request.session.session_key)
    assert restored.view_instance._get_child_view("menu").count == 2
