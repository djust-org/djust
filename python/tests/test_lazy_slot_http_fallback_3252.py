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
    template = "<div><p>Lazy {{ value }}</p></div>"

    def mount(self, request, **kwargs):
        self.value = kwargs.get("value", "default")


class _PageWithLazySlot(LiveView):
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


class _PlainPage(LiveView):
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
