"""#3646: the teardown hooks of a released view and the private-state restore enter a
``lifecycle`` step.

``_on_sticky_unmount``, ``_cleanup_on_unregister`` and ``_cleanup_uploads`` are
application hooks. Which thread runs them depends on the transport:

* WebSocket (``arelease_root_view``) and the SSE stream's own close (the stream
  generator's ``finally`` awaits ``session.shutdown()``): inline on the EVENT LOOP.
  No step: a wrapper that opens a transaction would raise there, and a hook that
  queries fails on the loop with or without a wrapper.
* SSE navigation and the release of a view mounted beside the page
  (``sync_to_async(release_root_view)``), and ``shutdown()`` reached through
  ``abandon()`` / ``response.close()`` for a stream that never started: a WORKER
  THREAD, the thread class where an RLS read needs the scope. They run inside a
  ``lifecycle`` step.

Each test records the step open while the hook body ran (a thread-local the
wrapper sets) and the thread, so it passes only if the hook really ran inside the
scope. Both transports are driven through their real entry points.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
import uuid
from typing import Dict, List, Optional, Tuple

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, override_settings
from django.urls import path
from django.utils.functional import SimpleLazyObject

from djust import LiveView, sse
from djust.config import config as djust_config
from djust.decorators import event_handler, push_handler
from djust.hooks import on_mount
from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions

pytest.importorskip("channels")

pytestmark = [pytest.mark.django_db(transaction=True)]

MOD = __name__

ENTERED: List[Tuple[str, int]] = []
#: ``(label, step open while the hook body ran, thread)``.
SEEN: List[Tuple[str, Optional[str], int]] = []
_OPEN = threading.local()
MODE: Dict[str, str] = {}


def recording_wrapper(view, step, turn):
    @contextlib.contextmanager
    def scope():
        mode = MODE.get(step)
        ENTERED.append((step, threading.get_ident()))
        if mode == "raise":
            raise RuntimeError("the wrapper cannot open its scope")
        _OPEN.step = step
        try:
            yield
        finally:
            _OPEN.step = None
        if mode == "exit-raises":
            raise RuntimeError("the wrapper cannot commit")

    return scope()


def note(label: str) -> None:
    SEEN.append((label, getattr(_OPEN, "step", None), threading.get_ident()))


def labels() -> List[str]:
    return [label for label, _step, _thread in SEEN]


def seen(label: str) -> List[Tuple[Optional[str], int]]:
    return [(step, thread) for found, step, thread in SEEN if found == label]


class Kid(LiveView):
    """A legacy embedded child with an unregister hook."""

    exposure_policy = "legacy"
    template = "<div><span>kid</span></div>"

    def mount(self, request, **kwargs):
        pass

    def _cleanup_on_unregister(self):
        note("cleanup_on_unregister")


class ExplicitKid(LiveView):
    """An explicit child: disposed, so all three teardown hooks run."""

    exposure_policy = "explicit"
    template = "<div><span>explicit</span></div>"

    def mount(self, request, **kwargs):
        pass

    def _cleanup_uploads(self):
        note("cleanup_uploads")

    def _cleanup_on_unregister(self):
        note("explicit_cleanup_on_unregister")

    def _on_sticky_unmount(self):
        note("on_sticky_unmount")


class Page(LiveView):
    exposure_policy = "legacy"
    template = (
        "{% load live_tags %}"
        '<div dj-root dj-view="' + MOD + '.Page">'
        "<h1>page</h1>"
        '{% live_render "' + MOD + '.Kid" view_id="kid" %}'
        '{% live_render "' + MOD + '.ExplicitKid" view_id="ekid" %}'
        "</div>"
    )

    def mount(self, request, **kwargs):
        pass

    def get_context_data(self, **kwargs):
        return {"view": self}

    def _cleanup_uploads(self):
        note("page_cleanup_uploads")


class StickyKid(LiveView):
    """A sticky child: kept across a ``live_redirect`` unless its auth now fails."""

    exposure_policy = "legacy"
    sticky = True
    sticky_id = "dock"
    template = "<div><span>dock</span></div>"

    def mount(self, request, **kwargs):
        pass

    def _on_sticky_unmount(self):
        note("sticky_on_sticky_unmount")

    def _cleanup_on_unregister(self):
        note("sticky_cleanup_on_unregister")


class StickyPage(LiveView):
    exposure_policy = "legacy"
    template = (
        "{% load live_tags %}"
        '<div dj-root dj-view="' + MOD + '.StickyPage">'
        "<h1>sticky page</h1>"
        '{% live_render "' + MOD + '.StickyKid" sticky=True %}'
        "</div>"
    )

    def mount(self, request, **kwargs):
        pass

    def get_context_data(self, **kwargs):
        return {"view": self}


class Beta(LiveView):
    """A destination with no slot for the sticky child."""

    exposure_policy = "legacy"
    template = '<div dj-root dj-view="' + MOD + '.Beta"><h1>beta</h1></div>'

    def mount(self, request, **kwargs):
        pass


class PushPage(LiveView):
    """A page with a server-push handler and a signed-snapshot opt-in."""

    exposure_policy = "legacy"
    enable_state_snapshot = True
    template = '<div dj-root dj-view="' + MOD + '.PushPage"><h1>push</h1></div>'

    def mount(self, request, **kwargs):
        self.count = 0

    @push_handler
    def handle_pushed(self, **kwargs):
        note("push_handler")

    def _capture_snapshot_state(self, *, strict=False):
        note("capture_snapshot")
        return {"count": self.count}


class Dest(LiveView):
    exposure_policy = "legacy"
    template = '<div dj-root dj-view="' + MOD + '.Dest"><h1>dest</h1></div>'

    def mount(self, request, **kwargs):
        pass


urlpatterns = [
    path("page/", Page.as_view()),
    path("dest/", Dest.as_view()),
    path("sticky/", StickyPage.as_view()),
    path("beta/", Beta.as_view()),
    path("push/", PushPage.as_view()),
]

#: The three teardown hooks, as the labels they record.
HOOKS = {
    "cleanup_on_unregister",
    "explicit_cleanup_on_unregister",
    "on_sticky_unmount",
    "cleanup_uploads",
    "page_cleanup_uploads",
}


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(sse, "_SESSION_LINGER_S", 0)
    previous = djust_config.get("sync_turn_wrapper")
    ENTERED.clear()
    SEEN.clear()
    MODE.clear()
    _sse_sessions.clear()
    djust_config.set("sync_turn_wrapper", recording_wrapper)
    with override_settings(
        ROOT_URLCONF=__name__, LIVEVIEW_ALLOWED_MODULES=["djust", __name__], DEBUG=False
    ):
        yield
    djust_config.set("sync_turn_wrapper", previous)
    _sse_sessions.clear()
    ENTERED.clear()
    SEEN.clear()
    MODE.clear()


async def _until(predicate, what):
    for _ in range(500):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out waiting for %s; seen=%r entered=%r" % (what, SEEN, ENTERED))


# --------------------------------------------------------------------------- #
# SSE
# --------------------------------------------------------------------------- #


def _request(method, url, body, key):
    factory = RequestFactory()
    if method == "GET":
        request = factory.get(url, data=body)
    else:
        request = factory.post(url, data=json.dumps(body), content_type="application/json")
    request.session = SessionStore(key)
    request.user = SimpleLazyObject(lambda: get_user(request))
    request.tenant = None
    return request


def _fresh_key():
    session = SessionStore()
    session.create()
    return session.session_key


async def _sse_page():
    key = await sync_to_async(_fresh_key)()
    sid = str(uuid.uuid4())
    request = await sync_to_async(_request)(
        "GET", f"/djust/sse/{sid}/", {"view": MOD + ".Page", "_djust_url": "/page/"}, key
    )
    response = await DjustSSEStreamView().get(request, session_id=sid)
    assert response.status_code == 200
    stream = response._iterator
    assert "sse_connect" in await stream.__anext__()
    return _sse_sessions[sid], key, stream


async def _post(session, key, body):
    request = await sync_to_async(_request)(
        "POST", f"/djust/sse/{session.session_id}/message/", body, key
    )
    response = await DjustSSEMessageView().post(request, session_id=session.session_id)
    assert response.status_code == 200


def _all_ran_in_a_lifecycle_step_off_the_loop(loop_thread):
    for hook in (
        "cleanup_on_unregister",
        "explicit_cleanup_on_unregister",
        "on_sticky_unmount",
        "cleanup_uploads",
        "page_cleanup_uploads",
    ):
        found = seen(hook)
        assert found, (hook, SEEN)
        for step, thread in found:
            assert step == "lifecycle", (hook, step, SEEN)
            assert thread != loop_thread, (hook, "ran on the event loop", SEEN)


async def _abandoned_page():
    """A mounted SSE session whose stream never started, and the response to close.

    ``response.close()`` is what the ASGI server calls, on a worker thread, when the
    client leaves before reading the stream: ``_SSEStream.close`` -> ``abandon()`` ->
    ``shutdown()``.
    """
    key = await sync_to_async(_fresh_key)()
    sid = str(uuid.uuid4())
    request = await sync_to_async(_request)(
        "GET", f"/djust/sse/{sid}/", {"view": MOD + ".Page", "_djust_url": "/page/"}, key
    )
    response = await DjustSSEStreamView().get(request, session_id=sid)
    assert response.status_code == 200
    assert sid in _sse_sessions
    return response


async def test_an_sse_response_close_runs_the_teardown_hooks_inside_a_lifecycle_step():
    loop_thread = threading.get_ident()
    response = await _abandoned_page()
    del SEEN[:]
    await sync_to_async(response.close)()  # what the server does: a worker thread
    await _until(lambda: HOOKS <= set(labels()), "the teardown hooks")

    _all_ran_in_a_lifecycle_step_off_the_loop(loop_thread)


async def test_an_sse_stream_close_runs_the_teardown_hooks_on_the_loop_without_a_step():
    """The generator's ``finally`` awaits ``shutdown()``: inline on the loop, not wrapped."""
    loop_thread = threading.get_ident()
    _session, _key, stream = await _sse_page()
    del SEEN[:]
    before = len(ENTERED)
    await stream.aclose()
    await _until(lambda: HOOKS <= set(labels()), "the teardown hooks")

    for hook in HOOKS:
        for step, thread in seen(hook):
            assert step is None, (hook, "was wrapped on the loop", SEEN)
            assert thread == loop_thread, (hook, "left the loop", SEEN)
    assert ENTERED[before:] == [], ENTERED[before:]


async def test_an_sse_navigation_runs_the_teardown_hooks_inside_a_lifecycle_step():
    loop_thread = threading.get_ident()
    session, key, stream = await _sse_page()
    del SEEN[:]
    try:
        await _post(session, key, {"type": "live_redirect_mount", "url": "/dest/", "params": {}})
        await _until(lambda: HOOKS <= set(labels()), "the teardown hooks")
    finally:
        await stream.aclose()

    _all_ran_in_a_lifecycle_step_off_the_loop(loop_thread)


async def test_an_sse_response_close_still_tears_down_when_the_wrapper_cannot_open_its_scope(
    caplog,
):
    """A teardown must go on: a wrapper that raises on entry does not skip the hooks."""
    response = await _abandoned_page()
    del SEEN[:]
    MODE["lifecycle"] = "raise"
    with caplog.at_level(logging.WARNING, logger="djust"):
        await sync_to_async(response.close)()
        await _until(lambda: HOOKS <= set(labels()), "the teardown hooks")

    # The body ran, without the scope the wrapper failed to open.
    assert {step for _label, step, _thread in SEEN} == {None}, SEEN
    assert "sync_turn_wrapper failed around a lifecycle teardown" in caplog.text


async def test_an_sse_response_close_logs_a_wrapper_that_fails_on_exit_and_tears_down_once(caplog):
    response = await _abandoned_page()
    del SEEN[:]
    MODE["lifecycle"] = "exit-raises"
    with caplog.at_level(logging.WARNING, logger="djust"):
        await sync_to_async(response.close)()
        await _until(lambda: HOOKS <= set(labels()), "the teardown hooks")

    for hook in HOOKS:
        assert len(seen(hook)) == 1, (hook, SEEN)
        assert seen(hook)[0][0] == "lifecycle"
    assert "sync_turn_wrapper failed around a lifecycle teardown" in caplog.text


# --------------------------------------------------------------------------- #
# WebSocket: the release runs on the event loop and is not wrapped
# --------------------------------------------------------------------------- #


async def _ws_mounted():
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    key = await sync_to_async(_fresh_key)()

    class _ScopeSession:
        session_key = key

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = _ScopeSession()
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from(timeout=3)
    await communicator.send_json_to({"type": "mount", "view": MOD + ".Page", "url": "/page/"})
    for _ in range(8):
        frame = await communicator.receive_json_from(timeout=10)
        if frame.get("type") == "mount":
            return communicator
    raise AssertionError("no mount frame")


async def test_a_websocket_disconnect_runs_the_teardown_hooks_on_the_loop_without_a_step():
    loop_thread = threading.get_ident()
    communicator = await _ws_mounted()
    del SEEN[:]
    before = len(ENTERED)
    await communicator.disconnect()
    await _until(lambda: HOOKS <= set(labels()), "the teardown hooks")

    for hook in HOOKS:
        for step, thread in seen(hook):
            assert step is None, (hook, "was wrapped on the loop", SEEN)
            assert thread == loop_thread, (hook, "left the loop", SEEN)
    # No lifecycle entry from the teardown (the views define neither connected nor
    # disconnected, so nothing else enters one).
    assert ENTERED[before:] == [], ENTERED[before:]


# --------------------------------------------------------------------------- #
# run_teardown_step itself
# --------------------------------------------------------------------------- #


def test_run_teardown_step_enters_the_wrapper_on_a_plain_thread():
    from djust.sync_turn import run_teardown_step

    result = run_teardown_step(
        object(), "lifecycle", lambda a, b=0: (getattr(_OPEN, "step", None), a, b), 1, b=2
    )

    assert result == ("lifecycle", 1, 2)
    assert [step for step, _thread in ENTERED] == ["lifecycle"]


async def test_run_teardown_step_does_not_enter_the_wrapper_on_the_event_loop():
    from djust.sync_turn import run_teardown_step

    result = run_teardown_step(object(), "lifecycle", lambda: getattr(_OPEN, "step", None))

    assert result is None
    assert ENTERED == []


def test_run_teardown_step_does_not_nest_inside_another_step():
    from djust.sync_turn import run_sync_step, run_teardown_step

    run_sync_step(object(), "event", lambda: run_teardown_step(object(), "lifecycle", note, "x"))

    assert [step for step, _thread in ENTERED] == ["event"]
    assert seen("x")[0][0] == "event"


def test_run_teardown_step_propagates_the_bodys_own_failure():
    from djust.sync_turn import run_teardown_step

    def boom():
        raise ValueError("body")

    with pytest.raises(ValueError, match="body"):
        run_teardown_step(object(), "lifecycle", boom)


def test_run_teardown_step_runs_the_body_when_the_wrapper_fails_on_entry():
    from djust.sync_turn import run_teardown_step

    MODE["lifecycle"] = "raise"
    calls = []

    assert run_teardown_step(object(), "lifecycle", lambda: calls.append(1) or "ran") == "ran"
    assert calls == [1]


def test_run_teardown_step_returns_the_result_when_the_wrapper_fails_on_exit():
    from djust.sync_turn import run_teardown_step

    MODE["lifecycle"] = "exit-raises"
    calls = []

    assert run_teardown_step(object(), "lifecycle", lambda: calls.append(1) or "ran") == "ran"
    assert calls == [1]


def test_run_teardown_step_costs_nothing_without_a_wrapper():
    from djust.sync_turn import run_teardown_step

    djust_config.set("sync_turn_wrapper", None)

    assert run_teardown_step(object(), "lifecycle", lambda: "ran") == "ran"
    assert ENTERED == []


# --------------------------------------------------------------------------- #
# WebSocket live_redirect: a sticky child whose auth now fails (item 6)
# --------------------------------------------------------------------------- #


async def _ws_sticky_page():
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    key = await sync_to_async(_fresh_key)()

    class _ScopeSession:
        session_key = key

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = _ScopeSession()
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from(timeout=3)
    await communicator.send_json_to(
        {"type": "mount", "view": MOD + ".StickyPage", "url": "/sticky/"}
    )
    for _ in range(8):
        frame = await communicator.receive_json_from(timeout=10)
        if frame.get("type") == "mount":
            return communicator
    raise AssertionError("no mount frame")


async def _redirect_to_beta(communicator):
    await communicator.send_json_to(
        {"type": "live_redirect_mount", "view": MOD + ".Beta", "url": "/beta/", "params": {}}
    )
    for _ in range(8):
        frame = await communicator.receive_json_from(timeout=10)
        if frame.get("type") == "mount":
            return
    raise AssertionError("no mount frame after the redirect")


async def test_a_sticky_child_its_auth_now_denies_is_discarded_inside_a_lifecycle_step(
    monkeypatch,
):
    """``_preserve_sticky_children`` runs on a worker thread; the discard it makes
    called the child's teardown hooks with no step before (#3646 item 6)."""
    loop_thread = threading.get_ident()
    communicator = await _ws_sticky_page()
    try:
        monkeypatch.setattr(StickyKid, "login_required", True, raising=False)
        del SEEN[:]
        await _redirect_to_beta(communicator)
        await _until(lambda: "sticky_on_sticky_unmount" in labels(), "the discard")
    finally:
        await communicator.disconnect()

    for hook in ("sticky_on_sticky_unmount", "sticky_cleanup_on_unregister"):
        found = seen(hook)
        if hook == "sticky_cleanup_on_unregister" and not found:
            continue  # a navigation discard runs only _on_sticky_unmount
        assert found, (hook, SEEN)
        for step, thread in found:
            assert step == "lifecycle", (hook, step, SEEN)
            assert thread != loop_thread, (hook, "ran on the event loop", SEEN)


# --------------------------------------------------------------------------- #
# _restore_private_state: the model re-fetch and the setters are a lifecycle step
# --------------------------------------------------------------------------- #


class Restorable(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root><span>r</span></div>"

    @property
    def _gadget(self):
        return self.__dict__.get("_gadget_value")

    @_gadget.setter
    def _gadget(self, value):
        note("private_setter")
        self.__dict__["_gadget_value"] = value


def _restorable():
    view = Restorable()
    view._framework_attrs = frozenset()
    view._user_private_keys = set()
    return view


async def test_the_private_state_restore_runs_its_setters_inside_a_lifecycle_step():
    view = _restorable()
    await sync_to_async(view._restore_private_state)({"_gadget": 7})

    assert view._gadget == 7
    assert seen("private_setter") == [("lifecycle", seen("private_setter")[0][1])]
    assert [step for step, _thread in ENTERED] == ["lifecycle"]


async def test_the_private_model_re_fetch_runs_inside_a_lifecycle_step():
    """``decode_private_model_refs`` reads the row: under row-level security that read
    needs the wrapper's scope, or the attribute comes back ``None``."""
    from django.contrib.auth import get_user_model
    from django.db.models.signals import post_init

    User = get_user_model()
    user = await sync_to_async(User.objects.create)(username="restore-" + uuid.uuid4().hex[:6])

    def loaded(sender, **kwargs):
        note("model_loaded")

    post_init.connect(loaded, sender=User, weak=False)
    try:
        view = _restorable()
        from djust.serialization import _PRIVATE_MODEL_REF_KEY

        ref = {_PRIVATE_MODEL_REF_KEY: User._meta.label, "pk": user.pk}
        del SEEN[:]
        await sync_to_async(view._restore_private_state)({"_gadget": ref})
    finally:
        post_init.disconnect(loaded, sender=User)

    assert view._gadget is not None and view._gadget.pk == user.pk
    assert [step for step, _thread in seen("model_loaded")] == ["lifecycle"], SEEN


async def test_the_private_state_restore_does_not_enter_the_wrapper_again_inside_a_step():
    from djust.sync_turn import run_sync_step

    view = _restorable()
    await sync_to_async(run_sync_step)(view, "mount", view._restore_private_state, {"_gadget": 1})

    assert [step for step, _thread in ENTERED] == ["mount"]


# --------------------------------------------------------------------------- #
# The worker-pool server-push turn (one hop) enters the same step as the stock turn
# --------------------------------------------------------------------------- #


@pytest.fixture(params=[None, 2], ids=["stock", "pool"])
def pool(request, monkeypatch):
    from djust import worker_pool

    monkeypatch.setitem(djust_config._config, "worker_threads", request.param)
    monkeypatch.setattr(worker_pool, "_pool", [])
    yield request.param


async def _ws_push_page():
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    key = await sync_to_async(_fresh_key)()

    class _ScopeSession:
        session_key = key

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = _ScopeSession()
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from(timeout=3)
    await communicator.send_json_to({"type": "mount", "view": MOD + ".PushPage", "url": "/push/"})
    for _ in range(8):
        frame = await communicator.receive_json_from(timeout=10)
        if frame.get("type") == "mount":
            return communicator
    raise AssertionError("no mount frame")


async def test_a_server_push_handler_runs_inside_the_wrapper_in_both_turn_shapes(pool):
    """The offloaded turn called ``handler_fn(**payload)`` raw (#3646)."""
    from djust.push import apush_to_view

    communicator = await _ws_push_page()
    try:
        del SEEN[:]
        await apush_to_view(MOD + ".PushPage", handler="handle_pushed", payload={})
        await _until(lambda: "push_handler" in labels(), "the push handler")
    finally:
        await communicator.disconnect()

    assert [step for step, _thread in seen("push_handler")] == ["hook"], SEEN


# --------------------------------------------------------------------------- #
# The per-event signed-snapshot refresh (#3098) enters the step the mount does
# --------------------------------------------------------------------------- #


async def test_the_event_snapshot_refresh_captures_state_inside_a_lifecycle_step():
    from djust.security.state_snapshot import legacy_snapshot_fields

    view = PushPage()
    view.count = 1
    fields = await sync_to_async(legacy_snapshot_fields)(view, MOD + ".PushPage", "session-key")

    assert fields["view"] == MOD + ".PushPage"
    assert [step for step, _thread in seen("capture_snapshot")] == ["lifecycle"], SEEN


# --------------------------------------------------------------------------- #
# A lazy ``{% live_render lazy=True %}`` child mounts and builds its context in a step
# --------------------------------------------------------------------------- #


class LazyKid(LiveView):
    template = "<div><p>lazy {{ value }}</p></div>"

    def mount(self, request, **kwargs):
        note("lazy_mount")
        self.value = "kid"

    def get_context_data(self, **kwargs):
        note("lazy_context")
        return {"value": self.value}


class LazyPage(LiveView):
    template = (
        "{% load live_tags %}"
        "<html><body><div dj-root>"
        '{% live_render "' + MOD + '.LazyKid" lazy=True %}'
        "</div></body></html>"
    )

    def mount(self, request, **kwargs):
        pass


async def test_a_lazy_childs_mount_and_context_run_inside_the_wrapper():
    """``_render_eager`` called the child's ``mount`` and ``get_context_data`` raw on a
    worker thread (#3646): the fill of a lazy slot ran them with no scope."""
    from django.contrib.sessions.middleware import SessionMiddleware

    def render_page():
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        view = LazyPage()
        view.streaming_render = False
        with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=[MOD]):
            return view.get(request).content.decode("utf-8")

    html = await sync_to_async(render_page)()

    assert "lazy kid" in html, html
    assert [step for step, _thread in seen("lazy_mount")] == ["mount"], SEEN
    assert {step for step, _thread in seen("lazy_context")} == {"render"}, SEEN


# --------------------------------------------------------------------------- #
# The guide's "entries per request" list, as executable claims (#3646 item 5)
# --------------------------------------------------------------------------- #


class SequencePage(LiveView):
    template = "<div dj-root><span>{{ count }}</span></div>"

    def mount(self, request, **kwargs):
        self.count = 0

    def handle_params(self, params, uri, **kwargs):
        pass

    def get_object(self):
        return None

    def has_object_permission(self, request, obj):
        return True

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1

    def get_context_data(self, **kwargs):
        return {"count": self.count}


@on_mount
def _sequence_on_mount(view, request, **kwargs):
    return None


class SequencePageWithOnMount(SequencePage):
    on_mount = [_sequence_on_mount]


def _page_request(method, session=None):
    from django.contrib.sessions.middleware import SessionMiddleware

    factory = RequestFactory()
    if method == "GET":
        request = factory.get("/seq/")
    else:
        request = factory.post(
            "/seq/",
            data=json.dumps({"event": "bump", "params": {}}),
            content_type="application/json",
            HTTP_X_DJUST_EVENT="bump",
        )
    request.user = AnonymousUser()
    if session is None:
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
    else:
        request.session = session
    return request


def _entered_by(view_class, *requests_):
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[MOD]):
        del ENTERED[:]
        for request in requests_:
            view_class.as_view()(request)
        return [step for step, _thread in ENTERED]


def test_the_guides_entries_per_request_for_a_page_get():
    assert _entered_by(SequencePage, _page_request("GET")) == [
        "auth",
        "mount",
        "handle_params",
        "auth",
        "render",
        "render",
    ]
    assert _entered_by(SequencePageWithOnMount, _page_request("GET")) == [
        "auth",
        "lifecycle",
        "mount",
        "handle_params",
        "auth",
        "render",
        "render",
    ]


def test_the_guides_entries_per_request_for_a_post_event():
    fresh = _entered_by(SequencePage, _page_request("POST"))
    assert fresh == ["auth", "mount", "handle_params", "auth", "event", "render", "render"]

    first = _page_request("GET")
    SequencePage.as_view()(first)
    restored = _entered_by(SequencePage, _page_request("POST", session=first.session))
    assert restored == ["auth", "lifecycle", "auth", "hook", "event", "render", "render"]
