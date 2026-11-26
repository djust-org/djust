"""#3658 (TC9): the wire-protocol version check and the outdated bundle's self-reload.

A client that sends no ``protocol`` (an old cached bundle, the 1.3 client
among them) or a different one is told to reload:

* WebSocket: a ``reload`` frame, then close 4409;
* SSE stream GET: the same ``reload`` frame, then the stream ends;
* HTTP POST and SSE message POST: 409 with the ``protocol_mismatch`` envelope.

A matching version mounts normally, and ``connect``, ``sse_connect`` and
``mount`` name the server's protocol. The bundle and chunk URLs carry
``?v=<djust version>`` so the reload fetches the new bundle.

The suite-wide conftest sets ``DJUST_ALLOW_MISSING_PROTOCOL``, because most
transport tests build frames without the field; ``strict`` removes it here.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.sessions.backends.cache import SessionStore
from django.test import RequestFactory, override_settings

import djust
from djust import LiveView, event_handler, state
from djust._protocol import (
    PROTOCOL_CLOSE_CODE,
    PROTOCOL_HEADER,
    PROTOCOL_VERSION,
    anote_refusal,
    client_protocol_ok,
    mismatch_envelope,
    note_refusal,
    parse_protocol,
    reload_frame,
)
from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions
from djust.websocket import LiveViewConsumer

from ._ws_frames import receive_until
from .test_exposure_runtime import make_request
from .test_sse_session_binding_f24_f25 import ALLOWED_ORIGIN, _anon_user, _fake_mount_ok

MOD = __name__
NAMESPACE_JS = (
    Path(djust.__file__).resolve().parent / "static" / "djust" / "src" / "00-namespace.js"
)

#: A mount frame as the 1.3 client sends it (03-websocket.js ``mount``): no ``protocol``.
LEGACY_MOUNT = {
    "type": "mount",
    "view": MOD + ".Counter",
    "params": {},
    "url": "/counter/",
    "has_prerendered": False,
    "client_timezone": "UTC",
}


class Counter(LiveView):
    count = state(0)
    template = "<div dj-root><b>{{ count }}</b></div>"

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1


@pytest.fixture(autouse=True)
def _breaker():
    """Each test starts with no refusal noted (the reload breaker's cache)."""
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def strict(settings):
    """The shipped default: a client that names no protocol is refused."""
    if hasattr(settings, "DJUST_ALLOW_MISSING_PROTOCOL"):
        del settings.DJUST_ALLOW_MISSING_PROTOCOL
    return settings


# ---------------------------------------------------------------------------
# The module
# ---------------------------------------------------------------------------


class TestProtocolModule:
    def test_the_breaker_lets_one_reload_through_per_browser(self):
        assert note_refusal("abc", "10.0.0.1") is True
        assert note_refusal("abc", "10.0.0.2") is False  # same session
        assert note_refusal("xyz", "10.0.0.1") is True  # another session
        assert note_refusal(None, "10.0.0.9") is True
        assert note_refusal(None, "10.0.0.9") is False  # no session: by IP
        assert note_refusal(None, None) is True
        assert note_refusal(None, None) is True  # nothing to key on: never trips

    async def test_the_async_breaker_shares_the_window(self):
        assert note_refusal("abc", None) is True
        assert await anote_refusal("abc", None) is False

    def test_a_broken_cache_never_stops_the_reload(self, monkeypatch):
        from django.core.cache import cache

        def boom(*args, **kwargs):
            raise ConnectionError("down")

        monkeypatch.setattr(cache, "add", boom)
        assert note_refusal("abc", None) is True
        assert note_refusal("abc", None) is True

    def test_parse_accepts_ints_and_decimal_strings(self):
        assert parse_protocol(3) == 3
        assert parse_protocol(" 2 ") == 2
        for bad in (None, "", "x", "1.0", True, 1.0, [1], "9" * 12):
            assert parse_protocol(bad) is None, bad

    def test_missing_refused_by_default(self, strict):
        assert client_protocol_ok(None) is False
        assert client_protocol_ok("") is False

    def test_missing_allowed_with_opt_out(self, settings):
        settings.DJUST_ALLOW_MISSING_PROTOCOL = True
        assert client_protocol_ok(None) is True

    def test_a_different_version_is_refused_even_with_the_opt_out(self, settings):
        settings.DJUST_ALLOW_MISSING_PROTOCOL = True
        assert client_protocol_ok(PROTOCOL_VERSION + 1) is False
        assert client_protocol_ok("garbage") is False
        assert client_protocol_ok(PROTOCOL_VERSION) is True
        assert client_protocol_ok(str(PROTOCOL_VERSION)) is True

    def test_frames(self):
        assert reload_frame() == {
            "type": "reload",
            "reason": "protocol",
            "protocol": PROTOCOL_VERSION,
        }
        assert mismatch_envelope() == {
            "type": "error",
            "code": "protocol_mismatch",
            "recoverable": False,
            "error": "This page is out of date. Reload to continue.",
        }
        assert PROTOCOL_CLOSE_CODE == 4409

    def test_python_and_js_constants_agree(self):
        """``window.djust.PROTOCOL`` in 00-namespace.js is ``PROTOCOL_VERSION``."""
        source = NAMESPACE_JS.read_text(encoding="utf-8")
        found = re.findall(r"window\.djust\.PROTOCOL\s*=\s*(\d+)\s*;", source)
        assert found, "00-namespace.js does not set window.djust.PROTOCOL"
        assert [int(n) for n in found] == [PROTOCOL_VERSION]


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------


class _Socket:
    def __init__(self, monkeypatch, session_key=None):
        self.session_key = session_key
        monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
        self.settings = override_settings(
            LIVEVIEW_ALLOWED_MODULES=["djust", MOD],
            DEBUG=False,
            DJUST_TENANTS=None,
            DJUST_CONFIG={},
        )

    async def __aenter__(self):
        self.settings.enable()
        request = await sync_to_async(make_request)(self.session_key)
        self.session_key = request.session.session_key
        self.socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        self.socket.scope.update(session=request.session, user=request.user, tenant=None)
        assert (await self.socket.connect())[0]
        self.connect = await self.socket.receive_json_from()
        return self

    async def __aexit__(self, *exc):
        try:
            await self.socket.disconnect()
        finally:
            self.settings.disable()


def _closed(frames):
    return bool(frames) and frames[-1].get("type") == "websocket.close"


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestWebSocket:
    async def test_connect_names_the_protocol(self, monkeypatch):
        async with _Socket(monkeypatch) as ws:
            assert ws.connect["type"] == "connect"
            assert ws.connect["protocol"] == PROTOCOL_VERSION

    @pytest.mark.parametrize(
        "frame",
        [
            LEGACY_MOUNT,
            {**LEGACY_MOUNT, "protocol": PROTOCOL_VERSION + 1},
            {"type": "mount_batch", "views": []},
            {"type": "live_redirect_mount", "view": MOD + ".Counter", "url": "/counter/"},
        ],
        ids=["legacy-mount", "other-version", "mount_batch", "live_redirect_mount"],
    )
    async def test_an_outdated_client_reloads_and_the_socket_closes_4409(
        self, monkeypatch, strict, frame
    ):
        async with _Socket(monkeypatch) as ws:
            await ws.socket.send_json_to(frame)
            frames = await receive_until(ws.socket, _closed, what="the 4409 close")
        assert frames[0] == reload_frame()
        assert frames[-1] == {"type": "websocket.close", "code": PROTOCOL_CLOSE_CODE}
        assert len(frames) == 2

    async def test_a_second_refusal_within_the_window_closes_without_reload(
        self, monkeypatch, strict
    ):
        """A cached page shell serving the old bundle again must not loop."""
        async with _Socket(monkeypatch) as first:
            await first.socket.send_json_to(LEGACY_MOUNT)
            frames = await receive_until(first.socket, _closed, what="the first close")
        assert frames[0] == reload_frame()
        async with _Socket(monkeypatch, first.session_key) as again:
            await again.socket.send_json_to(LEGACY_MOUNT)
            frames = await receive_until(again.socket, _closed, what="the second close")
        assert frames == [{"type": "websocket.close", "code": PROTOCOL_CLOSE_CODE}]

    async def test_a_matching_client_mounts_and_the_mount_names_the_protocol(
        self, monkeypatch, strict
    ):
        async with _Socket(monkeypatch) as ws:
            await ws.socket.send_json_to({**LEGACY_MOUNT, "protocol": PROTOCOL_VERSION})
            frames = await receive_until(
                ws.socket,
                lambda fs: any(f.get("type") == "mount" for f in fs),
                what="the mount frame",
            )
        mount = next(f for f in frames if f.get("type") == "mount")
        assert mount["protocol"] == PROTOCOL_VERSION
        assert ">0</b>" in mount["html"]


# ---------------------------------------------------------------------------
# SSE
# ---------------------------------------------------------------------------


def _stream_get(sid, **query):
    request = RequestFactory().get(
        f"/djust/sse/{sid}/",
        {"view": MOD + ".Counter", **query},
        HTTP_ORIGIN=ALLOWED_ORIGIN,
        HTTP_HOST="example.com",
        secure=True,
    )
    request.user = _anon_user()
    request.session = SessionStore()
    return request


def _saved_session():
    session = SessionStore()
    session.create()
    return session


async def _first_event(response):
    async for chunk in response.streaming_content:
        text = chunk.decode() if isinstance(chunk, bytes) else chunk
        if text.startswith("data: "):
            return json.loads(text[len("data: ") :])
    raise AssertionError("the stream sent no event")


@pytest.mark.asyncio
class TestSSE:
    @pytest.fixture(autouse=True)
    def _registry(self, settings):
        settings.ALLOWED_HOSTS = ["example.com"]
        _sse_sessions.clear()
        yield
        for session in list(_sse_sessions.values()):
            session.shutdown()
        _sse_sessions.clear()

    @pytest.mark.parametrize("query", [{}, {"protocol": str(PROTOCOL_VERSION + 1)}])
    async def test_an_outdated_stream_get_yields_reload_and_ends(self, strict, query):
        sid = str(uuid.uuid4())
        with patch("djust.runtime.ViewRuntime.dispatch_mount") as mount:
            response = await DjustSSEStreamView().get(_stream_get(sid, **query), session_id=sid)
        mount.assert_not_called()
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/event-stream")
        assert response.content.decode() == f"data: {json.dumps(reload_frame())}\n\n"
        assert sid not in _sse_sessions

    async def test_a_second_refusal_within_the_window_ends_the_stream_empty(self, strict):
        session = await sync_to_async(_saved_session)()
        bodies = []
        for _ in range(2):
            sid = str(uuid.uuid4())
            request = _stream_get(sid)
            request.session = session
            with patch("djust.runtime.ViewRuntime.dispatch_mount") as mount:
                response = await DjustSSEStreamView().get(request, session_id=sid)
            mount.assert_not_called()
            bodies.append(response.content.decode())
        assert bodies == [f"data: {json.dumps(reload_frame())}\n\n", ""]

    async def test_a_matching_stream_get_mounts_and_sse_connect_names_the_protocol(self, strict):
        sid = str(uuid.uuid4())
        with patch("djust.runtime.ViewRuntime.dispatch_mount", new=_fake_mount_ok):
            response = await DjustSSEStreamView().get(
                _stream_get(sid, protocol=str(PROTOCOL_VERSION)), session_id=sid
            )
        assert sid in _sse_sessions
        first = await _first_event(response)
        assert first == {"type": "sse_connect", "session_id": sid, "protocol": PROTOCOL_VERSION}
        _sse_sessions[sid].shutdown()
        await response.streaming_content.aclose()

    @pytest.mark.parametrize("header", [None, str(PROTOCOL_VERSION + 1)])
    async def test_an_outdated_message_post_gets_409(self, strict, header):
        extra = {} if header is None else {"HTTP_X_DJUST_PROTOCOL": header}
        request = RequestFactory().post(
            f"/djust/sse/{uuid.uuid4()}/message/",
            data=json.dumps({"type": "event", "event": "bump", "params": {}}),
            content_type="application/json",
            HTTP_ORIGIN=ALLOWED_ORIGIN,
            HTTP_HOST="example.com",
            secure=True,
            **extra,
        )
        response = await DjustSSEMessageView().post(request, session_id=str(uuid.uuid4()))
        assert response.status_code == 409
        assert json.loads(response.content) == mismatch_envelope()
        assert response[PROTOCOL_HEADER] == str(PROTOCOL_VERSION)

    async def test_a_matching_message_post_passes_the_check(self, strict):
        request = RequestFactory().post(
            "/djust/sse/x/message/",
            data=json.dumps({"type": "event", "event": "bump", "params": {}}),
            content_type="application/json",
            HTTP_ORIGIN=ALLOWED_ORIGIN,
            HTTP_HOST="example.com",
            secure=True,
            HTTP_X_DJUST_PROTOCOL=str(PROTOCOL_VERSION),
        )
        response = await DjustSSEMessageView().post(request, session_id=str(uuid.uuid4()))
        assert response.status_code == 404  # past the check: no such session


# ---------------------------------------------------------------------------
# HTTP POST
# ---------------------------------------------------------------------------


def _http_post(**headers):
    request = RequestFactory().post(
        "/counter/",
        data=json.dumps({"event": "bump", "params": {}}),
        content_type="application/json",
        **headers,
    )
    initial = make_request()
    request.user, request.session, request.tenant = initial.user, initial.session, None
    return request


@pytest.mark.django_db
class TestHTTP:
    @pytest.fixture(autouse=True)
    def _view_setup(self, monkeypatch, settings):
        monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
        settings.LIVEVIEW_ALLOWED_MODULES = ["djust", MOD]

    @pytest.mark.parametrize("header", [None, str(PROTOCOL_VERSION + 1), "x"])
    def test_an_outdated_post_gets_409_and_no_handler_runs(self, strict, header):
        extra = {} if header is None else {"HTTP_X_DJUST_PROTOCOL": header}
        view = Counter()
        with patch.object(Counter, "bump") as bump:
            response = view.post(_http_post(**extra))
        bump.assert_not_called()
        assert response.status_code == 409
        assert json.loads(response.content) == mismatch_envelope()
        assert response[PROTOCOL_HEADER] == str(PROTOCOL_VERSION)

    def test_an_outdated_post_is_noted_for_the_breaker(self, strict):
        """The 409 itself never changes; the refusal counts for the other wires."""
        request = _http_post()
        responses = [Counter().post(request) for _ in range(2)]
        assert [r.status_code for r in responses] == [409, 409]
        assert note_refusal(request.session.session_key, None) is False

    def test_a_matching_post_answers_and_names_the_protocol(self, strict):
        view = Counter()
        response = view.post(_http_post(HTTP_X_DJUST_PROTOCOL=str(PROTOCOL_VERSION)))
        assert response.status_code == 200
        assert response[PROTOCOL_HEADER] == str(PROTOCOL_VERSION)


# ---------------------------------------------------------------------------
# The bundle URL
# ---------------------------------------------------------------------------


class TestBundleUrl:
    def test_versioned_url(self):
        from djust.mixins.post_processing import _versioned_url

        v = djust.__version__
        assert _versioned_url("/static/djust/client-core.min.js") == (
            f"/static/djust/client-core.min.js?v={v}"
        )
        assert _versioned_url("/static/x.js?h=1") == f"/static/x.js?h=1&v={v}"

    @pytest.mark.django_db
    def test_script_and_chunk_urls_carry_the_version(self, settings, monkeypatch):
        from djust.mixins.post_processing import CLIENT_CHUNKS

        monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
        settings.LIVEVIEW_ALLOWED_MODULES = ["djust", MOD]
        request = RequestFactory().get("/counter/")
        initial = make_request()
        request.user, request.session, request.tenant = initial.user, initial.session, None
        response = Counter.as_view()(request)
        html = response.content.decode()
        src = re.search(r'<script src="([^"]*djust/client[^"]*)"', html)
        assert src, html[-2000:]
        assert src.group(1).endswith(f"?v={djust.__version__}")
        chunks = re.search(r'data-djust-chunks="([^"]*)"', html)
        if chunks is not None:  # the core bundle (the default)
            urls = json.loads(chunks.group(1).replace("&quot;", '"'))
            assert set(urls) == set(CLIENT_CHUNKS)
            assert all(u.endswith(f"?v={djust.__version__}") for u in urls.values())
