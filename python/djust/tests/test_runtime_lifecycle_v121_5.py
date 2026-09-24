"""v1.2.1-5 — runtime routing, mount and sticky lifecycle.

* #2962 — ``self.listen()`` in ``mount()`` (or a later handler) joins the
  ``djust_db_notify_<channel>`` group, so ``handle_info`` fires.
* #2924 — a component handler's ``_skip_render`` is honoured and consumed on
  the ``component_id`` route instead of leaking into the next view event.
* #2969 — ``AsyncWorkMixin.cancel_async_all()`` exists, sticky unmount uses
  it, and the runtime path honours ``cancel_async`` for a running task.
* #2919 — a reused sticky child warns when the tag's kwargs change (they are
  mount-time only in 1.2).
* #2961 — SQL capture sees queries from sync event handlers.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from typing import Any, Dict, List, Optional

import pytest
from asgiref.sync import sync_to_async
from django.db import connection
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust.components.descriptors.base import LiveComponent, TypedState
from djust.decorators import event_handler
from djust.mixins.async_work import AsyncWorkMixin
from djust.runtime import ViewRuntime

_MOD = "djust.tests.test_runtime_lifecycle_v121_5"


# ------------------------------------------------------------------ #
# Shared harness
# ------------------------------------------------------------------ #


class MockTransport:
    def __init__(self) -> None:
        self._session_id = str(uuid.uuid4())
        self.sent: List[Dict[str, Any]] = []

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def client_ip(self) -> Optional[str]:
        return None

    async def send(self, data: Dict[str, Any]) -> None:
        self.sent.append(data)

    async def send_error(self, error: str, **kwargs: Any) -> None:
        self.sent.append({"type": "error", "error": error, **kwargs})

    async def close(self, code: int = 1000) -> None:
        pass

    def next_client_version(self, html: Optional[str], rust_version: int) -> int:
        return rust_version

    def build_request(self) -> Optional[Any]:
        return None

    def on_view_mounted(self, view_instance: Any) -> None:
        pass

    def on_event_frame(self, view: Any, frame: Dict[str, Any], **kwargs: Any) -> None:
        pass

    @contextlib.asynccontextmanager
    async def event_context(self, view: Any):
        yield


def _mounted(cls: type):
    view = cls()
    view.mount(None)
    transport = MockTransport()
    runtime = ViewRuntime(transport)
    runtime.view_instance = view
    view.render_with_diff()  # mount baseline
    return view, runtime, transport


def _frames(transport: MockTransport) -> List[Dict[str, Any]]:
    return [f for f in transport.sent if f.get("type") in ("patch", "html_update", "noop")]


class _ScopeSession:
    def __init__(self, key: str) -> None:
        self.session_key = key


async def _receive_until(communicator, wanted_type, *, tries=8, timeout=3):
    last = None
    for _ in range(tries):
        last = await communicator.receive_json_from(timeout=timeout)
        if last.get("type") == wanted_type:
            return last
    return last


async def _connect_and_mount(view_path: str, url: str = "/v121-5/"):
    pytest.importorskip("channels")
    from channels.testing import WebsocketCommunicator
    from django.contrib.sessions.backends.db import SessionStore

    from djust.websocket import LiveViewConsumer

    def _create_session():
        s = SessionStore()
        s.create()
        return s.session_key

    session_key = await sync_to_async(_create_session)()
    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = _ScopeSession(session_key)
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from(timeout=2)  # connect frame
    await communicator.send_json_to({"type": "mount", "view": view_path, "url": url})
    mount_frame = await _receive_until(communicator, "mount")
    assert mount_frame.get("type") == "mount", mount_frame
    return communicator, mount_frame


# ------------------------------------------------------------------ #
# #2962 — listen() in mount() / a handler joins the NOTIFY group
# ------------------------------------------------------------------ #


class _FakeListener:
    channels: List[str] = []

    async def ensure_listening(self, channel: str) -> None:
        self.channels.append(channel)


@pytest.fixture
def fake_pg_listener(monkeypatch):
    from djust.db import notifications

    listener = _FakeListener()
    listener.channels = []
    monkeypatch.setattr(notifications.PostgresNotifyListener, "instance", lambda: listener)
    return listener


class ListenInMountView(LiveView):
    template = "<div dj-root><p>{{ seen }}</p></div>"

    def mount(self, request, **kwargs):
        self.seen = "none"
        self.listen("orders_2962")

    def handle_info(self, message):
        self.seen = message["payload"].get("id", "?")


class ListenLaterView(LiveView):
    template = "<div dj-root><p>{{ seen }}</p></div>"

    def mount(self, request, **kwargs):
        self.seen = "none"

    @event_handler()
    def subscribe(self, **kwargs):
        self.listen("later_2962")
        self.seen = "subscribed"

    def handle_info(self, message):
        self.seen = message["payload"].get("id", "?")


async def _notify(group: str, channel: str, payload: Dict[str, Any]) -> None:
    from channels.layers import get_channel_layer

    await get_channel_layer().group_send(
        group, {"type": "db_notify", "channel": channel, "payload": payload}
    )


@pytest.mark.django_db(transaction=True)
class TestListenJoinsTheGroup:
    @pytest.mark.asyncio
    async def test_listen_in_mount_receives_the_notify(self, fake_pg_listener):
        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_MOD]):
            communicator, mounted = await _connect_and_mount(f"{_MOD}.ListenInMountView")
            try:
                assert fake_pg_listener.channels == ["orders_2962"]
                await _notify("djust_db_notify_orders_2962", "orders_2962", {"id": "o-7"})
                frame = await _receive_until(communicator, "patch")
                assert "o-7" in str(frame), frame
            finally:
                await communicator.disconnect()

    @pytest.mark.asyncio
    async def test_listen_in_a_later_handler_receives_the_notify(self, fake_pg_listener):
        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_MOD]):
            communicator, _ = await _connect_and_mount(f"{_MOD}.ListenLaterView")
            try:
                await communicator.send_json_to(
                    {"type": "event", "event": "subscribe", "params": {}, "ref": 1}
                )
                await _receive_until(communicator, "patch")
                await _notify("djust_db_notify_later_2962", "later_2962", {"id": "l-3"})
                frame = await _receive_until(communicator, "patch")
                assert "l-3" in str(frame), frame
            finally:
                await communicator.disconnect()

    @pytest.mark.asyncio
    async def test_join_is_idempotent(self):
        from djust.runtime import WSConsumerTransport

        joined: List[str] = []

        class _Layer:
            async def group_add(self, group, channel):
                joined.append(group)

        class _Consumer:
            channel_layer = _Layer()
            channel_name = "c1"
            _db_notify_channels: set = set()

        consumer = _Consumer()
        consumer._db_notify_channels = set()
        transport = WSConsumerTransport(consumer)

        class _View:
            _listen_channels = {"a_2962", "b_2962"}

        await transport._join_listen_channels(_View())
        await transport._join_listen_channels(_View())
        assert sorted(joined) == ["djust_db_notify_a_2962", "djust_db_notify_b_2962"]
        assert consumer._db_notify_channels == {"a_2962", "b_2962"}


# ------------------------------------------------------------------ #
# #2924 — _skip_render on the component_id route
# ------------------------------------------------------------------ #


class Quiet(LiveComponent):
    class State(TypedState):
        count: int = 0

    template = "<p>{{ count }}</p>"

    @event_handler()
    def bump_quietly(self, **kwargs: Any) -> None:
        self.state.count += 1
        self._view._skip_render = True


class QuietPage(LiveView):
    template = "<div dj-root><h1>{{ title }}</h1>{{ quiet }}</div>"
    quiet = Quiet()

    def mount(self, request: Any, **kwargs: Any) -> None:
        self.title = "X"

    @event_handler()
    def rename(self, value: str = "", **kwargs: Any) -> None:
        self.title = value


@pytest.mark.django_db
class TestSkipRenderOnTheComponentRoute:
    @pytest.mark.asyncio
    async def test_component_skip_render_answers_noop_and_is_consumed(self):
        view, runtime, transport = _mounted(QuietPage)
        await runtime.dispatch_event(
            {
                "type": "event",
                "event": "bump_quietly",
                "params": {"component_id": "quiet"},
                "ref": 1,
            }
        )
        first = _frames(transport)[-1]
        assert first["type"] == "noop" and first["ref"] == 1, first
        assert view._skip_render is False
        # The next view event is not swallowed by a leaked flag.
        await runtime.dispatch_event(
            {"type": "event", "event": "rename", "params": {"value": "Y"}, "ref": 2}
        )
        second = _frames(transport)[-1]
        assert second["type"] in ("patch", "html_update"), second
        assert "Y" in str(second)

    @pytest.mark.asyncio
    async def test_forced_full_html_still_wins(self):
        view, runtime, transport = _mounted(QuietPage)
        view._force_full_html = True
        await runtime.dispatch_event(
            {
                "type": "event",
                "event": "bump_quietly",
                "params": {"component_id": "quiet"},
                "ref": 1,
            }
        )
        assert _frames(transport)[-1]["type"] == "html_update"
        assert view._skip_render is False


# ------------------------------------------------------------------ #
# #2969 — cancel_async_all
# ------------------------------------------------------------------ #


class _Work(AsyncWorkMixin):
    def cb(self):
        return None


class TestCancelAsyncAll:
    def test_drops_scheduled_tasks_without_poisoning_the_names(self):
        w = _Work()
        w.start_async(w.cb, name="a")
        w.start_async(w.cb)
        w._async_pending = (w.cb, (), {})
        w.cancel_async_all()
        assert w._async_tasks == {}
        assert w._async_pending is None
        # A task scheduled later under the same name is NOT pre-cancelled.
        assert "a" not in getattr(w, "_async_cancelled", set())

    def test_marks_running_tasks_cancelled(self):
        w = _Work()
        w._async_running = {"export"}
        w.cancel_async_all()
        assert "export" in w._async_cancelled

    def test_noop_on_a_fresh_view(self):
        w = _Work()
        w.cancel_async_all()
        assert getattr(w, "_async_tasks", {}) == {}

    def test_sticky_unmount_cancels(self):
        class StickyChild(LiveView):
            sticky = True
            sticky_id = "c"
            template = "<div>x</div>"

        child = StickyChild()
        child.start_async(lambda: None, name="poll")
        child._async_running = {"stream"}
        child._on_sticky_unmount()
        assert child._async_tasks == {}
        assert "stream" in child._async_cancelled


class SlowView(LiveView):
    template = "<div dj-root><p>{{ result }}</p></div>"

    def mount(self, request: Any, **kwargs: Any) -> None:
        self.result = "idle"

    @event_handler()
    def go(self, **kwargs: Any) -> None:
        self.result = "working"
        self.start_async(self._work, name="export")

    async def _work(self):
        await self._gate.wait()
        self.result = "done"

    @event_handler()
    def stop(self, **kwargs: Any) -> None:
        self.cancel_async("export")
        self.result = "stopped"


@pytest.mark.django_db
class TestRuntimeHonoursCancel:
    @pytest.mark.asyncio
    async def test_cancelled_running_task_does_not_re_render(self):
        view, runtime, transport = _mounted(SlowView)
        view._gate = asyncio.Event()
        await runtime.dispatch_event({"type": "event", "event": "go", "params": {}, "ref": 1})
        await asyncio.sleep(0)
        assert "export" in view._async_running
        await runtime.dispatch_event({"type": "event", "event": "stop", "params": {}, "ref": 2})
        view._gate.set()
        for _ in range(5):
            await asyncio.sleep(0.01)
        async_frames = [f for f in transport.sent if f.get("source") == "async"]
        assert async_frames == [], async_frames
        assert "export" not in view._async_running
        assert "export" not in view._async_cancelled

    @pytest.mark.asyncio
    async def test_uncancelled_task_still_renders(self):
        view, runtime, transport = _mounted(SlowView)
        view._gate = asyncio.Event()
        view._gate.set()
        await runtime.dispatch_event({"type": "event", "event": "go", "params": {}, "ref": 1})
        for _ in range(5):
            await asyncio.sleep(0.01)
        async_frames = [f for f in transport.sent if f.get("source") == "async"]
        assert async_frames and "done" in str(async_frames[-1])
        assert "export" not in view._async_running


# ------------------------------------------------------------------ #
# #2919 — sticky reuse warns when kwargs change
# ------------------------------------------------------------------ #


class KwChild(LiveView):
    sticky = True
    sticky_id = "kwchild"
    template = "<div>child {{ n }}</div>"

    def mount(self, request, n=0, **kwargs):
        self.n = n

    def get_context_data(self, **kwargs):
        return {"n": self.n}


class KwParent(LiveView):
    template = (
        "{% load live_tags %}"
        '<div dj-root dj-view="' + _MOD + '.KwParent">'
        "<h1>{{ n }}</h1>"
        '{% live_render "' + _MOD + '.KwChild" sticky=True n=n %}'
        "</div>"
    )

    def mount(self, request, **kwargs):
        self.n = 1

    def get_context_data(self, **kwargs):
        return {"view": self, "n": self.n}


@pytest.mark.django_db
class TestStickyKwargsWarning:
    def _render(self, view):
        return view.render_with_diff()[0]

    def test_changed_kwarg_warns_once_and_unchanged_is_silent(self, caplog):
        with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=[_MOD]):
            request = RequestFactory().get("/")
            view = KwParent()
            view.request = request
            view.mount(request)
            html = self._render(view)
            assert "child 1" in html
            with caplog.at_level(logging.WARNING, logger="djust"):
                self._render(view)  # same kwargs: silent
                assert not [r for r in caplog.records if "mount-time only" in r.getMessage()]
                view.n = 2
                html = self._render(view)
                html = self._render(view)  # same changed value: no second warning
        warnings = [r for r in caplog.records if "mount-time only" in r.getMessage()]
        assert len(warnings) == 1, [r.getMessage() for r in warnings]
        assert "'n'" in warnings[0].getMessage()
        # Behaviour unchanged in 1.2.1: the child keeps its mount-time value.
        assert "child 1" in html


# ------------------------------------------------------------------ #
# #2961 — SQL capture from sync handlers
# ------------------------------------------------------------------ #


@pytest.mark.django_db(transaction=True)
class TestSqlCaptureFromSyncHandlers:
    @pytest.fixture(autouse=True)
    def _clean(self):
        from djust.observability.sql import _clear_queries

        _clear_queries()
        yield
        _clear_queries()

    @pytest.mark.asyncio
    async def test_sync_handler_queries_are_captured(self):
        from djust.observability.sql import capture_for_event, get_queries_since
        from djust.websocket_utils import _call_handler

        def handler():
            with connection.cursor() as cursor:
                cursor.execute("SELECT 2961")

        with override_settings(DEBUG=True):
            with capture_for_event(session_id="s-2961", handler_name="h"):
                await _call_handler(handler)
        entries = [e for e in get_queries_since() if "2961" in e["sql"]]
        assert len(entries) == 1, entries
        assert entries[0]["session_id"] == "s-2961"

    @pytest.mark.asyncio
    async def test_production_handlers_pay_nothing(self):
        from djust.observability.sql import capture_for_event, get_queries_since
        from djust.websocket_utils import _call_handler

        def handler():
            with connection.cursor() as cursor:
                cursor.execute("SELECT 29612")

        with override_settings(DEBUG=False):
            with capture_for_event(session_id="s", handler_name="h"):
                await _call_handler(handler)
        assert not [e for e in get_queries_since() if "29612" in e["sql"]]

    @pytest.mark.asyncio
    async def test_outside_a_scope_nothing_is_captured(self):
        from djust.observability.sql import get_buffer_size
        from djust.websocket_utils import _call_handler

        def handler():
            with connection.cursor() as cursor:
                cursor.execute("SELECT 29610")

        await _call_handler(handler)
        assert get_buffer_size() == 0

    def test_same_thread_is_not_recorded_twice(self):
        from djust.observability.sql import (
            capture_for_event,
            get_queries_since,
            run_in_capture_scope,
        )

        def handler():
            with connection.cursor() as cursor:
                cursor.execute("SELECT 29611")

        with override_settings(DEBUG=True):
            with capture_for_event(session_id="s", handler_name="h"):
                run_in_capture_scope(handler)()
        assert len([e for e in get_queries_since() if "29611" in e["sql"]]) == 1
