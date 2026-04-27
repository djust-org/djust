"""Unit tests for v0.9.0 PR-B: ``{% live_render lazy=True %}`` (ADR-015).

Covers:

1. **Tag-eval validation** — sticky+lazy collision, dict-form key
   validation, trigger/timeout_s/on_error value checks.
2. **Synchronous placeholder emit** — `<dj-lazy-slot data-id data-trigger>`
   shape; child mount NOT called at tag eval time.
3. **Thunk stash** — `parent._lazy_thunks.append(...)` with the right
   tuple shape.
4. **Thunk closure** — eager-render path runs lazily; produces the
   `<template id="djl-fill-X" data-status="ok">...</template><script>`
   envelope.
5. **Error envelope** — child raises → `data-status="error"` template;
   timeout → `data-status="timeout"` template.

Tag-render path is exercised via ``_render_tag``. Thunk closures are
invoked directly via ``await thunk()`` and the returned bytes
inspected.
"""

from __future__ import annotations

import asyncio
from typing import List, Tuple

import pytest
from django.template import Context, Template, TemplateSyntaxError
from django.test import override_settings

from djust import LiveView


# ---------------------------------------------------------------------------
# Fixtures (reuse pattern from test_live_render_tag.py)
# ---------------------------------------------------------------------------


class _LazyChild(LiveView):
    template = "<div><p>Lazy {{ value }}</p></div>"

    def mount(self, request, **kwargs):
        self.value = kwargs.get("value", "default")


class _LazyChildSticky(LiveView):
    """Sticky-enabled child used to exercise the sticky+lazy collision."""

    sticky = True
    sticky_id = "lazy-sticky"
    template = "<div>sticky+lazy collision target</div>"

    def mount(self, request, **kwargs):
        pass


class _LazyChildThatRaises(LiveView):
    template = "<div>never rendered</div>"

    def mount(self, request, **kwargs):
        raise RuntimeError("intentional mount failure")


class _LazyChildThatSleeps(LiveView):
    """Mount that takes longer than the test timeout to trigger
    `data-status="timeout"`. Sync sleep so sync_to_async can be
    cancelled."""

    template = "<div>too late</div>"

    def mount(self, request, **kwargs):
        import time

        time.sleep(0.5)


class _LazyParentView(LiveView):
    template = "{% load live_tags %}<div dj-root></div>"

    def mount(self, request, **kwargs):
        pass


def _render_tag(source: str, context: dict | None = None) -> str:
    full = "{% load live_tags %}" + source
    return Template(full).render(Context(context or {}))


def _make_parent(rf, parent_cls=_LazyParentView):
    request = rf.get("/")
    from django.contrib.auth.models import AnonymousUser

    request.user = AnonymousUser()
    parent = parent_cls()
    parent.request = request
    mount = getattr(parent, "mount", None)
    if callable(mount):
        mount(request)
    return parent


# ---------------------------------------------------------------------------
# 1. Tag-eval validation
# ---------------------------------------------------------------------------


class TestLazyTagValidation:
    def test_sticky_plus_lazy_raises_template_syntax_error(self, rf):
        parent = _make_parent(rf)
        with override_settings(
            DJUST_LIVE_RENDER_ALLOWED_MODULES=["tests.unit.test_live_render_lazy"]
        ):
            with pytest.raises(TemplateSyntaxError, match="mutually exclusive"):
                _render_tag(
                    '{% live_render "tests.unit.test_live_render_lazy._LazyChildSticky" '
                    "sticky=True lazy=True %}",
                    {"view": parent, "request": parent.request},
                )

    def test_lazy_dict_unknown_key_raises(self, rf):
        parent = _make_parent(rf)
        with override_settings(
            DJUST_LIVE_RENDER_ALLOWED_MODULES=["tests.unit.test_live_render_lazy"]
        ):
            with pytest.raises(TemplateSyntaxError, match="unknown key"):
                _render_tag(
                    '{% live_render "tests.unit.test_live_render_lazy._LazyChild" lazy=lazy_cfg %}',
                    {
                        "view": parent,
                        "request": parent.request,
                        "lazy_cfg": {"bogus_key": True},
                    },
                )

    def test_lazy_dict_invalid_trigger_raises(self, rf):
        parent = _make_parent(rf)
        with override_settings(
            DJUST_LIVE_RENDER_ALLOWED_MODULES=["tests.unit.test_live_render_lazy"]
        ):
            with pytest.raises(TemplateSyntaxError, match="trigger"):
                _render_tag(
                    '{% live_render "tests.unit.test_live_render_lazy._LazyChild" lazy=lazy_cfg %}',
                    {
                        "view": parent,
                        "request": parent.request,
                        "lazy_cfg": {"trigger": "scroll"},
                    },
                )

    def test_lazy_dict_invalid_on_error_raises(self, rf):
        parent = _make_parent(rf)
        with override_settings(
            DJUST_LIVE_RENDER_ALLOWED_MODULES=["tests.unit.test_live_render_lazy"]
        ):
            with pytest.raises(TemplateSyntaxError, match="on_error"):
                _render_tag(
                    '{% live_render "tests.unit.test_live_render_lazy._LazyChild" lazy=lazy_cfg %}',
                    {
                        "view": parent,
                        "request": parent.request,
                        "lazy_cfg": {"on_error": "explode"},
                    },
                )

    def test_lazy_dict_invalid_timeout_raises(self, rf):
        parent = _make_parent(rf)
        with override_settings(
            DJUST_LIVE_RENDER_ALLOWED_MODULES=["tests.unit.test_live_render_lazy"]
        ):
            with pytest.raises(TemplateSyntaxError, match="timeout_s"):
                _render_tag(
                    '{% live_render "tests.unit.test_live_render_lazy._LazyChild" lazy=lazy_cfg %}',
                    {
                        "view": parent,
                        "request": parent.request,
                        "lazy_cfg": {"timeout_s": -5},
                    },
                )


# ---------------------------------------------------------------------------
# 2. Synchronous placeholder emit
# ---------------------------------------------------------------------------


class TestLazyPlaceholder:
    def test_lazy_true_emits_dj_lazy_slot(self, rf):
        parent = _make_parent(rf)
        with override_settings(
            DJUST_LIVE_RENDER_ALLOWED_MODULES=["tests.unit.test_live_render_lazy"]
        ):
            out = _render_tag(
                '{% live_render "tests.unit.test_live_render_lazy._LazyChild" lazy=True %}',
                {"view": parent, "request": parent.request},
            )
        assert "<dj-lazy-slot" in out
        assert 'data-trigger="flush"' in out
        # Child not embedded directly — only the placeholder.
        assert "<p>Lazy" not in out

    def test_lazy_visible_emits_visible_trigger(self, rf):
        parent = _make_parent(rf)
        with override_settings(
            DJUST_LIVE_RENDER_ALLOWED_MODULES=["tests.unit.test_live_render_lazy"]
        ):
            out = _render_tag(
                '{% live_render "tests.unit.test_live_render_lazy._LazyChild" lazy="visible" %}',
                {"view": parent, "request": parent.request},
            )
        assert 'data-trigger="visible"' in out

    def test_lazy_with_custom_placeholder(self, rf):
        parent = _make_parent(rf)
        with override_settings(
            DJUST_LIVE_RENDER_ALLOWED_MODULES=["tests.unit.test_live_render_lazy"]
        ):
            out = _render_tag(
                '{% live_render "tests.unit.test_live_render_lazy._LazyChild" lazy=lazy_cfg %}',
                {
                    "view": parent,
                    "request": parent.request,
                    "lazy_cfg": {"placeholder": "<div>Loading...</div>"},
                },
            )
        assert "<div>Loading...</div>" in out

    def test_lazy_does_not_call_mount_at_tag_eval(self, rf):
        """Tag eval registers the thunk but doesn't run mount/render —
        crucial because mount() may be slow and the whole point of lazy
        is to defer."""

        mount_calls = {"n": 0}

        class _MountSpy(LiveView):
            template = "<div>spy</div>"

            def mount(self, request, **kwargs):
                mount_calls["n"] += 1

        # Resolve via dotted path — register at module scope.
        globals()["_MountSpy"] = _MountSpy

        parent = _make_parent(rf)
        with override_settings(
            DJUST_LIVE_RENDER_ALLOWED_MODULES=["tests.unit.test_live_render_lazy"]
        ):
            _render_tag(
                '{% live_render "tests.unit.test_live_render_lazy._MountSpy" lazy=True %}',
                {"view": parent, "request": parent.request},
            )
        assert mount_calls["n"] == 0, "mount() must not run at tag-eval time"


# ---------------------------------------------------------------------------
# 3. Thunk stash on parent
# ---------------------------------------------------------------------------


class TestLazyThunkStash:
    def test_thunk_stashed_on_parent_lazy_thunks(self, rf):
        parent = _make_parent(rf)
        with override_settings(
            DJUST_LIVE_RENDER_ALLOWED_MODULES=["tests.unit.test_live_render_lazy"]
        ):
            _render_tag(
                '{% live_render "tests.unit.test_live_render_lazy._LazyChild" lazy=True %}',
                {"view": parent, "request": parent.request},
            )
        thunks: List[Tuple[str, callable]] = parent._lazy_thunks
        assert len(thunks) == 1
        view_id, thunk_fn = thunks[0]
        assert isinstance(view_id, str) and view_id
        assert callable(thunk_fn)

    def test_two_lazy_calls_stash_two_thunks_in_order(self, rf):
        parent = _make_parent(rf)
        with override_settings(
            DJUST_LIVE_RENDER_ALLOWED_MODULES=["tests.unit.test_live_render_lazy"]
        ):
            _render_tag(
                '{% live_render "tests.unit.test_live_render_lazy._LazyChild" lazy=True %}'
                '{% live_render "tests.unit.test_live_render_lazy._LazyChild" lazy=True %}',
                {"view": parent, "request": parent.request},
            )
        assert len(parent._lazy_thunks) == 2
        assert parent._lazy_thunks[0][0] != parent._lazy_thunks[1][0]


# ---------------------------------------------------------------------------
# 4. Thunk closure produces fill envelope
# ---------------------------------------------------------------------------


class TestLazyThunkInvocation:
    @pytest.mark.asyncio
    async def test_thunk_produces_template_envelope(self, rf):
        parent = _make_parent(rf)
        with override_settings(
            DJUST_LIVE_RENDER_ALLOWED_MODULES=["tests.unit.test_live_render_lazy"]
        ):
            _render_tag(
                '{% live_render "tests.unit.test_live_render_lazy._LazyChild" lazy=True %}',
                {"view": parent, "request": parent.request},
            )
        view_id, thunk_fn = parent._lazy_thunks[0]
        chunk = await thunk_fn()
        assert isinstance(chunk, bytes)
        body = chunk.decode("utf-8")
        assert '<template id="djl-fill-' + view_id in body
        assert 'data-status="ok"' in body
        assert "<p>Lazy default</p>" in body
        assert "window.djust.lazyFill" in body

    @pytest.mark.asyncio
    async def test_thunk_error_envelope_when_mount_raises(self, rf):
        parent = _make_parent(rf)
        with override_settings(
            DJUST_LIVE_RENDER_ALLOWED_MODULES=["tests.unit.test_live_render_lazy"]
        ):
            _render_tag(
                '{% live_render "tests.unit.test_live_render_lazy._LazyChildThatRaises" '
                "lazy=True %}",
                {"view": parent, "request": parent.request},
            )
        view_id, thunk_fn = parent._lazy_thunks[0]
        chunk = await thunk_fn()
        body = chunk.decode("utf-8")
        assert 'data-status="error"' in body
        assert "<dj-error" in body
        assert 'aria-live="polite"' in body
        # The fill envelope still calls lazyFill so the slot is replaced
        # with the error message rather than left as a permanent
        # placeholder.
        assert "window.djust.lazyFill" in body

    @pytest.mark.asyncio
    async def test_thunk_timeout_envelope(self, rf):
        parent = _make_parent(rf)
        with override_settings(
            DJUST_LIVE_RENDER_ALLOWED_MODULES=["tests.unit.test_live_render_lazy"]
        ):
            _render_tag(
                '{% live_render "tests.unit.test_live_render_lazy._LazyChildThatSleeps" '
                "lazy=lazy_cfg %}",
                {
                    "view": parent,
                    "request": parent.request,
                    "lazy_cfg": {"timeout_s": 0.01},
                },
            )
        view_id, thunk_fn = parent._lazy_thunks[0]
        chunk = await asyncio.wait_for(thunk_fn(), timeout=2.0)
        body = chunk.decode("utf-8")
        assert 'data-status="timeout"' in body
        assert "<dj-error" in body
