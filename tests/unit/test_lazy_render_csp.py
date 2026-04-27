"""Tests for #1147 — CSP-nonce-aware activator for ``<dj-lazy-slot>`` fills.

When ``request.csp_nonce`` is set (Django ``django-csp`` middleware
convention), the lazy-fill envelope must propagate the nonce to:

1. the ``<template id="djl-fill-X">`` element, and
2. the inline ``<script>`` activator that calls
   ``window.djust.lazyFill(...)``.

Without this, sites with strict CSP (no ``unsafe-inline``,
``script-src 'nonce-...'``) reject the activator script and
lazy-rendered children silently fail to mount.

When ``request.csp_nonce`` is absent (the common case for sites
without CSP middleware), no nonce attribute is emitted — backward
compatibility for non-CSP sites is preserved.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.template import Context, Template
from django.test import override_settings

from djust import LiveView


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _CspLazyChild(LiveView):
    template = "<div>nonce-aware lazy child</div>"

    def mount(self, request, **kwargs):
        pass


class _CspLazyParentView(LiveView):
    template = "{% load live_tags %}<div dj-root></div>"

    def mount(self, request, **kwargs):
        pass


def _make_request_with_nonce(rf, nonce):
    request = rf.get("/")
    request.user = AnonymousUser()
    if nonce is not None:
        request.csp_nonce = nonce  # type: ignore[attr-defined]
    return request


def _make_parent(rf, nonce=None):
    request = _make_request_with_nonce(rf, nonce)
    parent = _CspLazyParentView()
    parent.request = request
    parent.mount(request)
    return parent


def _render_tag(source: str, context: dict | None = None) -> str:
    full = "{% load live_tags %}" + source
    return Template(full).render(Context(context or {}))


# ---------------------------------------------------------------------------
# 1. Synchronous placeholder emission — ``<dj-lazy-slot>`` carries nonce
# ---------------------------------------------------------------------------


class TestLazyPlaceholderNonce:
    def test_placeholder_carries_nonce_when_set(self, rf):
        """The ``<dj-lazy-slot>`` placeholder carries a ``nonce=`` attr
        when ``request.csp_nonce`` is set, so the activator can read it
        on the client side.
        """
        parent = _make_parent(rf, nonce="abc123")
        with override_settings(
            DJUST_LIVE_RENDER_ALLOWED_MODULES=["tests.unit.test_lazy_render_csp"]
        ):
            out = _render_tag(
                '{% live_render "tests.unit.test_lazy_render_csp._CspLazyChild" lazy=True %}',
                {"view": parent, "request": parent.request},
            )
        assert 'nonce="abc123"' in out

    def test_placeholder_no_nonce_attr_when_absent(self, rf):
        """No ``request.csp_nonce`` → no ``nonce`` attr emitted at all
        (no empty ``nonce=""`` regression)."""
        parent = _make_parent(rf, nonce=None)
        with override_settings(
            DJUST_LIVE_RENDER_ALLOWED_MODULES=["tests.unit.test_lazy_render_csp"]
        ):
            out = _render_tag(
                '{% live_render "tests.unit.test_lazy_render_csp._CspLazyChild" lazy=True %}',
                {"view": parent, "request": parent.request},
            )
        assert "nonce=" not in out

    def test_placeholder_no_nonce_attr_when_empty(self, rf):
        """``request.csp_nonce = ""`` is treated as absent — no nonce attr
        (equivalent to no CSP middleware in use)."""
        parent = _make_parent(rf, nonce="")
        with override_settings(
            DJUST_LIVE_RENDER_ALLOWED_MODULES=["tests.unit.test_lazy_render_csp"]
        ):
            out = _render_tag(
                '{% live_render "tests.unit.test_lazy_render_csp._CspLazyChild" lazy=True %}',
                {"view": parent, "request": parent.request},
            )
        assert "nonce=" not in out


# ---------------------------------------------------------------------------
# 2. Thunk fill envelope — ``<template>`` and inline ``<script>`` carry nonce
# ---------------------------------------------------------------------------


class TestLazyFillEnvelopeNonce:
    @pytest.mark.asyncio
    async def test_envelope_carries_nonce_when_set(self, rf):
        """The fill envelope emitted by the thunk includes ``nonce=`` on
        BOTH the ``<template id=djl-fill-X>`` element AND the inline
        ``<script>`` activator. The script-side nonce is the load-bearing
        one — strict CSP rejects the activator without it."""
        parent = _make_parent(rf, nonce="env-nonce-1")
        with override_settings(
            DJUST_LIVE_RENDER_ALLOWED_MODULES=["tests.unit.test_lazy_render_csp"]
        ):
            _render_tag(
                '{% live_render "tests.unit.test_lazy_render_csp._CspLazyChild" lazy=True %}',
                {"view": parent, "request": parent.request},
            )
        view_id, thunk_fn = parent._lazy_thunks[0]
        chunk = await thunk_fn()
        body = chunk.decode("utf-8")
        # Both elements MUST carry the nonce.
        assert '<template id="djl-fill-' + view_id in body
        assert 'nonce="env-nonce-1"' in body
        # Specifically the script tag carries nonce — the load-bearing
        # case for strict CSP. Verify by counting occurrences (>= 2: one
        # on <template>, one on <script>).
        assert body.count('nonce="env-nonce-1"') >= 2
        # Activator function call still present.
        assert "window.djust.lazyFill" in body

    @pytest.mark.asyncio
    async def test_envelope_no_nonce_attr_when_absent(self, rf):
        """No ``request.csp_nonce`` → envelope emits no ``nonce`` attr
        on either element. Backward-compat for non-CSP sites."""
        parent = _make_parent(rf, nonce=None)
        with override_settings(
            DJUST_LIVE_RENDER_ALLOWED_MODULES=["tests.unit.test_lazy_render_csp"]
        ):
            _render_tag(
                '{% live_render "tests.unit.test_lazy_render_csp._CspLazyChild" lazy=True %}',
                {"view": parent, "request": parent.request},
            )
        view_id, thunk_fn = parent._lazy_thunks[0]
        chunk = await thunk_fn()
        body = chunk.decode("utf-8")
        assert "nonce=" not in body
        # Activator still present and functional.
        assert "window.djust.lazyFill" in body

    @pytest.mark.asyncio
    async def test_nonce_is_html_escaped(self, rf):
        """Even though django-csp generates safe nonces (URL-safe base64),
        defense-in-depth: the framework MUST HTML-escape the nonce when
        interpolating it into attribute context, so a hostile middleware
        substitute can't smuggle attribute-break characters."""
        parent = _make_parent(rf, nonce='ev"il')
        with override_settings(
            DJUST_LIVE_RENDER_ALLOWED_MODULES=["tests.unit.test_lazy_render_csp"]
        ):
            _render_tag(
                '{% live_render "tests.unit.test_lazy_render_csp._CspLazyChild" lazy=True %}',
                {"view": parent, "request": parent.request},
            )
        view_id, thunk_fn = parent._lazy_thunks[0]
        chunk = await thunk_fn()
        body = chunk.decode("utf-8")
        # Raw ``"`` must NOT survive into the attribute context.
        assert 'nonce="ev"il"' not in body
        # Escaped form is fine.
        assert "ev&quot;il" in body or "ev&#34;il" in body
