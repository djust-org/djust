"""Regression tests for #1737 — initial SSR render must apply the SAME
comment/whitespace normalization that ``render_with_diff`` applies, so the
first-hydration ``morphChildren`` is a no-op (no flash).

Root cause (#1737)
------------------
``render_with_diff()`` normalizes the dj-root template via
``_strip_comments_and_whitespace()`` in ``get_template()`` BEFORE the Rust
VDOM renders it, and the Rust ``render_with_diff()`` applies a further
inter-element whitespace pass on the rendered output. The initial-GET path
(``render_full_template`` → ``self._rust_view.render()``) did NEITHER for the
common ``dj-view``-only root: its dj-root replacement keyed on
``_DJ_ROOT_RE`` (literal ``dj-root`` attribute only), so a template declaring
just ``dj-view`` fell through to returning the un-normalized ``_full_template``
shell — comment nodes preserved, as-authored whitespace preserved. The first
WS frame had them stripped. The structural mismatch made the client's
first-hydration ``morphChildren`` rebuild the whole subtree (visible flash),
even with #1724 (client-side whitespace-only-text-node skip) in place.

Fix
---
1. ``render_full_template`` falls back to ``_DJ_VIEW_RE`` when there is no
   literal ``dj-root`` attribute, so the normalized ``liveview_html`` (rendered
   from the SAME ``self._rust_view`` the WS path uses) actually replaces the
   shell's root.
2. ``render_full_template`` applies ``_strip_comments_and_whitespace()`` to the
   rendered ``liveview_html``, mirroring the additional whitespace pass that
   Rust ``render_with_diff()`` does (and that plain Rust ``render()`` does not).
3. ``_strip_comments_and_whitespace`` now collapses whitespace adjacent to
   ``<pre>``/``<code>``/``<textarea>`` boundaries too, matching Rust exactly.

The assertion target is byte-equivalence of the dj-root slice between the two
paths AFTER stripping the ``dj-id`` attrs that the Rust ``render_with_diff()``
stamps (and that the client stamps onto the prerender DOM per #1610). dj-id is
the one intentional, client-reconciled difference; everything structural
(comment nodes, element nesting, inter-element whitespace) must match.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

import pytest
from django.test import override_settings

from djust import LiveView
from djust.utils import clear_template_dirs_cache


_DJ_ID_RE = re.compile(r'\s*dj-id="[^"]*"')


def _strip_dj_id(html: str) -> str:
    """Remove the dj-id attrs that the Rust render_with_diff() / client (#1610)
    stamp — the one intentional difference between the SSR and WS roots."""
    return _DJ_ID_RE.sub("", html)


class _TemplateHarness:
    """Write a base + child template pair to a tmp dir and wire it into the
    Django + Rust template search paths for the duration of a test."""

    def __init__(self, base_src: str, child_src: str):
        self._tmpdir = Path(tempfile.mkdtemp())
        (self._tmpdir / "base_1737.html").write_text(base_src)
        (self._tmpdir / "child_1737.html").write_text(child_src)
        self._override = None

    def __enter__(self):
        from django.conf import settings

        templates = [dict(t) for t in settings.TEMPLATES]
        templates[0] = dict(templates[0])
        templates[0]["DIRS"] = [str(self._tmpdir), *templates[0].get("DIRS", [])]
        self._override = override_settings(TEMPLATES=templates)
        self._override.enable()
        clear_template_dirs_cache()
        return self

    def __exit__(self, *exc):
        if self._override is not None:
            self._override.disable()
        clear_template_dirs_cache()
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        return False


def _make_view_class(name: str, mount_body, ctx_body):
    """Build a fresh LiveView subclass per test (avoids class-level state
    leaking across tests, per the dynamic-subclass discipline #1109)."""

    return type(
        name,
        (LiveView,),
        {
            "template_name": "child_1737.html",
            "mount": lambda self, request, **kwargs: mount_body(self),
            "get_context_data": lambda self, **kwargs: ctx_body(self),
        },
    )


_BASE = (
    "<!DOCTYPE html>\n<html>\n<head><title>{% block title %}T{% endblock %}</title></head>\n"
    "<body>\n<nav>NAV</nav>\n{% block content %}{% endblock %}\n<footer>F</footer>\n</body>\n</html>"
)


def _ssr_and_ws_roots(view_cls):
    """Render the initial-GET dj-root slice and the first-WS-frame dj-root
    slice for the same view, returning both (no dj-id stripping)."""
    v = view_cls()
    v.mount(None)
    v.get_template()  # sets _full_template (mirrors RequestMixin GET path)
    ssr_html = v.render_full_template(None)
    ssr_root = v._extract_liveview_root_with_wrapper(ssr_html)

    v2 = view_cls()
    v2.mount(None)
    ws_html, _patches, _version = v2.render_with_diff(None)
    ws_root = v2._extract_liveview_root_with_wrapper(ws_html)
    return ssr_root, ws_root


class TestSsrRenderNormalizationParity:
    """Initial SSR dj-root must match the first WS frame's dj-root (#1737)."""

    def test_dj_view_root_with_comments_byte_equivalent(self):
        """A dj-view-only root with HTML comments + indentation: the initial
        render's dj-root must be byte-equal to render_with_diff's (modulo
        dj-id), and contain NO comment nodes.

        This is the load-bearing repro: pre-fix, the SSR root kept the
        comments + whitespace and was NOT equal.
        """
        child = (
            '{% extends "base_1737.html" %}\n'
            "{% block content %}\n"
            '<div dj-view="app.MyView">\n'
            "    <!-- a leading comment -->\n"
            '    <div class="card">\n'
            "        <!-- inner comment -->\n"
            "        <span>hello {{ name }}</span>\n"
            "    </div>\n"
            "</div>\n"
            "{% endblock %}\n"
        )
        with _TemplateHarness(_BASE, child):
            cls = _make_view_class(
                "CommentsView",
                lambda self: setattr(self, "name", "world"),
                lambda self: {"name": self.name},
            )
            ssr_root, ws_root = _ssr_and_ws_roots(cls)

        # The structural fix: no comment nodes survive in the SSR root.
        assert "<!--" not in ssr_root, (
            "Initial SSR dj-root still contains HTML comment nodes — the "
            "normalization render_with_diff applies was not mirrored (#1737)."
        )
        # The SSR root's first child must be an element, not a comment node.
        inner = ssr_root[ssr_root.index(">") + 1 :]
        assert not inner.lstrip().startswith("<!--"), (
            "SSR dj-root's first child is a comment node; the client will "
            "morph it away on first hydration (flash)."
        )
        # Byte-equivalence (modulo the client-reconciled dj-id attrs).
        assert _strip_dj_id(ssr_root) == _strip_dj_id(ws_root), (
            "SSR dj-root is not byte-equivalent to the first WS frame's "
            "dj-root after dj-id normalization.\nSSR: %r\nWS:  %r"
            % (_strip_dj_id(ssr_root), _strip_dj_id(ws_root))
        )

    def test_dj_if_blocks_and_pre_preserved_and_equivalent(self):
        """A root with a {% if %} block (dj-if markers) AND a <pre> block:
        dj-if markers + <pre> internal whitespace are preserved, and the SSR
        root is byte-equivalent to the WS frame (modulo dj-id)."""
        child = (
            '{% extends "base_1737.html" %}\n'
            "{% block content %}\n"
            '<div dj-view="app.MyView">\n'
            "    <!-- top comment -->\n"
            "    {% if show %}\n"
            "        <span>shown</span>\n"
            "    {% endif %}\n"
            "    <pre>line1\n    line2\n        line3</pre>\n"
            "</div>\n"
            "{% endblock %}\n"
        )
        with _TemplateHarness(_BASE, child):
            cls = _make_view_class(
                "DjIfPreView",
                lambda self: setattr(self, "show", True),
                lambda self: {"show": self.show},
            )
            ssr_root, ws_root = _ssr_and_ws_roots(cls)

        # dj-if boundary markers preserved on BOTH paths.
        assert "dj-if" in ssr_root and "dj-if" in ws_root, (
            "dj-if boundary markers must survive normalization on both paths."
        )
        # <pre> internal whitespace preserved (NOT collapsed).
        assert "line1\n    line2\n        line3" in ssr_root
        # Plain HTML comment stripped, <pre> kept.
        assert "top comment" not in ssr_root
        # Byte-equivalent (modulo dj-id).
        assert _strip_dj_id(ssr_root) == _strip_dj_id(ws_root), (
            "SSR dj-root not byte-equivalent to WS frame with dj-if + <pre>.\n"
            "SSR: %r\nWS:  %r" % (_strip_dj_id(ssr_root), _strip_dj_id(ws_root))
        )

    def test_textarea_internal_whitespace_preserved_boundary_stripped(self):
        """A <textarea> root child: internal whitespace preserved, but the
        whitespace BETWEEN the tag boundary and the <textarea> is stripped to
        match Rust (#1737)."""
        child = (
            '{% extends "base_1737.html" %}\n'
            "{% block content %}\n"
            '<div dj-view="app.MyView">\n'
            "    <div>\n"
            '        <textarea name="doc">first\n\nlast</textarea>\n'
            "    </div>\n"
            "</div>\n"
            "{% endblock %}\n"
        )
        with _TemplateHarness(_BASE, child):
            cls = _make_view_class(
                "TextareaView",
                lambda self: None,
                lambda self: {},
            )
            ssr_root, ws_root = _ssr_and_ws_roots(cls)

        # Internal newlines preserved.
        assert "first\n\nlast" in ssr_root
        # Boundary whitespace stripped (Rust parity): no whitespace text node
        # between the <div> and the <textarea>.
        assert "<div><textarea" in _strip_dj_id(ssr_root)
        assert _strip_dj_id(ssr_root) == _strip_dj_id(ws_root), (
            "SSR dj-root not byte-equivalent to WS frame with <textarea>.\n"
            "SSR: %r\nWS:  %r" % (_strip_dj_id(ssr_root), _strip_dj_id(ws_root))
        )

    def test_adjacent_preserved_blocks_byte_equivalent(self):
        """Three ADJACENT preserved blocks (<textarea> <pre> <code>) separated
        only by inter-element whitespace: the SSR dj-root must be byte-equal to
        the WS frame (modulo dj-id), each block's INTERNAL whitespace must be
        preserved, and NO whitespace text node must survive between them.

        This pins the preserved↔preserved boundary case the Stage-11 reviewer
        flagged: Part-3's literal-tag↔preserved regexes handled `</div> <pre>`
        but NOT `</textarea> <pre>`, so the SSR path left a stray space while
        Rust render_with_diff() drops it (the parser filters whitespace-only
        text nodes that are direct children of a non-preserving element,
        parser.rs:520-531). Without the fix this assertion fails — the
        "byte-equivalence modulo dj-id" invariant is now actually tested, not
        just claimed.
        """
        child = (
            '{% extends "base_1737.html" %}\n'
            "{% block content %}\n"
            '<div dj-view="app.MyView">\n'
            '    <textarea name="a">ta1\n    ta2</textarea>\n'
            "    <pre>pre1\n        pre2</pre>\n"
            "    <code>code1 code2</code>\n"
            "</div>\n"
            "{% endblock %}\n"
        )
        with _TemplateHarness(_BASE, child):
            cls = _make_view_class(
                "AdjacentPreservedView",
                lambda self: None,
                lambda self: {},
            )
            ssr_root, ws_root = _ssr_and_ws_roots(cls)

        # Each preserved block's INTERNAL whitespace survives.
        assert "ta1\n    ta2" in ssr_root
        assert "pre1\n        pre2" in ssr_root
        # No whitespace text node between adjacent preserved blocks (Rust parity).
        ssr_no_id = _strip_dj_id(ssr_root)
        assert "</textarea><pre>" in ssr_no_id, (
            "Whitespace between adjacent <textarea> and <pre> was NOT collapsed; "
            "SSR diverges from Rust render_with_diff(). Got: %r" % ssr_no_id
        )
        assert "</pre><code>" in ssr_no_id, (
            "Whitespace between adjacent <pre> and <code> was NOT collapsed. Got: %r" % ssr_no_id
        )
        # The load-bearing invariant: byte-equivalence modulo dj-id.
        assert _strip_dj_id(ssr_root) == _strip_dj_id(ws_root), (
            "SSR dj-root not byte-equivalent to WS frame with adjacent "
            "preserved blocks.\nSSR: %r\nWS:  %r" % (_strip_dj_id(ssr_root), _strip_dj_id(ws_root))
        )


class TestStripCommentsAndWhitespacePreservedBoundary:
    """Unit-level pins for the #1737 normalizer alignment (Rust parity)."""

    @pytest.fixture
    def mixin(self):
        from djust.mixins.template import TemplateMixin

        return TemplateMixin()

    def test_whitespace_before_pre_stripped(self, mixin):
        result = mixin._strip_comments_and_whitespace("<div>   <pre>a\nb</pre></div>")
        assert "<div><pre>" in result
        assert "a\nb" in result

    def test_whitespace_after_pre_stripped(self, mixin):
        result = mixin._strip_comments_and_whitespace("<div><pre>a\nb</pre>   </div>")
        assert "</pre></div>" in result
        assert "a\nb" in result

    def test_text_adjacent_to_pre_keeps_single_space(self, mixin):
        """A <pre> adjacent to actual TEXT (not a tag) keeps a single space —
        matching Rust. Only TAG-adjacent whitespace is stripped."""
        result = mixin._strip_comments_and_whitespace("<div>before   <pre>x</pre>   after</div>")
        assert "before <pre>" in result
        assert "</pre> after" in result

    def test_whitespace_between_two_preserved_blocks_collapsed(self, mixin):
        """Whitespace BETWEEN two adjacent preserved blocks is collapsed (#1737,
        Stage-11 finding): `</textarea> <pre>` → `</textarea><pre>`, matching
        Rust render_with_diff()."""
        result = mixin._strip_comments_and_whitespace(
            "<textarea>a\nb</textarea>   \n   <pre>c\nd</pre>"
        )
        assert "</textarea><pre>" in result
        assert "a\nb" in result
        assert "c\nd" in result

    def test_three_adjacent_preserved_blocks_all_gaps_collapsed(self, mixin):
        """A run of 3+ adjacent preserved blocks collapses EVERY gap in one
        pass (lookahead form, not a consuming group)."""
        result = mixin._strip_comments_and_whitespace(
            "<pre>p</pre>  <textarea>t</textarea>  <code>c</code>"
        )
        assert "</pre><textarea>" in result
        assert "</textarea><code>" in result

    def test_text_between_two_preserved_blocks_keeps_single_space(self, mixin):
        """Actual TEXT between two preserved blocks is NOT a whitespace-only
        node — Rust keeps it, so we keep a single space too (no over-collapse)."""
        result = mixin._strip_comments_and_whitespace("<pre>p</pre>  mid  <textarea>t</textarea>")
        assert "</pre> mid <textarea>" in result

    def test_dj_if_marker_survives_with_adjacent_pre(self, mixin):
        html = '<div> <!--dj-if id="if-0"--> <pre>x\ny</pre> <!--/dj-if--> </div>'
        result = mixin._strip_comments_and_whitespace(html)
        assert '<!--dj-if id="if-0"-->' in result
        assert "<!--/dj-if-->" in result
        assert "x\ny" in result
