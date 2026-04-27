"""Integration tests for v0.9.0 PR-B: lazy=True end-to-end through the
chunk emitter pipeline.

Drives:

  Django template render with `{% live_render lazy=True %}` placeholders
    → parent._lazy_thunks captured during sync render
    → emitter.register_thunk(...) transfer (mimics RequestMixin.aget)
    → arender_chunks(full_html, emitter) phase 1-5
    → consumer drains the async iterator

Asserts:

1. The full chunk schedule arrives: shell-open, body-open, body-content
   with `<dj-lazy-slot>` placeholders, body-close, lazy-fill template(s).
2. Lazy fills land AFTER `</body></html>` (chunk-4) per ADR-015 §"Wire
   format" decision.
3. Multiple lazy children produce one fill template each, distinct
   `data-id` attributes.

The WSGI fallback path (placeholders survive, no fills emitted because
arender_chunks doesn't run) is verified at the as_view-dispatch level
in higher-stack integration tests; covering it here would duplicate
that signal without adding new coverage of the chunk emitter pipeline.
"""

from __future__ import annotations

import asyncio

import pytest
from django.template import Context, Template
from django.test import override_settings

from djust import LiveView
from djust.http_streaming import ChunkEmitter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _LazyChildA(LiveView):
    template = '<div class="child-a"><p>Child A content {{ value }}</p></div>'

    def mount(self, request, **kwargs):
        self.value = kwargs.get("v", "default-a")


class _LazyChildB(LiveView):
    template = '<div class="child-b"><span>Child B</span></div>'

    def mount(self, request, **kwargs):
        pass


class _LazyParentView(LiveView):
    """Parent template with lazy children embedded — rendered via the
    Django template engine to bypass the Rust engine's tag-handler
    requirement (Rust engine doesn't know `{% live_render %}`)."""

    template = (
        "{% load live_tags %}"
        "<!DOCTYPE html><html><head><title>lazy</title></head><body>"
        "<div dj-root>"
        "<h1>Lazy Demo</h1>"
        '<section>{% live_render "tests.integration.test_lazy_streaming_flow._LazyChildA" lazy=True %}</section>'
        '<section>{% live_render "tests.integration.test_lazy_streaming_flow._LazyChildB" lazy=True %}</section>'
        "</div>"
        "</body></html>"
    )

    def mount(self, request, **kwargs):
        pass


def _build_request(rf):
    request = rf.get("/")
    from django.contrib.auth.models import AnonymousUser

    request.user = AnonymousUser()
    return request


def _render_parent_with_lazies(parent, request):
    """Render the parent's template via Django (not Rust) so
    `{% live_render lazy=True %}` runs and stashes thunks on the parent.

    Mirrors the production path's sync render phase (the part that
    happens inside ``sync_to_async(self.get)`` before chunk emission).
    """
    return Template(parent.template).render(Context({"view": parent, "request": request}))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLazyEndToEnd:
    @pytest.mark.asyncio
    async def test_lazy_children_emit_fill_envelopes_after_body_close(self, rf):
        """Full pipeline: sync render stashes thunks, transfer puts them
        on the emitter, arender_chunks phase 5 invokes them. Fill
        envelopes land AFTER `</body></html>` per ADR §"Wire format"."""
        parent = _LazyParentView()
        request = _build_request(rf)
        parent.request = request
        parent._lazy_thunks = []

        with override_settings(
            DJUST_LIVE_RENDER_ALLOWED_MODULES=["tests.integration.test_lazy_streaming_flow"]
        ):
            full_html = _render_parent_with_lazies(parent, request)

        # Sanity — both placeholders are in the rendered HTML.
        assert full_html.count("<dj-lazy-slot") == 2
        assert "<p>Child A" not in full_html  # not yet rendered
        assert "<span>Child B" not in full_html

        # Two thunks stashed on parent during sync render.
        assert len(parent._lazy_thunks) == 2

        # Mimic RequestMixin.aget's transfer: copy thunks onto emitter.
        emitter = ChunkEmitter(request)
        for view_id, thunk_fn in parent._lazy_thunks:
            emitter.register_thunk(view_id, thunk_fn)

        # Drive arender_chunks + drain the consumer concurrently.
        async def _drain():
            chunks = []
            async for chunk in emitter:
                chunks.append(chunk)
            return chunks

        consumer = asyncio.create_task(_drain())
        await parent.arender_chunks(full_html, emitter)
        await emitter.close()
        chunks = await consumer

        full_body = b"".join(chunks)

        # Wire-format assertions.
        assert b"<!DOCTYPE" in full_body
        assert full_body.count(b"<dj-lazy-slot") == 2
        # Each child gets a fill envelope. The activator script
        # references ``window.djust.lazyFill`` twice (existence check +
        # invocation) so count the unique slot ids instead.
        assert full_body.count(b'<template id="djl-fill-') == 2
        assert full_body.count(b"</script>") == 2
        # Both children's eager content appears in the fill envelopes.
        assert b"Child A content default-a" in full_body
        assert b"<span>Child B</span>" in full_body

        # Ordering: </body></html> arrives BEFORE any fill template.
        body_close_pos = full_body.find(b"</body></html>")
        first_fill_pos = full_body.find(b'<template id="djl-fill-')
        assert body_close_pos > 0
        assert first_fill_pos > 0
        assert body_close_pos < first_fill_pos, (
            "lazy fills must land after </body></html> per ADR-015 wire format"
        )

    @pytest.mark.asyncio
    async def test_lazy_thunk_failure_emits_error_envelope(self, rf):
        """A child that raises in mount produces a `data-status="error"`
        envelope so the slot resolves to a `<dj-error>` rather than
        leaving a permanent placeholder."""

        class _FailingChild(LiveView):
            template = "<div>never</div>"

            def mount(self, request, **kwargs):
                raise ValueError("intentional")

        # Module-level binding so the dotted path resolves.
        globals()["_FailingChild"] = _FailingChild

        class _FailingParent(LiveView):
            template = (
                "{% load live_tags %}"
                "<!DOCTYPE html><html><body><div dj-root>"
                '{% live_render "tests.integration.test_lazy_streaming_flow._FailingChild" lazy=True %}'
                "</div></body></html>"
            )

        parent = _FailingParent()
        request = _build_request(rf)
        parent.request = request
        parent._lazy_thunks = []

        with override_settings(
            DJUST_LIVE_RENDER_ALLOWED_MODULES=["tests.integration.test_lazy_streaming_flow"]
        ):
            full_html = _render_parent_with_lazies(parent, request)

        emitter = ChunkEmitter(request)
        for view_id, thunk_fn in parent._lazy_thunks:
            emitter.register_thunk(view_id, thunk_fn)

        async def _drain():
            return [c async for c in emitter]

        consumer = asyncio.create_task(_drain())
        await parent.arender_chunks(full_html, emitter)
        await emitter.close()
        chunks = await consumer

        full_body = b"".join(chunks)
        assert b'data-status="error"' in full_body
        assert b"<dj-error" in full_body
        assert b'aria-live="polite"' in full_body
