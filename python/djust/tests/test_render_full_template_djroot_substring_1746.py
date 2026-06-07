"""Regression tests for #1746 — render_full_template must NOT double-render the
whole page when a LiveView's CHILD template merely *mentions* the literal text
``dj-root`` / ``dj-view`` (e.g. inside a ``<code>`` example) while the real
``<div dj-root>`` lives in the BASE template.

Root cause (#1746)
------------------
``python/djust/mixins/template.py`` ``get_template()`` chose the VDOM source
with a NAIVE substring check::

    vdom_source = (
        template_source
        if ("dj-root" in template_source or "dj-view" in template_source)
        else resolved
    )

``"dj-root" in template_source`` matches the token ANYWHERE — including
documentation/example code that merely *displays* ``dj-root`` / ``dj-view`` as
text, or even another word containing it as a substring (``adj-view``). When
the real ``<div dj-root>`` lives in the BASE template (a common layout) and the
child only mentions the tokens in text, this wrongly picks the child as
``vdom_source``; ``_extract_liveview_root_with_wrapper`` finds no real dj-root
there → the VDOM template is wrong → ``render_full_template``'s shell/dj-root
replacement nests the whole page (two ``<!DOCTYPE>`` / two ``<footer>``).

Fix
---
Replace the substring check with the anchored-attribute regexes the rest of the
module already uses (``_DJ_ROOT_RE`` / ``_DJ_VIEW_RE``), which require a REAL
``<div ... dj-root ...>`` tag — so a child that merely displays the token in
text won't match.

This file pins the variant repro table from the issue:

| child content                                  | <!DOCTYPE> count |
|------------------------------------------------|------------------|
| ``<p>hello</p>``                               | 1 (control)      |
| ``<p>dj-view</p>`` (plain text)                | 1 (was 2)        |
| ``<p>dj-root</p>`` (plain text)                | 1 (was 2)        |
| ``<p><code>dj-view</code> <code>dj-root</code>`` | 1 (was 2)      |
| ``<p>adj-view</p>`` (substring)                | 1 (was 2)        |
| ``<p>dj-click</p>`` (other dj-* attr)          | 1 (control)      |

Plus a control proving the GOOD path still works: a child that DOES declare its
own real ``<div dj-root>`` must still render exactly one ``<!DOCTYPE>`` /
``<footer>`` — the fix must not break the legitimate "child declares dj-root"
case the original heuristic was written for.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest
from django.test import override_settings

from djust import LiveView
from djust.utils import clear_template_dirs_cache


class _TemplateHarness:
    """Write a base + child template pair to a tmp dir and wire it into the
    Django + Rust template search paths for the duration of a test."""

    def __init__(self, base_src: str, child_src: str):
        self._tmpdir = Path(tempfile.mkdtemp())
        (self._tmpdir / "base_1746.html").write_text(base_src)
        (self._tmpdir / "child_1746.html").write_text(child_src)
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
    """Build a fresh LiveView subclass per test (avoids class-level state
    leaking across tests, per the dynamic-subclass discipline #1109)."""

    return type(
        name,
        (LiveView,),
        {
            "template_name": "child_1746.html",
            "mount": lambda self, request, **kwargs: None,
            "get_context_data": lambda self, **kwargs: {},
        },
    )


# BASE template owns the real reactive root (<div dj-root> in the BASE);
# single <footer>; single <!DOCTYPE>.
_BASE = (
    "<!DOCTYPE html>\n<html>\n<head><title>T</title></head>\n"
    "<body>\n<nav>NAV</nav>\n"
    "<main><div dj-root>{% block content %}{% endblock %}</div></main>\n"
    "<footer>F</footer>\n</body>\n</html>"
)


def _doctype_and_footer_counts(child_block: str):
    """Render render_full_template(None) for a child that {% extends %} _BASE
    with the given content block, returning (doctype_count, footer_count)."""
    child = (
        '{% extends "base_1746.html" %}\n{% block content %}\n' + child_block + "\n{% endblock %}\n"
    )
    with _TemplateHarness(_BASE, child):
        cls = _make_view_class("View1746")
        v = cls()
        v.mount(None)
        v.get_template()  # sets _full_template (mirrors RequestMixin GET path)
        html = v.render_full_template(None)
    return (html.upper().count("<!DOCTYPE"), html.lower().count("<footer"))


class TestRenderFullTemplateDoesNotDoubleRender:
    """render_full_template must emit exactly ONE <!DOCTYPE>/<footer> regardless
    of whether the child template merely *mentions* dj-root/dj-view as text."""

    @pytest.mark.parametrize(
        "label,child_block",
        [
            ("control_plain", "<p>hello</p>"),
            ("dj_view_text", "<p>dj-view</p>"),
            ("dj_root_text", "<p>dj-root</p>"),
            ("dj_root_view_code", "<p><code>dj-view</code> and <code>dj-root</code></p>"),
            ("substring_adj_view", "<p>adj-view</p>"),
            ("control_dj_click", "<p>dj-click</p>"),
        ],
    )
    def test_single_doctype_and_footer(self, label, child_block):
        doctypes, footers = _doctype_and_footer_counts(child_block)
        assert doctypes == 1, (
            "child=%s: expected exactly ONE <!DOCTYPE> but got %d — the page "
            "was double-rendered because the VDOM source was mis-selected "
            "(#1746)." % (label, doctypes)
        )
        assert footers == 1, "child=%s: expected exactly ONE <footer> but got %d (#1746)." % (
            label,
            footers,
        )

    def test_child_declaring_its_own_real_dj_root_still_works(self):
        """The GOOD path the original heuristic was written for: a child that
        DOES declare its own real ``<div dj-root>`` must still render exactly
        one <!DOCTYPE>/<footer>. The fix must pick ``template_source`` for this
        case (the child has a REAL dj-root)."""
        # Base here has NO dj-root; the child owns the real reactive root.
        base_no_root = (
            "<!DOCTYPE html>\n<html>\n<head><title>T</title></head>\n"
            "<body>\n<nav>NAV</nav>\n{% block content %}{% endblock %}\n"
            "<footer>F</footer>\n</body>\n</html>"
        )
        child = (
            '{% extends "base_1746.html" %}\n'
            "{% block content %}\n"
            "<div dj-root><p>hello {{ nothing }}</p></div>\n"
            "{% endblock %}\n"
        )
        with _TemplateHarness(base_no_root, child):
            cls = _make_view_class("ViewOwnRoot1746")
            v = cls()
            v.mount(None)
            v.get_template()
            html = v.render_full_template(None)
        assert html.upper().count("<!DOCTYPE") == 1, (
            "child declaring its own real <div dj-root> regressed: expected "
            "ONE <!DOCTYPE>, got %d." % html.upper().count("<!DOCTYPE")
        )
        assert html.lower().count("<footer") == 1, (
            "child declaring its own real <div dj-root> regressed: expected "
            "ONE <footer>, got %d." % html.lower().count("<footer")
        )
