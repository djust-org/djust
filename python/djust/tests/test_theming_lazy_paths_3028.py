"""The lazy ``theme_context`` on every path a LiveView page takes (#3028).

Each case renders through the lazy processor and through the pre-#3028 processor
(``_eager_theme_context_3028``, a verbatim copy) and requires the same bytes, then
checks the unused-page case renders no chunk: HTTP GET page shell, the HTTP POST
fallback, the streaming ``aget`` and a WebSocket mount plus two events.
"""

from __future__ import annotations

import asyncio
import copy
import itertools
import json
import re
from unittest.mock import patch

import pytest
from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust.decorators import event_handler
from djust.theming import context_processors as cp
from djust.theming.manager import ThemeManager
from djust.theming.templatetags import theme_tags

from .test_theming_lazy_context_3028 import (
    OLD_PROCESSOR,
    PROCESSOR,
    _clear_template_caches,
)

pytestmark = [pytest.mark.theming, pytest.mark.django_db]

PROCESSORS = {"lazy": PROCESSOR, "old": OLD_PROCESSOR}


def _mask(text: str) -> str:
    return re.sub(r'(csrf-token" content=")[^"]*', r"\1X", text)


def _templates(processor):
    templates = copy.deepcopy(settings.TEMPLATES)
    processors = templates[0]["OPTIONS"]["context_processors"]
    processors[processors.index(PROCESSOR)] = processor
    return templates


def _fresh(processor):
    from djust.tests import _eager_theme_context_3028 as old_module

    _clear_template_caches()
    cp.clear_theme_context_cache()
    old_module.clear_theme_context_cache()
    return override_settings(TEMPLATES=_templates(processor), ALLOWED_HOSTS=["*"])


def _presets():
    from djust.theming._registry_accessor import get_registry

    return sorted(get_registry().list_presets())[:5]


class HttpUsed(LiveView):
    template = (
        "<html><head>{{ theme_head }}</head><body><div dj-root>{{ theme_switcher }}|"
        "{{ theme_panel }}|{{ count }}|{% if theme_mode_toggle %}T{% endif %}|"
        "{{ theme_preset_selector|length }}</div></body></html>"
    )

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def inc(self, **kwargs):
        self.count += 1


class HttpUnused(LiveView):
    template = "<html><body><div dj-root>C{{ count }}</div></body></html>"

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def inc(self, **kwargs):
        self.count += 1


class StreamUsed(LiveView):
    streaming_render = True
    template = (
        "<html><head>{{ theme_head }}</head><body><div dj-root>"
        "[{{ theme_panel }}]|{{ count }}</div></body></html>"
    )

    def mount(self, request, **kwargs):
        self.count = 0


class StreamUnused(LiveView):
    streaming_render = True
    template = "<html><body><div dj-root>n{{ count }}</div></body></html>"

    def mount(self, request, **kwargs):
        self.count = 0


def _get(view_cls, processor, preset, mode, nonce):
    with _fresh(processor):
        request = RequestFactory().get("/")
        session = SessionStore()
        session.create()
        session[ThemeManager(request=None)._session_key] = {"mode": mode}
        request.session = session
        request.COOKIES["djust_theme_preset"] = preset
        if nonce:
            request.csp_nonce = nonce
        request.user = AnonymousUser()
        response = view_cls.as_view()(request)
        if hasattr(response, "render"):
            response.render()
        return _mask(response.content.decode())


class TestHttpGetShell:
    def test_the_page_shell_is_byte_identical_for_every_preset_mode_and_nonce(self):
        count = 0
        for preset, mode, nonce in itertools.product(_presets(), ["light", "dark"], [None, "xyz"]):
            lazy = _get(HttpUsed, PROCESSORS["lazy"], preset, mode, nonce)
            old = _get(HttpUsed, PROCESSORS["old"], preset, mode, nonce)
            assert lazy == old, (preset, mode, nonce)
            count += 1
        assert count == len(_presets()) * 4
        assert "<style" in lazy and "&lt;script" not in lazy
        assert 'nonce="xyz"' in lazy

    def test_an_unused_page_renders_no_chunk_and_matches_the_old_output(self):
        with patch.object(theme_tags, "theme_head", wraps=theme_tags.theme_head) as head:
            lazy = _get(HttpUnused, PROCESSORS["lazy"], "default", "light", "q")
        assert head.call_count == 0
        assert lazy == _get(HttpUnused, PROCESSORS["old"], "default", "light", "q")


def _post(view_cls, processor):
    from .test_exposure_runtime import make_request

    with _fresh(processor):
        request = make_request()
        request.csp_nonce = "pn"
        get_response = view_cls().get(request)
        post = RequestFactory().post(
            request.path,
            data=json.dumps({}),
            content_type="application/json",
            HTTP_X_DJUST_EVENT="inc",
        )
        post.user, post.session, post.tenant = request.user, request.session, None
        post.csp_nonce = "pn"
        response = view_cls().post(post)
        return (
            _mask(get_response.content.decode()),
            response.status_code,
            json.loads(response.content),
        )


class TestPostFallback:
    @pytest.mark.parametrize("view_cls", [HttpUsed, HttpUnused])
    def test_get_then_post_match_the_old_processor(self, view_cls):
        lazy = _post(view_cls, PROCESSORS["lazy"])
        old = _post(view_cls, PROCESSORS["old"])
        assert lazy[0] == old[0]
        assert lazy[1] == old[1] == 200
        assert json.dumps(lazy[2], sort_keys=True) == json.dumps(old[2], sort_keys=True)

    def test_an_unused_post_renders_no_chunk(self):
        with patch.object(theme_tags, "theme_head", wraps=theme_tags.theme_head) as head:
            _post(HttpUnused, PROCESSORS["lazy"])
        assert head.call_count == 0


def _stream(view_cls, processor, monkeypatch):
    from .test_exposure_runtime import make_request

    with _fresh(processor):
        request = make_request()
        request.csp_nonce = "sn"
        view = view_cls()
        monkeypatch.setattr(view, "_is_asgi_context", lambda request=None: True)
        view.setup(request)
        response = asyncio.run(view.aget(request))
        if hasattr(response, "streaming_content"):

            async def collect():
                out = b""
                async for chunk in response.streaming_content:
                    out += chunk
                return out

            try:
                body = asyncio.run(collect())
            except Exception:  # noqa: BLE001 - a sync iterator, not an async one
                body = b"".join(response.streaming_content)
        else:
            body = response.content
        return _mask(body.decode())


class TestStreamingAget:
    @pytest.mark.parametrize("view_cls", [StreamUsed, StreamUnused])
    def test_the_streamed_body_matches_the_old_processor(self, view_cls, monkeypatch):
        lazy = _stream(view_cls, PROCESSORS["lazy"], monkeypatch)
        old = _stream(view_cls, PROCESSORS["old"], monkeypatch)
        assert lazy == old
        assert "&lt;script" not in lazy

    def test_an_unused_stream_renders_no_chunk(self, monkeypatch):
        with patch.object(theme_tags, "theme_head", wraps=theme_tags.theme_head) as head:
            _stream(StreamUnused, PROCESSORS["lazy"], monkeypatch)
        assert head.call_count == 0


class WsUsed(LiveView):
    template = (
        '<div dj-view="djust.tests.test_theming_lazy_paths_3028.WsUsed" dj-id="0">'
        "[{{ theme_head }}]|[{{ theme_switcher }}]|[{{ theme_panel }}]|{{ count }}"
        "|{% if theme_mode_toggle %}T{% endif %}</div>"
    )

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1


class WsUnused(LiveView):
    template = (
        '<div dj-view="djust.tests.test_theming_lazy_paths_3028.WsUnused" dj-id="0">'
        "C{{ count }}</div>"
    )

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1


async def _drive(view_name):
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    from .test_ws_send_version_1788 import _receive_until

    def make_key():
        session = SessionStore()
        session.create()
        return session.session_key

    key = await sync_to_async(make_key)()

    class Session:
        session_key = key

    communicator = WebsocketCommunicator(
        LiveViewConsumer.as_asgi(), "/ws/", headers=[(b"origin", b"http://testserver")]
    )
    communicator.scope["session"] = Session()
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from(timeout=2)
    await communicator.send_json_to(
        {"type": "mount", "view": f"{__name__}.{view_name}", "url": "/"}
    )
    frames = [await _receive_until(communicator, "mount")]
    for ref in (1, 2):
        await communicator.send_json_to(
            {"type": "event", "event": "bump", "params": {}, "ref": ref}
        )
        frames.append(await _receive_until(communicator, "patch"))
    await communicator.disconnect()
    return frames


@pytest.mark.asyncio
class TestWebSocketMountAndEvents:
    @pytest.mark.parametrize("view_name", ["WsUsed", "WsUnused"])
    async def test_mount_html_and_patches_match_the_old_processor(self, view_name):
        results = {}
        for label, processor in PROCESSORS.items():
            with _fresh(processor), override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
                with patch.object(theme_tags, "theme_head", wraps=theme_tags.theme_head) as head:
                    results[label] = (await _drive(view_name), head.call_count)
        lazy_frames, lazy_head_calls = results["lazy"]
        old_frames, old_head_calls = results["old"]
        assert (lazy_frames[0].get("html") or "") == (old_frames[0].get("html") or "")
        for lazy_frame, old_frame in zip(lazy_frames[1:], old_frames[1:]):
            assert json.dumps(lazy_frame.get("patches")) == json.dumps(old_frame.get("patches"))
        if view_name == "WsUsed":
            assert "&lt;script" not in (lazy_frames[0].get("html") or "")
            assert lazy_head_calls >= 1
        else:
            assert lazy_head_calls == 0 and old_head_calls >= 1
