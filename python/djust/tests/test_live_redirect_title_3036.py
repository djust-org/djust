"""#3036: live navigation updates the tab title from the destination page.

``dj-navigate`` / ``live_redirect`` mounts the destination view and swaps only
the ``dj-root``, so the tab kept the PREVIOUS page's ``<title>`` unless the
new view set ``page_title``. The destination's rendered ``<head><title>``
(``{% block title %}``) is now sent through the existing ``page_metadata``
title frame when the view queued no title of its own.
"""

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings

from djust import LiveView
from djust.runtime import document_title, navigation_title
from djust.websocket import LiveViewConsumer

SETTINGS = dict(DEBUG=False, DJUST_TENANTS=None, DJUST_CONFIG={})


def _page(title: str, body: str) -> str:
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{title}</title></head><body>"
        f"<div dj-root>{body}</div></body></html>"
    )


class SourcePage(LiveView):
    template = _page("Home", "<p>home</p>")


class DocsPage(LiveView):
    template = _page("Docs &amp; {{ section }}", "<p>docs</p>")

    def mount(self, request, **kwargs):
        self.section = "Guides"


class OwnTitlePage(LiveView):
    template = _page("Template title", "<p>own</p>")

    def mount(self, request, **kwargs):
        self.page_title = "Set by the view"


class NoHeadPage(LiveView):
    template = "<div dj-root><svg><title>icon</title></svg></div>"


class TestDocumentTitle:
    def test_reads_the_head_title_and_decodes_entities(self):
        assert document_title(_page("A &amp; B", "")) == "A & B"

    def test_collapses_whitespace(self):
        assert document_title(_page("\n  Docs\n   page  ", "")) == "Docs page"

    def test_ignores_a_body_svg_title(self):
        assert document_title("<div dj-root><svg><title>icon</title></svg></div>") is None
        html = "<html><head></head><body><svg><title>icon</title></svg></body></html>"
        assert document_title(html) is None

    def test_empty_or_missing(self):
        assert document_title(None) is None
        assert document_title("") is None
        assert document_title(_page("   ", "")) is None


def _templates(tmp_path, files):
    for name, body in files.items():
        (tmp_path / name).write_text(body)
    return [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [str(tmp_path)],
            "APP_DIRS": False,
            "OPTIONS": {},
        }
    ]


BASE = (
    "<!DOCTYPE html><html><head><title>{% block title %}Site{% endblock %}</title>"
    "</head><body>{% block content %}{% endblock %}</body></html>"
)


class _Named:
    """Minimal stand-in: what ``navigation_title`` reads off a mounted view."""

    template = None

    def __init__(self, template_name, **attrs):
        self.template_name = template_name
        self._prev_context_immutables = {}
        self.__dict__.update(attrs)


class TestNavigationTitle:
    def test_extends_with_a_title_block(self, tmp_path):
        child = (
            '{% extends "base.html" %}{% block title %}Docs: {{ section|upper }}{% endblock %}'
            "{% block content %}<div dj-root>x</div>{% endblock %}"
        )
        with override_settings(
            TEMPLATES=_templates(tmp_path, {"base.html": BASE, "c.html": child})
        ):
            assert navigation_title(_Named("c.html", section="guides")) == "Docs: GUIDES"

    def test_values_come_from_the_last_render_first(self, tmp_path):
        child = '{% extends "base.html" %}{% block title %}{{ n }} items{% endblock %}'
        view = _Named("c2.html")
        view._prev_context_immutables = {"n": 3}
        with override_settings(
            TEMPLATES=_templates(tmp_path, {"base.html": BASE, "c2.html": child})
        ):
            assert navigation_title(view) == "3 items"

    def test_block_super_is_left_alone(self, tmp_path):
        """Flattened inheritance loses the parent block, so the result would be
        a different wrong title: leave the title as it was."""
        child = '{% extends "base.html" %}{% block title %}Docs | {{ block.super }}{% endblock %}'
        with override_settings(
            TEMPLATES=_templates(tmp_path, {"base.html": BASE, "c3.html": child})
        ):
            assert navigation_title(_Named("c3.html")) is None

    def test_an_unknown_variable_is_left_alone(self, tmp_path):
        child = '{% extends "base.html" %}{% block title %}{{ site_name }}{% endblock %}'
        with override_settings(
            TEMPLATES=_templates(tmp_path, {"base.html": BASE, "c4.html": child})
        ):
            assert navigation_title(_Named("c4.html")) is None

    def test_a_missing_template_never_raises(self):
        assert navigation_title(_Named("does-not-exist.html")) is None


def _session():
    session = SessionStore()
    session.save()
    return session


async def _drain(socket):
    frames = []
    while not await socket.receive_nothing(timeout=0.3):
        frames.append(await socket.receive_json_from(timeout=3))
    return frames


async def _navigate(target: str):
    """Mount SourcePage, live-redirect to ``target``; return the frames the
    redirect produced up to and including its mount frame plus the next
    few (page_metadata goes out after the mount frame)."""
    socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    socket.scope.update(session=await sync_to_async(_session)(), user=AnonymousUser(), tenant=None)
    assert (await socket.connect())[0]
    await socket.receive_json_from(timeout=3)
    try:
        await socket.send_json_to(
            {"type": "mount", "view": f"{__name__}.SourcePage", "url": "/src/"}
        )
        assert (await socket.receive_json_from(timeout=3))["type"] == "mount"
        await socket.send_json_to(
            {
                "type": "live_redirect_mount",
                "view": f"{__name__}.{target}",
                "url": "/dest/",
                "params": {},
            }
        )
        return await _drain(socket)
    finally:
        await socket.disconnect()


def _titles(frames):
    return [
        f["value"]
        for f in frames
        if f.get("type") == "page_metadata" and f.get("action") == "title"
    ]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_redirect_sends_the_destination_document_title():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
        frames = await _navigate("DocsPage")
    assert any(f.get("type") == "mount" for f in frames), frames
    assert _titles(frames) == ["Docs & Guides"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_view_page_title_wins_over_the_template_title():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
        frames = await _navigate("OwnTitlePage")
    assert _titles(frames) == ["Set by the view"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_template_without_a_head_title_sends_no_title():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
        frames = await _navigate("NoHeadPage")
    assert any(f.get("type") == "mount" for f in frames), frames
    assert _titles(frames) == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_initial_mount_sends_no_title_frame():
    """The HTTP-rendered document already has the right title; only a live
    redirect needs the frame."""
    socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    socket.scope.update(session=await sync_to_async(_session)(), user=AnonymousUser(), tenant=None)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
        assert (await socket.connect())[0]
        await socket.receive_json_from(timeout=3)
        try:
            await socket.send_json_to(
                {"type": "mount", "view": f"{__name__}.DocsPage", "url": "/dest/"}
            )
            frames = await _drain(socket)
        finally:
            await socket.disconnect()
    assert any(f.get("type") == "mount" for f in frames)
    assert _titles(frames) == []
