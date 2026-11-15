"""The page-POST fallback fills its lazy slots (#3252).

``{% live_render ... lazy=True %}`` renders a ``<dj-lazy-slot>`` placeholder and
stashes an async thunk that builds a fill envelope — an inert
``<template id="djl-fill-X">`` plus an activator. The envelope needs no
transport (`50-lazy-fill.js` fills from the template, and its DOMContentLoaded
auto-scan covers the activator running before the bundle defines ``lazyFill``),
but only the streaming path ran the thunks. On the plain page render — the
fallback for a browser with neither WebSocket nor ``EventSource`` — the slot
stayed empty although the server could render it.

These pin the fill, and that a page with no lazy slot is untouched.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, override_settings
from unittest.mock import patch

from djust import LiveView


class _LazyChild(LiveView):
    exposure_policy = "legacy"
    template = "<div><p>Lazy {{ value }}</p></div>"

    def mount(self, request, **kwargs):
        self.value = kwargs.get("value", "default")


class _PageWithLazySlot(LiveView):
    exposure_policy = "legacy"
    template = (
        "{% load live_tags %}"
        "<html><body><div dj-root>"
        '{% live_render "' + __name__ + '._LazyChild" lazy=True %}'
        "</div></body></html>"
    )

    def mount(self, request, **kwargs):
        pass


class _ExplicitPage(_PageWithLazySlot):
    exposure_policy = "explicit"


class _ExplicitLazyChild(LiveView):
    exposure_policy = "explicit"
    template = "<div><p>Explicit lazy {{ value }}</p></div>"

    def mount(self, request, **kwargs):
        self.value = "filled"
        self._private_note = "LAZY_PRIVATE_SENTINEL"

    def get_context_data(self, **kwargs):
        return {"value": self.value}


class _ObjectDeniedLazyChild(_ExplicitLazyChild):
    def get_object(self):
        return "the-object"

    def has_object_permission(self, request, obj):
        return False


def _explicit_page(child: type) -> type:
    return type(
        "Page" + child.__name__,
        (LiveView,),
        {
            "exposure_policy": "explicit",
            "template": (
                "{% load live_tags %}<html><body><div dj-root>"
                '{% live_render "' + __name__ + "." + child.__name__ + '" lazy=True %}'
                "</div></body></html>"
            ),
            "mount": lambda self, request, **kwargs: None,
        },
    )


class _PlainPage(LiveView):
    exposure_policy = "legacy"
    template = "<html><body><div dj-root><p>no slots</p></div></body></html>"

    def mount(self, request, **kwargs):
        pass


def _get_request():
    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    return request


def _render(view) -> str:
    view.streaming_render = False  # the page-POST fallback shape
    with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=[__name__]):
        return view.get(_get_request()).content.decode("utf-8")


@pytest.mark.django_db
class TestLazySlotOverHttpFallback:
    def test_the_page_render_fills_its_lazy_slot(self) -> None:
        html = _render(_PageWithLazySlot())

        # The placeholder is still what the DOM carries; the fill replaces it
        # client-side, so both must be present in the response.
        assert "<dj-lazy-slot" in html, "the placeholder must still be emitted"
        assert '<template id="djl-fill-' in html, "the slot must be filled"
        assert "<p>Lazy default</p>" in html, "the child's own HTML must be in the envelope"
        assert 'data-status="ok"' in html
        assert "window.djust.lazyFill" in html, "the activator must be emitted"

    def test_one_placeholder_fills_once_and_mounts_once(self) -> None:
        """`get()` renders the template twice; the slot must still fill once.

        The page render and the VDOM-diff baseline both run the template, so
        the tag stashes its thunk twice under the same id. Filling both would
        emit two envelopes for one placeholder and mount the child twice
        (found in review of #3412).
        """
        mounts: list[object] = []
        original = _LazyChild.mount

        def counting_mount(inner_self, request, **kwargs):
            mounts.append(inner_self)
            return original(inner_self, request, **kwargs)

        with patch.object(_LazyChild, "mount", counting_mount):
            html = _render(_PageWithLazySlot())

        assert len(mounts) == 1, "the child must mount once per rendered slot"
        assert html.count('<template id="djl-fill-') == 1, "one placeholder, one envelope"

    def test_an_explicit_exposure_parent_also_fills_once(self) -> None:
        """The axis that broke a dedupe-by-slot-id repair (found in review).

        An explicit-exposure parent scopes child ids per render, so the page
        pass and the VDOM-diff baseline assign *different* ids — a `seen` set
        cannot see the duplicate, and the child mounts twice. The fix keeps the
        page pass and drops the baseline's thunks, so this pins that.
        """
        mounts: list[object] = []
        original = _LazyChild.mount

        def counting_mount(inner_self, request, **kwargs):
            mounts.append(inner_self)
            return original(inner_self, request, **kwargs)

        with patch.object(_LazyChild, "mount", counting_mount):
            html = _render(_ExplicitPage())

        assert len(mounts) == 1, "the child must mount once under an explicit parent too"
        assert html.count('<template id="djl-fill-') == 1, "one placeholder, one envelope"

    def test_a_page_without_lazy_slots_is_unchanged(self) -> None:
        html = _render(_PlainPage())

        assert "<p>no slots</p>" in html
        assert "djl-fill-" not in html, "no envelope must be added to a page with no slot"
        assert "window.djust.lazyFill" not in html


def _render_explicit(view) -> str:
    """``_render`` with ``setup()`` first, as ``as_view()`` does: an explicit
    child's identity is bound to the page's request."""
    request = _get_request()
    view.setup(request)
    view.streaming_render = False
    with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=[__name__]):
        return view.get(request).content.decode("utf-8")


@pytest.mark.django_db
class TestExplicitLazyChildOverHttp:
    """#4154: an explicit child's lazy slot fills on both HTTP paths -- the
    plain page render (the page-POST fallback) and the streaming GET -- and an
    unauthorized one is refused in its envelope while the page renders."""

    def test_the_page_render_fills_an_explicit_lazy_child(self) -> None:
        html = _render_explicit(_explicit_page(_ExplicitLazyChild)())

        assert html.count('<template id="djl-fill-') == 1
        assert 'data-status="ok"' in html
        assert "<p>Explicit lazy filled</p>" in html
        assert "LAZY_PRIVATE_SENTINEL" not in html

    def test_the_page_render_refuses_a_denied_explicit_lazy_child(self) -> None:
        html = _render_explicit(_explicit_page(_ObjectDeniedLazyChild)())

        assert 'data-status="error"' in html and "denied access" in html
        assert "Explicit lazy" not in html

    @pytest.mark.asyncio
    async def test_the_streaming_get_fills_an_explicit_lazy_child(self) -> None:
        from asgiref.sync import sync_to_async

        from django.test import AsyncRequestFactory

        def asgi_request():
            # An ASGIRequest: a WSGI one makes aget() fall back to get().
            request = AsyncRequestFactory().get("/")
            request.user = AnonymousUser()
            SessionMiddleware(lambda r: None).process_request(request)
            request.session.save()
            return request

        view = _explicit_page(_ExplicitLazyChild)()
        view.streaming_render = True
        request = await sync_to_async(asgi_request)()
        view.setup(request)
        with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=[__name__]):
            response = await view.aget(request)
            assert response.streaming, "the streaming GET must answer with a streamed response"
            if response.is_async:
                chunks = [chunk async for chunk in response.streaming_content]
            else:
                chunks = await sync_to_async(list)(response.streaming_content)
        html = b"".join(c if isinstance(c, bytes) else c.encode() for c in chunks).decode()

        assert html.count('<template id="djl-fill-') == 1, html
        assert 'data-status="ok"' in html and "<p>Explicit lazy filled</p>" in html
        assert "LAZY_PRIVATE_SENTINEL" not in html
