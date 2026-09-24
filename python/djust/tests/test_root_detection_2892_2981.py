"""Root-element detection: #2981 and #2892 (v1.2.1-3).

#2981 — ``dj-view`` was stamped onto the page root by a literal
``html.replace("<div dj-root>", ...)``. Any other spelling
(``<div dj-root class="x">``, ``<div class="x" dj-root>``, ``<section dj-root>``)
got no ``dj-view``, so the client found nothing to mount and silently fell
back to HTTP POST for every event.

#2892 — the root-locating regexes and the closing-tag scanner only knew
``<div>``. A root on ``<main>`` / ``<section>`` / ``<article>`` skipped the
initial-GET normalisation, so the HTTP render and the first WS frame described
different trees and every patch fell back to recovery HTML.

The parity tests extract the root with a test-local regex, NOT with the
framework's own ``_extract_liveview_root_with_wrapper`` — comparing two outputs
of the helper under test would pass even if the helper were broken the same way
on both sides.
"""

from __future__ import annotations

import logging
import re
import shutil
import tempfile
from pathlib import Path

import pytest
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust.mixins import template as template_mod
from djust.mixins.template import TemplateMixin
from djust.utils import clear_template_dirs_cache

_DJ_ID_RE = re.compile(r'\s*dj-id="[^"]*"')


def _strip_dj_id(html: str) -> str:
    return _DJ_ID_RE.sub("", html)


# ---------------------------------------------------------------------------
# #2981 — dj-view stamping on the HTTP GET path
# ---------------------------------------------------------------------------


def _get(view_cls) -> str:
    from django.contrib.sessions.backends.db import SessionStore

    request = RequestFactory().get("/x/")
    request.session = SessionStore()
    request.session.create()
    response = view_cls.as_view()(request)
    if hasattr(response, "render"):
        response.render()
    return response.content.decode()


def _view(name: str, template: str):
    return type(
        name,
        (LiveView,),
        {
            "template": template,
            "__module__": __name__,
            "mount": lambda self, request, **kwargs: setattr(self, "n", 1),
        },
    )


def _path(cls) -> str:
    return f"{__name__}.{cls.__name__}"


@pytest.mark.django_db
class TestDjViewStamping2981:
    def test_dj_root_with_class_after_is_stamped(self):
        cls = _view("ClassAfter", '<div dj-root class="search">n={{ n }}</div>')
        html = _get(cls)
        assert f'<div dj-root dj-view="{_path(cls)}" class="search">' in html, html

    def test_dj_root_with_class_before_is_stamped(self):
        cls = _view("ClassBefore", '<div class="search" dj-root>n={{ n }}</div>')
        html = _get(cls)
        assert f'<div class="search" dj-root dj-view="{_path(cls)}">' in html, html

    def test_dj_root_on_section_is_stamped(self):
        cls = _view("SectionRoot", "<section dj-root>n={{ n }}</section>")
        html = _get(cls)
        assert f'<section dj-root dj-view="{_path(cls)}">' in html, html

    def test_dj_root_with_empty_value_is_stamped_after_the_value(self):
        cls = _view("EmptyValue", '<div dj-root="">n={{ n }}</div>')
        html = _get(cls)
        assert f'<div dj-root="" dj-view="{_path(cls)}">' in html, html

    def test_literal_div_dj_root_output_is_unchanged(self):
        """Back-compat: the one spelling that worked must render byte-identically."""
        cls = _view("Literal", "<div dj-root>n={{ n }}</div>")
        html = _get(cls)
        assert f'<div dj-root dj-view="{_path(cls)}">' in html, html

    def test_explicit_dj_view_is_not_doubled(self):
        cls = _view("Explicit", '<div dj-root dj-view="some.Other" class="x">n={{ n }}</div>')
        html = _get(cls)
        assert html.count("dj-view=") == 1, html
        assert 'dj-view="some.Other"' in html

    def test_exactly_one_dj_view_on_the_root(self):
        cls = _view("OneOnly", '<div dj-root class="a b" id="r">n={{ n }}</div>')
        html = _get(cls)
        tag = re.search(r"<div[^>]*\bdj-root\b[^>]*>", html).group(0)
        assert tag.count("dj-view=") == 1, tag

    def test_lookalike_attributes_are_not_stamped(self):
        """``data-dj-root`` / ``dj-rooted`` are not the root marker."""
        html = TemplateMixin._stamp_dj_view(
            '<p data-dj-root="1"></p><p dj-rooted></p><div dj-root>x</div>', "a.B"
        )
        assert html == '<p data-dj-root="1"></p><p dj-rooted></p><div dj-root dj-view="a.B">x</div>'

    def test_dj_root_text_inside_script_is_not_rewritten(self):
        """A ``<div dj-root>`` inside a script body is text, not markup (#2663)."""
        src = "<div dj-root><script>// see <div dj-root> in base</script>x</div>"
        out = TemplateMixin._stamp_dj_view(src, "a.B")
        assert (
            out == '<div dj-root dj-view="a.B"><script>// see <div dj-root> in base</script>x</div>'
        )

    # --- stamp placement: attribute NAMES only (review of #2981) -----------

    def test_dj_root_inside_an_attribute_value_is_never_stamped(self):
        """Stored-XSS regression: autoescape leaves spaces, ``=`` and ``(``
        alone, so a user value containing `` dj-root `` must not receive the
        stamp — its quotes would close the value and turn the rest of the
        user's text into live attributes."""
        cls = _view("XssProbe", '<input value="{{ q }}"><div dj-root>n={{ n }}</div>')
        cls.mount = lambda self, request, **kw: (
            setattr(self, "n", 1),
            setattr(self, "q", "x dj-root autofocus onfocus=alert(1) y"),
        )
        html = _get(cls)
        assert '<input value="x dj-root autofocus onfocus=alert(1) y">' in html, html
        assert html.count("dj-view=") == 1, html
        assert f'<div dj-root dj-view="{_path(cls)}">' in html, html

    def test_class_value_mentioning_dj_root_does_not_move_the_stamp(self):
        out = TemplateMixin._stamp_dj_view('<div class="dj-root-shell" dj-root>x</div>', "a.B")
        assert out == '<div class="dj-root-shell" dj-root dj-view="a.B">x</div>'

    def test_lookalike_name_before_the_real_attribute(self):
        out = TemplateMixin._stamp_dj_view("<div dj-rooted dj-root>x</div>", "a.B")
        assert out == '<div dj-rooted dj-root dj-view="a.B">x</div>'

    def test_dj_view_inside_a_value_does_not_suppress_the_stamp(self):
        out = TemplateMixin._stamp_dj_view('<div dj-root title="the dj-view attr">x</div>', "a.B")
        assert out == '<div dj-root dj-view="a.B" title="the dj-view attr">x</div>'

    def test_single_quoted_and_unquoted_values(self):
        out = TemplateMixin._stamp_dj_view("<div title='a dj-root b' dj-root=yes>x</div>", "a.B")
        assert out == "<div title='a dj-root b' dj-root=yes dj-view=\"a.B\">x</div>"

    def test_view_path_is_escaped(self):
        out = TemplateMixin._stamp_dj_view("<div dj-root>x</div>", 'a."B')
        assert out == '<div dj-root dj-view="a.&quot;B">x</div>'


# ---------------------------------------------------------------------------
# #2892 — non-<div> roots get the same normalisation as <div> roots
# ---------------------------------------------------------------------------


class _TemplateHarness:
    def __init__(self, base_src: str, child_src: str):
        self._tmpdir = Path(tempfile.mkdtemp())
        (self._tmpdir / "base_2892.html").write_text(base_src)
        (self._tmpdir / "child_2892.html").write_text(child_src)
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


def _extends_view(name: str):
    return type(
        name,
        (LiveView,),
        {
            "template_name": "child_2892.html",
            "mount": lambda self, request, **kwargs: setattr(self, "items", ["a", "b"]),
            "get_context_data": lambda self, **kwargs: {"items": self.items},
        },
    )


_BASE = (
    "<!DOCTYPE html>\n<html>\n<head><title>T</title></head>\n"
    "<body>\n<nav>NAV</nav>\n{% block content %}{% endblock %}\n<footer>F</footer>\n</body>\n</html>"
)


def _root_slice(html: str, tag: str) -> str:
    """Test-local extraction: first ``<tag ... dj-...>`` through its LAST
    ``</tag>`` before ``<footer>`` (roots in these fixtures do not nest the
    same tag except where the test says so)."""
    start = re.search(r"<%s\b[^>]*\bdj-(?:root|view)\b[^>]*>" % tag, html).start()
    end = html.rindex("</%s>" % tag, 0, html.index("<footer>")) + len("</%s>" % tag)
    return html[start:end]


def _inner(root_html: str) -> str:
    return root_html[root_html.index(">") + 1 : root_html.rindex("</")]


def _ssr_and_ws(cls, tag: str):
    v = cls()
    v.mount(None)
    v.get_template()
    ssr = v.render_full_template(None)

    v2 = cls()
    v2.mount(None)
    ws_html, _patches, _version = v2.render_with_diff(None)
    return ssr, ws_html


_CHILD_BODY = (
    "    <!-- a comment the WS frame strips -->\n"
    "    <ul>\n"
    "        {% for i in items %}<li>{{ i }}</li>{% endfor %}\n"
    "    </ul>\n"
    "    <p>after</p>\n"
)


@pytest.mark.parametrize(
    "open_tag,tag",
    [
        ('<main dj-view="app.V">', "main"),
        ('<section dj-root class="x">', "section"),
        ('<article class="y" dj-root dj-view="app.V">', "article"),
    ],
)
def test_non_div_root_initial_get_matches_ws_frame(open_tag, tag):
    child = (
        '{% extends "base_2892.html" %}\n{% block content %}\n'
        + open_tag
        + "\n"
        + _CHILD_BODY
        + "</%s>\n{%% endblock %%}\n" % tag
    )
    with _TemplateHarness(_BASE, child):
        ssr, ws_html = _ssr_and_ws(_extends_view("NonDiv_" + tag), tag)

    ssr_root = _root_slice(ssr, tag)
    # The WS frame is the root fragment itself.
    assert ws_html.lstrip().lower().startswith("<" + tag), ws_html[:80]
    assert "<nav>" not in ws_html, "VDOM must be the root, not the whole document"
    assert "<!--" not in ssr_root, "initial GET kept a comment the WS frame strips"
    # Compare the root's CHILDREN: the VDOM serialiser orders the root's own
    # attributes differently (``dj-root=""``), which the client never
    # compares — patches address the subtree.
    assert _strip_dj_id(_inner(ssr_root)) == _strip_dj_id(_inner(ws_html.strip())), (
        ssr_root,
        ws_html,
    )
    # The shell around the root survives.
    assert "<nav>NAV</nav>" in ssr and "<footer>F</footer>" in ssr


def test_nested_same_name_element_is_balanced():
    child = (
        '{% extends "base_2892.html" %}\n{% block content %}\n'
        "<section dj-root>\n  <!-- c -->\n  <section><p>inner</p></section>\n  <p>tail</p>\n</section>\n"
        "{% endblock %}\n"
    )
    with _TemplateHarness(_BASE, child):
        ssr, ws_html = _ssr_and_ws(_extends_view("NestedSection"), "section")
    assert "<p>tail</p></section>" in _strip_dj_id(ws_html)
    assert _strip_dj_id(_inner(_root_slice(ssr, "section"))) == _strip_dj_id(
        _inner(ws_html.strip())
    )
    assert "<nav>" not in ws_html
    assert "<!--" not in ssr


def test_body_attribute_lookalike_does_not_hijack_the_root():
    """``<body dj-view-transitions>`` is a different attribute (#2892 relaxed
    the tag, not the attribute-name boundary)."""
    base = _BASE.replace("<body>", "<body dj-view-transitions>")
    child = (
        '{% extends "base_2892.html" %}\n{% block content %}\n<main dj-view="app.V">\n'
        + _CHILD_BODY
        + "</main>\n{% endblock %}\n"
    )
    with _TemplateHarness(base, child):
        ssr, ws_html = _ssr_and_ws(_extends_view("BodyLookalike"), "main")
    assert ws_html.lstrip().startswith("<main"), ws_html[:80]
    assert "<body dj-view-transitions>" in ssr
    assert "<!--" not in _root_slice(ssr, "main")


def test_unsupported_root_on_body_warns_once(caplog):
    """A root the normaliser cannot use (``<body dj-root>``) fails loudly."""
    base = _BASE.replace("<body>", "<body dj-root>")
    child = '{% extends "base_2892.html" %}\n{% block content %}<p>x</p>{% endblock %}\n'
    template_mod._UNMATCHED_ROOT_WARNED.clear()
    with _TemplateHarness(base, child):
        cls = _extends_view("BodyRoot")
        with caplog.at_level(logging.WARNING, logger="djust.mixins.template"):
            for _ in range(2):
                v = cls()
                v.mount(None)
                v.get_template()
                v.render_full_template(None)
    hits = [r for r in caplog.records if "#2892" in r.getMessage()]
    assert len(hits) == 1, [r.getMessage() for r in caplog.records]
    assert "BodyRoot" in hits[0].getMessage()


def test_page_without_any_root_does_not_warn(caplog):
    base = _BASE
    child = '{% extends "base_2892.html" %}\n{% block content %}<p>x</p>{% endblock %}\n'
    template_mod._UNMATCHED_ROOT_WARNED.clear()
    with _TemplateHarness(base, child):
        with caplog.at_level(logging.WARNING, logger="djust.mixins.template"):
            v = _extends_view("NoRoot")()
            v.mount(None)
            v.get_template()
            v.render_full_template(None)
    assert not [r for r in caplog.records if "#2892" in r.getMessage()]


# ---------------------------------------------------------------------------
# The shared primitives
# ---------------------------------------------------------------------------


class TestRootOpenRegex:
    @pytest.mark.parametrize(
        "html",
        [
            "<div dj-root>",
            '<div class="x" dj-root>',
            "<main dj-root>",
            "<SECTION dj-root>",
            "<my-app dj-root>",
            '<div\tdj-root="">',
            "<div\ndj-root>",
        ],
    )
    def test_matches_real_root(self, html):
        m = template_mod._search_dj_root_open(html, template_mod._DJ_ROOT_RE)
        assert m and m.start() == 0

    @pytest.mark.parametrize(
        "html",
        [
            "<div dj-rooted>",
            "<div data-dj-root>",
            "<div dj-root-x>",
            "<body dj-root>",
            "<html dj-root>",
            "<head dj-root>",
            "<!-- <div dj-root> -->",
            "<script>'<div dj-root>'</script>",
        ],
    )
    def test_rejects_non_roots(self, html):
        assert template_mod._search_dj_root_open(html, template_mod._DJ_ROOT_RE) is None

    @pytest.mark.parametrize(
        "html",
        [
            '<input value="x dj-root y">',
            "<p title='a dj-view b'>",
            '<div class="dj-root">',
        ],
    )
    def test_marker_inside_a_quoted_value_is_not_a_root(self, html):
        for pattern in (template_mod._DJ_ROOT_RE, template_mod._DJ_VIEW_RE):
            assert template_mod._search_dj_root_open(html, pattern) is None

    def test_value_hijack_does_not_move_the_root(self):
        html = '<input value="x dj-root y"><main dj-root><p>r</p></main>'
        m = template_mod._search_dj_root_open(html, template_mod._DJ_ROOT_RE)
        assert html[m.start() : m.end()] == "<main dj-root>"

    def test_gt_inside_a_quoted_value_stays_inside_the_tag(self):
        html = '<div title="a>b" dj-root><p>r</p></div>'
        m = template_mod._search_dj_root_open(html, template_mod._DJ_ROOT_RE)
        assert m and html[m.end() :] == "<p>r</p></div>"

    def test_tag_soup_without_gt_is_linear(self):
        """The unquoted units exclude ``<``: 200 KB of ``<div <div ...`` used to
        take over a minute (quadratic); it must now be near-instant."""
        import time

        soup = "<div " * 40_000
        t0 = time.perf_counter()
        for pattern in (template_mod._DJ_ROOT_RE, template_mod._ANY_ROOT_ATTR_RE):
            assert pattern.search(soup) is None
        TemplateMixin._stamp_dj_view(soup, "a.B")
        assert time.perf_counter() - t0 < 2.0

    def test_dj_view_regex_rejects_view_prefixed_attributes(self):
        for html in ("<body dj-view-transitions>", "<div dj-viewport-top='x'>"):
            assert template_mod._search_dj_root_open(html, template_mod._DJ_VIEW_RE) is None


class TestFindClosingTagPos:
    def _close(self, html: str, tag: str):
        start = html.index(">") + 1
        return TemplateMixin._find_closing_tag_pos(html, start, tag)

    def test_nested_same_name(self):
        html = "<section dj-root><section>x</section><p>y</p></section><b>after</b>"
        close_start, close_end = self._close(html, "section")
        assert html[close_end:] == "<b>after</b>"
        assert html[close_start:close_end] == "</section>"

    def test_whitespace_in_close_tag(self):
        html = "<main dj-root><p>x</p></main\n><b>after</b>"
        _s, end = self._close(html, "main")
        assert html[end:] == "<b>after</b>"

    def test_custom_element_with_prefix_name_not_counted(self):
        html = "<section dj-root><section-x>y</section-x></section><b>after</b>"
        _s, end = self._close(html, "section")
        assert html[end:] == "<b>after</b>"

    def test_div_wrapper_still_works(self):
        html = "<div dj-root><div>x</div></div>tail"
        _s, end = TemplateMixin._find_closing_div_pos(html, html.index(">") + 1)
        assert html[end:] == "tail"

    def test_template_tag_right_after_the_name_counts_as_an_open(self):
        """``<div{{ attrs }}>`` in template source is still a ``<div>`` open."""
        html = "<div dj-root><div{{ attrs }}>x</div></div>tail"
        _s, end = TemplateMixin._find_closing_div_pos(html, html.index(">") + 1)
        assert html[end:] == "tail"

    def test_unclosed_returns_none(self):
        assert self._close("<main dj-root><p>x", "main") == (None, None)


# ---------------------------------------------------------------------------
# V012 now sees non-<div> roots
# ---------------------------------------------------------------------------


def test_v012_flags_sticky_child_with_dj_view_on_section():
    from djust.checks import check_sticky_child_own_dj_view

    cls = type(
        "StickySectionChild2892",
        (LiveView,),
        {
            "__module__": "djust.tests.test_root_detection_2892_2981",
            "sticky": True,
            "sticky_id": "s2892",
            "template": '<section dj-view="x.Y"><button dj-click="go">g</button></section>',
        },
    )
    label = "%s.%s" % (cls.__module__, cls.__qualname__)
    hits = [
        e
        for e in check_sticky_child_own_dj_view(None)
        if e.id == "djust.V012" and e.msg.startswith(label + ":")
    ]
    assert len(hits) == 1


# ---------------------------------------------------------------------------
# The other root-detecting sites use attribute-name boundaries too
# ---------------------------------------------------------------------------


def test_t005_ignores_view_prefixed_attributes():
    from djust.checks.templates import _check_view_root_same_element

    errors: list = []
    _check_view_root_same_element(
        "<body dj-view-transitions><div dj-root>x</div></body>", "t.html", "/t.html", errors
    )
    assert errors == []


def test_s011_root_range_ignores_view_prefixed_attributes():
    from djust.checks.security import _DJ_ROOT_OPEN_TAG_RE

    assert _DJ_ROOT_OPEN_TAG_RE.search("<body dj-view-transitions>") is None
    assert _DJ_ROOT_OPEN_TAG_RE.search("<main dj-view>") is not None


def test_testing_helper_finds_a_non_div_dj_view_root():
    from djust.testing import LiveViewTestClient

    html = '<nav dj-id="n"></nav><main dj-view="a.B" dj-id="0"><p dj-id="1">x</p></main>'
    assert LiveViewTestClient._djroot_djids(html) == ["0", "1"]
