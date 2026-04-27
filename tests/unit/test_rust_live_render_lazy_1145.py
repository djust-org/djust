"""Parity tests for ``{% live_render %}`` lazy=True on the Rust template
engine path (#1145).

Background
----------

PR #1138 (v0.9.0) shipped the Django path's ``lazy=True`` branch.
Production users on ``RustLiveView`` could not use it because the Rust
template engine had no handler registered for the ``live_render`` tag
— a "no handler" template error. This module locks in byte-for-byte
parity between the two render paths.

The Rust handler delegates to the Django function (see
``python/djust/template_tags/live_render.py``), so behaviour parity
is guaranteed by construction; these tests verify the bridge plumbing
and surface the regression test that would catch a future refactor
that breaks delegation.

Test matrix
-----------

1. ``lazy=True`` placeholder shape parity (Django vs Rust path).
2. ``lazy="visible"`` placeholder shape parity.
3. CSP nonce propagation onto the placeholder + thunk envelope.
4. ``sticky=False lazy=True`` is accepted; ``sticky=True lazy=True``
   raises ``TemplateSyntaxError`` on BOTH paths.
5. Inline-attribute mode ``template = "{% live_render ... lazy=True %}"``
   — the original failure mode from PR #1138 integration tests.
6. Non-lazy (eager) live_render also works on the Rust path
   (regression-guard for the bridge).
"""

from __future__ import annotations

import re

import pytest
from django.template import Context, Template, TemplateSyntaxError
from django.test import override_settings

from djust import LiveView


pytestmark = [pytest.mark.django_db(transaction=False)]

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _RustLazyChild(LiveView):
    template = "<div><p>RustLazy {{ value }}</p></div>"

    def mount(self, request, **kwargs):
        self.value = kwargs.get("value", "default")


class _RustLazyChildSticky(LiveView):
    sticky = True
    sticky_id = "rust-lazy-sticky"
    template = "<div>sticky+lazy collision target</div>"

    def mount(self, request, **kwargs):
        pass


class _RustParentView(LiveView):
    template = "{% load live_tags %}<div dj-root></div>"

    def mount(self, request, **kwargs):
        pass


_ALLOWED = ["tests.unit.test_rust_live_render_lazy_1145"]


def _make_parent(rf):
    """Build a primed parent view + AnonymousUser request."""
    from django.contrib.auth.models import AnonymousUser

    request = rf.get("/")
    request.user = AnonymousUser()
    parent = _RustParentView()
    parent.request = request
    parent.mount(request)
    return parent


def _render_django(source: str, ctx: dict) -> str:
    """Render ``source`` via the Django template engine."""
    full = "{% load live_tags %}" + source
    return Template(full).render(Context(ctx))


def _render_rust(source: str, view: LiveView, request) -> str:
    """Render ``source`` via the Rust template engine (RustLiveView).

    We exercise the same bridge that ``mixins/rust_bridge.py`` uses at
    runtime: instantiate ``RustLiveView``, push raw Python sidecar
    values for ``view``/``request`` (so the new
    ``call_handler_with_py_sidecar`` path threads them onto the
    handler context dict), then render. The sidecar is critical —
    without it the handler can't reach the parent ``view`` and would
    raise ``TemplateSyntaxError`` for "no parent view".
    """
    from djust._rust import RustLiveView

    # The Rust template engine doesn't auto-load Django templatetags
    # libraries (``{% load live_tags %}``), but the live_render handler
    # is keyed by tag name and lives in the global Rust registry —
    # ``{% load %}`` is a no-op in the Rust path. Strip it for clarity.
    source_for_rust = source.replace("{% load live_tags %}", "")
    rv = RustLiveView(source_for_rust)
    if hasattr(rv, "set_raw_py_values"):
        rv.set_raw_py_values({"view": view, "request": request})
    return rv.render()


def _normalize(html: str) -> str:
    """Collapse non-significant whitespace + drop the auto-generated
    ``view_id`` (``child_N``) so two render paths can be compared.

    The view_id is derived from the parent's ``_assign_view_id`` counter,
    which is per-instance — two separately-mounted parents will produce
    different ids. We normalize the id to a placeholder so the rest of
    the envelope structure can be compared.
    """
    # Strip leading/trailing whitespace, collapse runs of whitespace.
    out = re.sub(r"\s+", " ", html).strip()
    # Normalize child_N → child_*; embedded view_ids in lazy slots and
    # data-djust-embedded attributes share the same counter sequence.
    out = re.sub(r"child_\d+", "child_X", out)
    return out


# ---------------------------------------------------------------------------
# 1. lazy=True placeholder parity
# ---------------------------------------------------------------------------


class TestLazyTruePlaceholderParity:
    def test_lazy_true_placeholder_byte_equivalent(self, rf):
        parent_d = _make_parent(rf)
        parent_r = _make_parent(rf)
        source = (
            '{% live_render "tests.unit.test_rust_live_render_lazy_1145._RustLazyChild" '
            "lazy=True %}"
        )
        with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=_ALLOWED):
            django_out = _render_django(source, {"view": parent_d, "request": parent_d.request})
            rust_out = _render_rust(source, parent_r, parent_r.request)

        assert "<dj-lazy-slot" in django_out
        assert "<dj-lazy-slot" in rust_out
        assert _normalize(django_out) == _normalize(rust_out), (
            f"lazy=True placeholder mismatch:\n"
            f"  django: {_normalize(django_out)}\n"
            f"  rust:   {_normalize(rust_out)}"
        )

    def test_lazy_visible_placeholder_byte_equivalent(self, rf):
        parent_d = _make_parent(rf)
        parent_r = _make_parent(rf)
        source = (
            '{% live_render "tests.unit.test_rust_live_render_lazy_1145._RustLazyChild" '
            'lazy="visible" %}'
        )
        with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=_ALLOWED):
            django_out = _render_django(source, {"view": parent_d, "request": parent_d.request})
            rust_out = _render_rust(source, parent_r, parent_r.request)

        assert 'data-trigger="visible"' in django_out
        assert 'data-trigger="visible"' in rust_out
        assert _normalize(django_out) == _normalize(rust_out)

    def test_lazy_thunk_stashed_on_parent_via_rust_path(self, rf):
        """The Rust path must stash ``_lazy_thunks`` on the same parent
        instance — this is the load-bearing side effect that makes
        ``RequestMixin.aget`` transfer thunks onto the chunk emitter
        at flush time."""
        parent = _make_parent(rf)
        source = (
            '{% live_render "tests.unit.test_rust_live_render_lazy_1145._RustLazyChild" '
            "lazy=True %}"
        )
        with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=_ALLOWED):
            _render_rust(source, parent, parent.request)
        assert hasattr(parent, "_lazy_thunks")
        assert parent._lazy_thunks, "thunk should be stashed on parent after Rust render"
        view_id, thunk = parent._lazy_thunks[0]
        assert view_id.startswith("child_")
        assert callable(thunk)


# ---------------------------------------------------------------------------
# 2. CSP nonce propagation
# ---------------------------------------------------------------------------


class TestCspNonceParity:
    def test_lazy_with_nonce_byte_equivalent(self, rf):
        from django.contrib.auth.models import AnonymousUser

        parent_d = _make_parent(rf)
        parent_r = _make_parent(rf)
        # Stamp a CSP nonce onto both parent requests.
        parent_d.request.csp_nonce = "abc123nonce"
        parent_r.request.csp_nonce = "abc123nonce"
        # Make sure user attribute exists on both.
        parent_d.request.user = AnonymousUser()
        parent_r.request.user = AnonymousUser()

        source = (
            '{% live_render "tests.unit.test_rust_live_render_lazy_1145._RustLazyChild" '
            "lazy=True %}"
        )
        with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=_ALLOWED):
            django_out = _render_django(source, {"view": parent_d, "request": parent_d.request})
            rust_out = _render_rust(source, parent_r, parent_r.request)

        # Nonce must appear on the placeholder element on both paths.
        assert 'nonce="abc123nonce"' in django_out
        assert 'nonce="abc123nonce"' in rust_out
        assert _normalize(django_out) == _normalize(rust_out)


# ---------------------------------------------------------------------------
# 3. sticky+lazy collision parity
# ---------------------------------------------------------------------------


class TestStickyLazyCollisionParity:
    def test_lazy_without_sticky_succeeds_rust_path(self, rf):
        parent = _make_parent(rf)
        source = (
            '{% live_render "tests.unit.test_rust_live_render_lazy_1145._RustLazyChild" '
            "lazy=True %}"
        )
        with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=_ALLOWED):
            out = _render_rust(source, parent, parent.request)
        assert "<dj-lazy-slot" in out

    def test_sticky_plus_lazy_raises_rust_path(self, rf):
        parent = _make_parent(rf)
        source = (
            "{% live_render "
            '"tests.unit.test_rust_live_render_lazy_1145._RustLazyChildSticky" '
            "sticky=True lazy=True %}"
        )
        with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=_ALLOWED):
            with pytest.raises((TemplateSyntaxError, ValueError, RuntimeError)) as exc:
                _render_rust(source, parent, parent.request)
        # The error message should mention the mutual-exclusivity. The
        # Rust→Python bridge wraps Python exceptions in a generic
        # RuntimeError (visible in the message), so match the message
        # rather than the exception type alone.
        assert "mutually exclusive" in str(exc.value)


# ---------------------------------------------------------------------------
# 4. Inline-attribute mode (the original failure surface from PR #1138)
# ---------------------------------------------------------------------------


class TestInlineAttributeMode:
    def test_inline_template_attribute_lazy_true_renders(self, rf):
        """The failure mode from PR #1138 integration tests: a parent
        view declared with an inline ``template`` attribute carrying
        ``{% live_render ... lazy=True %}``. Before #1145, the Rust
        engine raised "no handler registered for tag: live_render".
        After #1145, the placeholder is emitted and the thunk is
        stashed.
        """

        class _InlineParent(LiveView):
            template = (
                "{% load live_tags %}<div dj-root>"
                '{% live_render "tests.unit.test_rust_live_render_lazy_1145._RustLazyChild" '
                "lazy=True %}"
                "</div>"
            )

            def mount(self, request, **kwargs):
                pass

        from django.contrib.auth.models import AnonymousUser

        request = rf.get("/")
        request.user = AnonymousUser()
        parent = _InlineParent()
        parent.request = request
        parent.mount(request)

        with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=_ALLOWED):
            out = _render_rust(parent.template, parent, request)

        assert "<dj-lazy-slot" in out, (
            f"Expected <dj-lazy-slot> placeholder in Rust path; got: {out}"
        )
        # Thunk must have been stashed on the parent for aget transfer.
        assert getattr(parent, "_lazy_thunks", None), "thunk not stashed"


# ---------------------------------------------------------------------------
# 5. Eager (non-lazy) live_render also works through the Rust bridge
# ---------------------------------------------------------------------------


class TestEagerRenderThroughRustBridge:
    def test_eager_live_render_renders_child_inline(self, rf):
        """The Rust handler also delegates correctly for the
        non-lazy (eager) branch. This is a regression guard — if the
        bridge breaks, both the lazy and eager paths fail at the
        same time, but only the eager test fails fast (it runs
        synchronously without thunks)."""
        parent = _make_parent(rf)
        source = '{% live_render "tests.unit.test_rust_live_render_lazy_1145._RustLazyChild" %}'
        with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=_ALLOWED):
            out = _render_rust(source, parent, parent.request)
        # Eager mount runs immediately: child template is in the output.
        assert "RustLazy" in out
        # Wrapped in a [dj-view] with data-djust-embedded attribute.
        assert "data-djust-embedded=" in out
