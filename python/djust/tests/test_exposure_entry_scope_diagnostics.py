"""ADR-038 E1-1 / D-a: protected diagnostic scopes on the transport entry points.

``handle_exception`` defaults to ``expose_details=True`` and diagnostics are
allowed outside any ``diagnostic_scope()``. These entry points ran view code
with no protected scope of their own, so an exception raised by an explicit
view's constructor, ``mount``, an ``on_mount`` hook, ``get_context_data`` or a
recovery render reached a client or a log with its message:

* HTTP GET (``get`` and the streaming ``aget``): Django's technical 500 page.
* SSE stream GET and SSE navigation (``_replace_view``): the stream bytes, or
  the technical 500 page of the request that ran the mount.
* The WebSocket ``receive`` catch-all, for the verbs that bypass
  ``dispatch_message`` (``request_html``, ``live_redirect_mount``,
  ``mount_batch``).
* ``ViewRuntime._instantiate_view``, which reports a constructor failure.

Every destination is checked with one sentinel: the response or stream bytes
or WebSocket frames, the log, and the observability traceback ring. Each
nonlegacy case has a legacy control that pins today's behaviour and proves the
failing path actually ran.

Contract (ADR-038 D-a, revised 2026-09-22): every test runs under both
``DEBUG`` modes. With ``DEBUG=False`` a nonlegacy view's failure is value-free
at every destination. With ``DEBUG=True`` every view's failure reads like
Django's DEBUG output — the technical 500 page, the detailed error frame, the
logged exception and the traceback-ring entry — exactly as a legacy view's does.
"""

import asyncio
import json
import logging
import sys
from collections import deque

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.core.exceptions import SuspiciousOperation
from django.core.signals import got_request_exception
from django.http import Http404
from django.test import AsyncClient, Client, override_settings
from django.urls import path

from djust import LiveView, event_handler
from djust.hooks import on_mount
from djust.observability import tracebacks
from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions
from djust.websocket import LiveViewConsumer

from ._ws_frames import drain_extra, receive_type, types_of

SENTINEL = "E1_ENTRY_SCOPE_SENTINEL"
NONLEGACY = ["explicit", None, "invalid"]
POLICIES = ["legacy", *NONLEGACY]
STATIC_LOG = "Protected view operation failed"


def _fail(view, stage):
    if type(view).fail_at == f"{stage}:bad_request":
        raise SuspiciousOperation(f"{SENTINEL} raised at {stage}")
    if type(view).fail_at == f"{stage}:not_found":
        raise Http404(f"{SENTINEL} raised at {stage}")
    if type(view).fail_at == stage:
        secret_local = SENTINEL  # a frame local, as a technical 500 page would show
        raise ValueError(f"{secret_local} raised at {stage}")


@on_mount
def entry_hook(view, request, **kwargs):
    _fail(view, "hook")


class EntryView(LiveView):
    exposure_policy = "legacy"
    fail_at = None
    template = '<div dj-root><span>{{ label }}</span><button dj-click="bump">b</button></div>'
    on_mount = [entry_hook]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        _fail(self, "init")

    def mount(self, request, **kwargs):
        self.label = "mounted"
        _fail(self, "mount")

    def get_context_data(self, **kwargs):
        _fail(self, "render")
        return super().get_context_data(label=self.label, **kwargs)

    def _strip_comments_and_whitespace(self, html):
        # The WS recovery (request_html) path calls this on the view after the
        # mount; an override raising there escapes to the receive catch-all.
        _fail(self, "recovery")
        return super()._strip_comments_and_whitespace(html)

    @event_handler()
    def bump(self):
        self.label = "bumped"


class StreamingEntryView(EntryView):
    streaming_render = True


def _declared(base, index, policy):
    # HTTP routes are built by as_view() at URLconf import, from the policy the
    # class declares, so each policy gets its own declaring class and route.
    return type(f"{base.__name__}{index}", (base,), {"exposure_policy": policy})


HTTP_VIEWS = {policy: _declared(EntryView, i, policy) for i, policy in enumerate(POLICIES)}
STREAM_VIEWS = {
    policy: _declared(StreamingEntryView, i, policy) for i, policy in enumerate(POLICIES)
}

urlpatterns = [
    path("entry/", EntryView.as_view()),
    *(path(f"entry/{i}/", HTTP_VIEWS[p].as_view()) for i, p in enumerate(POLICIES)),
    *(path(f"stream-entry/{i}/", STREAM_VIEWS[p].as_view()) for i, p in enumerate(POLICIES)),
    path("djust/sse/<str:session_id>/", DjustSSEStreamView.as_view()),
    path("djust/sse/<str:session_id>/message/", DjustSSEMessageView.as_view()),
]

VIEW_PATH = f"{__name__}.EntryView"


@pytest.fixture(params=[False, True], ids=["prod", "debug"])
def debug(request):
    return request.param


@pytest.fixture(autouse=True)
def staged(monkeypatch, caplog, debug):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    monkeypatch.setattr(tracebacks, "_buffer", deque(maxlen=50))
    caplog.set_level(logging.DEBUG)
    with override_settings(
        ROOT_URLCONF=__name__,
        LIVEVIEW_ALLOWED_MODULES=["djust.tests"],
        ALLOWED_HOSTS=["testserver", "localhost"],
        DEBUG=debug,
        DJUST_TENANTS=None,
        DJUST_CONFIG={},
    ):
        yield


def _set(monkeypatch, cls, policy, fail_at):
    # Class-level policy: explicit from construction, never flipped mid-session.
    monkeypatch.setattr(cls, "exposure_policy", policy)
    monkeypatch.setattr(EntryView, "fail_at", fail_at)


def _ring():
    return json.dumps(tracebacks.get_recent_tracebacks(50), default=str)


def _observe(caplog, destination):
    return (
        SENTINEL in destination,
        SENTINEL in caplog.text,
        SENTINEL in _ring(),
    )


@pytest.fixture
def signals():
    """Record what ``got_request_exception`` receivers see in ``sys.exc_info()``."""
    seen = []

    def receiver(sender, request=None, **kwargs):
        seen.append(sys.exc_info()[1])

    got_request_exception.connect(receiver)
    try:
        yield seen
    finally:
        got_request_exception.disconnect(receiver)


def _assert_value_free_signal(seen):
    from djust._exposure import ExposureError

    assert len(seen) == 1, seen
    exc = seen[0]
    assert isinstance(exc, ExposureError), repr(exc)
    assert SENTINEL not in str(exc)
    assert exc.__cause__ is None
    assert exc.__context__ is None
    assert exc.__traceback__ is not None


def _assert_generic_500(response, body):
    assert response.status_code == 500, body[:500]
    # Neither Django's technical 500 page nor its frame locals are rendered.
    assert "Traceback" not in body
    assert "Local vars" not in body
    assert "secret_local" not in body


# --------------------------------------------------------------------------- #
# (a) HTTP GET, sync ``get`` and streaming ``aget``; (e) constructor over HTTP
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
@pytest.mark.parametrize("fail_at", ["init", "mount", "hook", "render"])
@pytest.mark.parametrize("policy", POLICIES)
def test_http_get_failure(monkeypatch, caplog, signals, debug, policy, fail_at):
    monkeypatch.setattr(EntryView, "fail_at", fail_at)
    caplog.clear()
    response = Client(raise_request_exception=False).get(f"/entry/{POLICIES.index(policy)}/")
    body = response.content.decode()
    observed = _observe(caplog, body)
    if policy == "legacy":
        # Unchanged: Django's own view callable.
        assert not hasattr(HTTP_VIEWS["legacy"].as_view(), "__wrapped__")
    if debug:
        # Every policy: Django's DEBUG technical 500 page and its log, which
        # carry the value (legacy unchanged; nonlegacy per D-a, revised).
        assert response.status_code == 500
        assert "Traceback" in body
        assert observed == (True, True, False)
        assert len(signals) == 1 and SENTINEL in str(signals[0])
    elif policy == "legacy":
        # Unchanged production legacy: Django's plain 500, value in the log.
        _assert_generic_500(response, body)
        assert observed == (False, True, False)
        assert len(signals) == 1 and SENTINEL in str(signals[0])
    else:
        assert observed == (False, False, False)
        _assert_generic_500(response, body)
        assert STATIC_LOG in caplog.text
        _assert_value_free_signal(signals)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("fail_at", ["init", "mount", "render"])
@pytest.mark.parametrize("policy", POLICIES)
async def test_streaming_http_get_failure(monkeypatch, caplog, signals, debug, policy, fail_at):
    monkeypatch.setattr(EntryView, "fail_at", fail_at)
    caplog.clear()
    response = await AsyncClient(raise_request_exception=False).get(
        f"/stream-entry/{POLICIES.index(policy)}/"
    )
    body = response.content.decode()
    observed = _observe(caplog, body)
    if debug:
        # Every policy: Django's technical 500 page.
        assert response.status_code == 500
        assert "Traceback" in body
        assert observed == (True, True, False)
        assert len(signals) == 1 and SENTINEL in str(signals[0])
    elif policy == "legacy":
        _assert_generic_500(response, body)
        assert observed == (False, True, False)
        assert len(signals) == 1 and SENTINEL in str(signals[0])
    else:
        assert observed == (False, False, False)
        _assert_generic_500(response, body)
        assert STATIC_LOG in caplog.text
        _assert_value_free_signal(signals)


@pytest.mark.django_db
@pytest.mark.parametrize("policy", POLICIES)
def test_http_get_status_exceptions(monkeypatch, caplog, signals, debug, policy):
    """Django's status mapping survives: 400 stays 400, 404 stays 404.

    Under DEBUG Django answers a ``SuspiciousOperation`` with its technical
    page (status 400, frames and locals), for every policy. In production a
    nonlegacy owner gets a plain 400 and a value-free log.
    ``Http404`` keeps Django's 404 handling for every policy.
    """
    url = f"/entry/{POLICIES.index(policy)}/"
    monkeypatch.setattr(EntryView, "fail_at", "mount:bad_request")
    caplog.clear()
    response = Client(raise_request_exception=False).get(url)
    body = response.content.decode()
    assert response.status_code == 400
    if debug:
        assert SENTINEL in body
        assert "Traceback" in body
        assert STATIC_LOG not in caplog.text
    elif policy == "legacy":
        # Unchanged production legacy: Django's plain 400.
        assert SENTINEL not in body
        assert "Traceback" not in body
        assert STATIC_LOG not in caplog.text
    else:
        assert (SENTINEL in body, SENTINEL in caplog.text) == (False, False)
        assert "Traceback" not in body
        assert STATIC_LOG in caplog.text
    assert signals == []

    monkeypatch.setattr(EntryView, "fail_at", "mount:not_found")
    assert Client(raise_request_exception=False).get(url).status_code == 404


# --------------------------------------------------------------------------- #
# (b) SSE stream GET; (c) SSE navigation replacement
# --------------------------------------------------------------------------- #


async def _stream_body(response):
    chunks = []

    async def read():
        async for chunk in response.streaming_content:
            chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode())

    await asyncio.wait_for(read(), timeout=5)
    return b"".join(chunks).decode()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("fail_at", ["init", "hook"])
@pytest.mark.parametrize("policy", POLICIES)
async def test_sse_stream_get_failure(monkeypatch, caplog, signals, debug, policy, fail_at):
    import uuid

    _set(monkeypatch, EntryView, policy, fail_at)
    sid = str(uuid.uuid4())
    caplog.clear()
    try:
        response = await AsyncClient(raise_request_exception=False).get(
            f"/djust/sse/{sid}/", {"view": VIEW_PATH, "_djust_url": "/entry/"}
        )
        if response.streaming:
            body = await _stream_body(response)
        else:
            body = response.content.decode()
    finally:
        _sse_sessions.pop(sid, None)
    observed = _observe(caplog, body)
    if fail_at == "init":
        # The constructor failure is reported on the stream.
        assert response.status_code == 200
        assert '"type": "error"' in body, body
        if debug:
            # Every policy: the detailed DEBUG error frame on the stream.
            assert observed == (True, True, True)
        elif policy == "legacy":
            assert observed == (False, True, True)
        else:
            assert observed == (False, False, False)
            assert STATIC_LOG in caplog.text
        assert signals == []
    elif debug:
        # An exception escaping dispatch_mount reaches Django's technical 500,
        # for every policy.
        assert response.status_code == 500
        assert "Traceback" in body
        assert observed == (True, True, False)
    elif policy == "legacy":
        _assert_generic_500(response, body)
        assert observed == (False, True, False)
    else:
        assert observed == (False, False, False)
        _assert_generic_500(response, body)
        assert STATIC_LOG in caplog.text
        _assert_value_free_signal(signals)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("fail_at", ["init", "hook"])
@pytest.mark.parametrize("policy", POLICIES)
async def test_sse_navigation_failure(monkeypatch, caplog, signals, debug, policy, fail_at):
    from django.conf import settings

    from djust.tests.test_exposure_sse_navigation import drain

    _set(monkeypatch, EntryView, policy, None)
    import uuid

    sid = str(uuid.uuid4())
    client = AsyncClient(raise_request_exception=False)
    try:
        started = await client.get(
            f"/djust/sse/{sid}/", {"view": VIEW_PATH, "_djust_url": "/entry/"}
        )
        assert started.status_code == 200
        session = _sse_sessions[sid]
        assert session.view_instance is not None
        drain(session)
        monkeypatch.setattr(EntryView, "fail_at", fail_at)
        assert client.cookies.get(settings.SESSION_COOKIE_NAME) is not None
        caplog.clear()
        response = await client.post(
            f"/djust/sse/{sid}/message/",
            data=json.dumps({"type": "live_redirect_mount", "url": "/entry/", "params": {}}),
            content_type="application/json",
        )
        body = response.content.decode()
        frames = json.dumps(drain(session))
    finally:
        _sse_sessions.pop(sid, None)
    observed = _observe(caplog, body + frames)
    if fail_at == "init":
        assert response.status_code == 200, body[:500]
        assert '"type": "error"' in frames, frames
        if debug:
            # Every policy: the detailed DEBUG error frame.
            assert observed == (True, True, True)
        elif policy == "legacy":
            assert observed == (False, True, True)
        else:
            assert observed == (False, False, False)
            assert STATIC_LOG in caplog.text
        assert signals == []
    elif debug:
        # Django's technical 500, for every policy.
        assert response.status_code == 500
        assert "Traceback" in body
        assert observed == (True, True, False)
    elif policy == "legacy":
        _assert_generic_500(response, body)
        assert observed == (False, True, False)
    else:
        assert observed == (False, False, False)
        _assert_generic_500(response, body)
        assert STATIC_LOG in caplog.text
        _assert_value_free_signal(signals)


# --------------------------------------------------------------------------- #
# (d) WebSocket receive catch-all; (e) constructor over WebSocket
# --------------------------------------------------------------------------- #


async def _connect():
    from djust.tests.test_exposure_runtime import make_request

    request = await sync_to_async(make_request)()
    socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    socket.scope.update(session=request.session, user=request.user, tenant=None)
    assert (await socket.connect())[0]
    await socket.receive_json_from(timeout=3)
    return socket


async def _frames(socket, expected):
    """The turn's frames: up to the ``expected`` one (event-driven, #3130),
    then whatever else follows before the socket goes quiet. The trailing
    window can only miss an extra frame, never lose the expected one."""
    frames = await receive_type(socket, expected)
    return frames + await drain_extra(socket)


async def _mount(socket):
    await socket.send_json_to({"type": "mount", "view": VIEW_PATH, "url": "/entry/"})
    await _frames(socket, "mount")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["legacy", "explicit"])
async def test_ws_request_html_failure(monkeypatch, caplog, debug, policy):
    # A None or invalid policy fails closed at mount (no server-state adapter),
    # so only explicit reaches a mounted view that can be recovered.
    _set(monkeypatch, EntryView, policy, None)
    socket = await _connect()
    try:
        await _mount(socket)
        await socket.send_json_to({"type": "event", "event": "bump", "params": {}, "ref": 1})
        await _frames(socket, "patch")
        monkeypatch.setattr(EntryView, "fail_at", "recovery")
        caplog.clear()
        await socket.send_json_to({"type": "request_html"})
        frames = await _frames(socket, "error")
        assert [frame["type"] for frame in frames] == ["error"], frames
        observed = _observe(caplog, json.dumps(frames))
        if debug:
            # Every policy: the detailed DEBUG error frame, log and ring.
            assert observed == (True, True, True)
        elif policy == "legacy":
            # Unchanged production legacy: generic frame, value in log and ring.
            assert observed == (False, True, True)
        else:
            assert observed == (False, False, False)
            assert STATIC_LOG in caplog.text
        await socket.send_json_to({"type": "ping"})
        assert (await socket.receive_json_from(timeout=3))["type"] == "pong"
    finally:
        await socket.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("fail_at", ["init", "hook"])
@pytest.mark.parametrize("policy", POLICIES)
async def test_ws_live_redirect_mount_failure(monkeypatch, caplog, debug, policy, fail_at):
    _set(monkeypatch, EntryView, policy, None)
    socket = await _connect()
    try:
        if policy in ("legacy", "explicit"):
            # Navigate away from a mounted page. A None or invalid policy fails
            # closed at mount, so those redirect-mount on a fresh connection.
            await _mount(socket)
        monkeypatch.setattr(EntryView, "fail_at", fail_at)
        caplog.clear()
        await socket.send_json_to(
            {"type": "live_redirect_mount", "view": VIEW_PATH, "url": "/entry/"}
        )
        frames = await _frames(socket, "error")
        observed = _observe(caplog, json.dumps(frames))
        if debug:
            # Every policy: the detailed DEBUG error frame, log and ring.
            assert observed == (True, True, True)
        elif policy == "legacy":
            # Unchanged production legacy: generic frame, value in log and ring.
            assert observed == (False, True, True)
        else:
            assert observed == (False, False, False)
            assert STATIC_LOG in caplog.text
    finally:
        await socket.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("policy", POLICIES)
async def test_ws_mount_batch_constructor_failure(monkeypatch, caplog, debug, policy):
    _set(monkeypatch, EntryView, policy, "init")
    socket = await _connect()
    try:
        caplog.clear()
        await socket.send_json_to(
            {
                "type": "mount_batch",
                "views": [{"view": VIEW_PATH, "url": "/entry/", "target_id": "t1"}],
            }
        )
        frames = await _frames(socket, "mount_batch")
        assert types_of(frames) == ["mount_batch"], frames
        assert [entry["target_id"] for entry in frames[0]["failed"]] == ["t1"]
        observed = _observe(caplog, json.dumps(frames))
        if policy == "legacy" or debug:
            # failed[] reads the frame's "message" key, so only the log and
            # the ring carried the value; under DEBUG, for every policy.
            assert observed == (False, True, True)
        else:
            assert observed == (False, False, False)
            assert STATIC_LOG in caplog.text
    finally:
        await socket.disconnect()
