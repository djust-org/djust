"""Regression tests for the dj-root boundary depth-counter in
``render_full_template`` — multi-line ``<div`` opening tags must be counted (#1749).

Root cause
----------
``python/djust/mixins/template.py`` ``render_full_template`` locates the
``dj-root`` region's matching ``</div>`` by walking the rendered shell and
counting ``<div>`` depth. The opening-tag check originally only recognized two
EXACT byte forms::

    if shell_html[i : i + 5] == "<div " or shell_html[i : i + 5] == "<div>":
        depth += 1

A MULTI-LINE opening tag — ``<div\n  class="...">`` or ``<div\t...>`` — was not
matched, so it was NOT counted as an open. Each missed open under-counted depth,
so a later ``</div>`` drove depth to 0 EARLY: the ``dj-root`` region was closed
before its real end. ``render_full_template`` then spliced the (correct,
full) ``self._rust_view.render()`` output in place of the *truncated* region,
leaving the tail of the original shell content OUTSIDE ``<div dj-root>`` — so
that tail rendered both inside the root (from the rust output) AND as a sibling
after it (the leftover shell). Two consequences:

1. The tail content is DUPLICATED in the initial GET HTML.
2. Because ``dj-navigate`` swaps only the ``[dj-root]`` subtree on navigation,
   the ejected sibling copy is never cleared — it "leaks" onto the next page.

Observed downstream: djust.org ``/examples/`` (demo sections authored as
``<div\n class="demo-section">``) ejected every demo after the first outside
``dj-root``; navigating examples→home left the demos on the home page.

Fix
---
Match ``<div`` followed by ANY tag-boundary char (whitespace, ``>`` or ``/``),
not just the exact ``"<div "`` / ``"<div>"`` forms.
"""

from __future__ import annotations

import shutil
import tempfile
from html.parser import HTMLParser
from pathlib import Path

from django.test import override_settings

from djust import LiveView
from djust.utils import clear_template_dirs_cache


class _TemplateHarness:
    def __init__(self, base_src: str, child_src: str):
        self._tmpdir = Path(tempfile.mkdtemp())
        (self._tmpdir / "base_mlb.html").write_text(base_src)
        (self._tmpdir / "child_mlb.html").write_text(child_src)
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


def _make_view_class(name: str):
    return type(
        name,
        (LiveView,),
        {
            "template_name": "child_mlb.html",
            "mount": lambda self, request, **kwargs: None,
            "get_context_data": lambda self, **kwargs: {},
        },
    )


# BASE owns the real reactive root; single <footer>/<!DOCTYPE>.
_BASE = (
    "<!DOCTYPE html>\n<html>\n<head><title>T</title></head>\n"
    "<body>\n<nav>NAV</nav>\n"
    "<main><div dj-root>{% block content %}{% endblock %}</div></main>\n"
    "<footer>F</footer>\n</body>\n</html>"
)

# Child content authored with MULTI-LINE <div tags (the trigger). The TAIL
# marker sits after several multi-line divs — with the buggy counter it ejects
# (and duplicates) outside dj-root; with the fix it stays inside, once.
_CHILD_MULTILINE = (
    '{% extends "base_mlb.html" %}\n'
    "{% block content %}\n"
    '<div\n  class="card">CARD_ONE</div>\n'
    '<div\n  class="card">CARD_TWO</div>\n'
    '<div\n  class="card">TAIL_MARKER_XYZ</div>\n'
    "{% endblock %}\n"
)


def _render(child_src: str) -> str:
    with _TemplateHarness(_BASE, child_src):
        cls = _make_view_class("ViewMLB")
        v = cls()
        v.mount(None)
        v.get_template()  # sets _full_template (mirrors the GET path)
        return v.render_full_template(None)


class _RootContainmentParser(HTMLParser):
    """Track whether a given marker text appears inside <div dj-root> and
    whether any element is a SIBLING of dj-root inside <main>."""

    def __init__(self):
        super().__init__()
        self.stack = []
        self.dj_root_depth = None
        self.tail_inside = 0
        self.tail_outside = 0
        self._pending_marker = None

    def handle_starttag(self, tag, attrs):
        ad = dict(attrs)
        self.stack.append(tag)
        if tag == "div" and "dj-root" in ad:
            self.dj_root_depth = len(self.stack)

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        elif tag in self.stack:
            while self.stack and self.stack.pop() != tag:
                pass
        if self.dj_root_depth is not None and len(self.stack) < self.dj_root_depth:
            self.dj_root_depth = None

    def handle_data(self, data):
        if "TAIL_MARKER_XYZ" in data:
            if self.dj_root_depth is not None:
                self.tail_inside += 1
            else:
                self.tail_outside += 1


class TestMultilineDivBoundary:
    def test_tail_after_multiline_divs_stays_inside_dj_root(self):
        html = _render(_CHILD_MULTILINE)

        # The marker must appear EXACTLY ONCE in the output — the boundary bug
        # duplicated it (once inside the rust output, once as leftover shell).
        assert html.count("TAIL_MARKER_XYZ") == 1, (
            "TAIL_MARKER_XYZ appears %d times — content after multi-line <div> "
            "tags was ejected/duplicated outside dj-root by the boundary "
            "depth-counter." % html.count("TAIL_MARKER_XYZ")
        )

        # Structurally: the marker must be INSIDE <div dj-root>, never a sibling.
        p = _RootContainmentParser()
        p.feed(html)
        assert p.tail_inside == 1 and p.tail_outside == 0, (
            "TAIL_MARKER_XYZ containment wrong: inside=%d outside=%d — content "
            "rendered OUTSIDE dj-root will leak on dj-navigate." % (p.tail_inside, p.tail_outside)
        )

        # And exactly one document shell (no double-render).
        assert html.upper().count("<!DOCTYPE") == 1
        assert html.lower().count("<footer") == 1

    def test_single_line_divs_unaffected(self):
        """Control: single-line <div ...> content was always handled — keep it
        green so the fix didn't regress the common case."""
        child = (
            '{% extends "base_mlb.html" %}\n'
            "{% block content %}\n"
            '<div class="card">CARD_ONE</div>\n'
            '<div class="card">TAIL_MARKER_XYZ</div>\n'
            "{% endblock %}\n"
        )
        html = _render(child)
        assert html.count("TAIL_MARKER_XYZ") == 1
        p = _RootContainmentParser()
        p.feed(html)
        assert p.tail_inside == 1 and p.tail_outside == 0


class TestFindClosingDivCloseSideWhitespace:
    """The shared boundary scanner ``_find_closing_div_pos`` must tolerate
    whitespace before '>' in a close tag (``</div >`` / ``</div\\n>``) — the
    close-side twin of the #1749 open-side under-count (#1751). A plain
    ``</div>`` match misses those, over-counting depth so the close is never
    found (returns (None, None) → caller falls back / mis-splices)."""

    def _find(self, template, inner_start):
        from djust.mixins.template import TemplateMixin

        return TemplateMixin._find_closing_div_pos(template, inner_start)

    def test_trailing_whitespace_close_tags_are_matched(self):
        # Outer <div> opened at index 0; inner_start just past "<div>".
        # Nested inner div closed by "</div\n>"; outer closed by "</div >".
        tpl = "<div>A<div>B</div\n>C</div >TAIL"
        inner_start = len("<div>")
        close_start, close_end = self._find(tpl, inner_start)
        assert close_start is not None and close_end is not None, (
            "scanner returned (None, None) — whitespace close tags "
            "(</div >, </div\\n>) were not matched."
        )
        # close_end must consume the FULL outer "</div >" (incl. trailing ws),
        # so the splice boundary lands exactly before TAIL.
        assert tpl[close_end:] == "TAIL", (
            "close_end (%d) does not consume the full whitespace close tag; "
            "remainder=%r" % (close_end, tpl[close_end:])
        )

    def test_plain_close_tag_still_works(self):
        # Control: the common no-whitespace form must keep working.
        tpl = "<div>A<div>B</div>C</div>TAIL"
        inner_start = len("<div>")
        close_start, close_end = self._find(tpl, inner_start)
        assert close_start is not None
        assert tpl[close_end:] == "TAIL"
