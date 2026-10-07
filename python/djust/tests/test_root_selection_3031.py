"""One ownership-aware root selection rule, on both sides of the FFI (#3031).

Which element is the LiveView root was decided three times with three rules:

* Python (``mixins/template.py::_search_dj_root_open``): the first ``dj-root``
  in the document, else the first ``dj-view``, skipping embedded children.
* The Rust VDOM (``djust_vdom::parser::find_liveview_root``): the first
  element carrying EITHER attribute, embedded children included.
* The Rust text scanner (``djust_live::find_dj_root_content_range``): the same
  as the VDOM, which must pick the same element for the text fast path.

The one rule: a root belongs to the view that rendered it, so an embedded
``{% live_render %}`` child's wrapper (``dj-view`` + ``data-djust-embedded``,
sticky or not) and everything inside it is never the parent's root; among the
rest, the first ``dj-root`` wins over the first ``dj-view``.

``python/tests/fixtures/root_selection_3031.json`` is the shared corpus: the
element carrying ``data-expect-root`` is the root (``root: null`` means none).
A page with ``diverges`` lists the locators known NOT to reach that answer,
because of contexts html5ever reads as text or as a separate fragment
(``<title>``, ``<template>``, ``<noscript>``, ``<iframe>``,
``<xmp>``) or a void element used as a wrapper; those predate #3031 and are
pinned as they are rather than fixed here.
The textarea divergence was corrected in #3302: its RCDATA contents cannot
declare an inner root, so Python/scanner now agree with browser/html5ever semantics.
This file checks the Python locator and the Rust VDOM against it; the Rust text
scanner is checked against the same file by ``cargo test -p djust_live``
(``dj_root_selection_3031``).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from django.test import override_settings

from djust import LiveView
from djust._rust import RustLiveView
from djust.mixins.template import _DJ_ROOT_RE, _DJ_VIEW_RE, _find_root_close, _search_dj_root_open

_CORPUS = json.loads(
    (Path(__file__).resolve().parents[2] / "tests/fixtures/root_selection_3031.json").read_text()
)["cases"]
_IDS = [c["name"] for c in _CORPUS]
_MARK = "data-expect-root"


def _python_root(html: str):
    m = _search_dj_root_open(html, _DJ_ROOT_RE, _DJ_VIEW_RE)
    return None if m is None else html[m.start() : m.end()]


def _vdom_root_open_tag(html: str):
    """Open tag of the element the Rust VDOM is rooted at (serialised back)."""
    rendered, _patches, _version = RustLiveView(html, []).render_with_diff()
    m = re.match(r"<([A-Za-z][^\s>/]*)([^>]*)>", rendered)
    assert m, rendered
    return m.group(1).lower(), m.group(2)


def _picks_expected(tag, case) -> bool:
    return (tag is not None and _MARK in tag) if case["root"] else tag is None


@pytest.mark.parametrize("case", _CORPUS, ids=_IDS)
def test_python_locator_picks_the_corpus_root(case):
    tag = _python_root(case["html"])
    if "python" in case.get("diverges", ()):
        # A known leftover that predates #3031: pinned so a change is noticed.
        assert not _picks_expected(tag, case), (case["why"], tag)
    else:
        assert _picks_expected(tag, case), (case["why"], tag)


@pytest.mark.parametrize("case", [c for c in _CORPUS if c["html"]], ids=lambda c: c["name"])
def test_rust_vdom_picks_the_corpus_root(case):
    name, attrs = _vdom_root_open_tag(case["html"])
    if case["root"]:
        assert _MARK in attrs, (case["why"], name, attrs)
    else:
        # No root: the documented fallback, the first element child of <body>.
        assert _MARK not in attrs
        assert name == case["vdom_fallback_tag"], (case["why"], name)


@pytest.mark.parametrize(
    "case",
    [c for c in _CORPUS if c["html"] and not c.get("diverges")],
    ids=lambda c: c["name"],
)
def test_python_and_rust_agree_on_every_corpus_page(case):
    """The differential: Python's pick and the VDOM's pick are the same element.
    Pages in ``diverges`` are the documented leftovers and are excluded."""
    py = _python_root(case["html"])
    name, attrs = _vdom_root_open_tag(case["html"])
    if py is not None:
        assert (_MARK in py) == (_MARK in attrs)
        assert py.lower().startswith("<" + name)
    else:
        assert _MARK not in attrs


@pytest.mark.parametrize("case", [c for c in _CORPUS if "content" in c], ids=lambda c: c["name"])
def test_corpus_root_content_requires_a_complete_end_token(case):
    html = case["html"]
    opening = _search_dj_root_open(html, _DJ_ROOT_RE, _DJ_VIEW_RE)
    assert opening is not None
    start, end = _find_root_close(html, opening)
    if case["content"] is None:
        assert (start, end) == (None, None)
    else:
        assert html[opening.end() : start] == case["content"]


class TestIssue3031Scenario:
    """The reverted #3053 scenario: a ``dj-view``-only parent embedding a child
    whose template carries its own ``dj-root``. Letting the VDOM take Python's
    precedence (``dj-root`` first, anywhere) rooted it at the CHILD, and the
    parent's own changes stopped producing patches."""

    HTML = (
        '<div dj-view="p.ParentP"><p>{{ n }}</p>'
        '<div dj-view data-djust-embedded="child-1"><div dj-root><i>child</i></div></div></div>'
    )

    def test_parent_changes_still_patch(self):
        view = RustLiveView(self.HTML, [])
        view.set_state("n", 1)
        rendered, _, _ = view.render_with_diff()
        assert rendered.startswith('<div dj-id="0" dj-view="p.ParentP">'), rendered
        view.set_state("n", 2)
        _, patches, _ = view.render_with_diff()
        assert patches is not None and "SetText" in patches, patches

    def test_the_dj_view_dj_root_precedence_repro(self):
        html = '<nav dj-view="a.B"><a>n</a></nav><main dj-root><p>m</p></main>'
        assert _python_root(html) == "<main dj-root>"
        name, _ = _vdom_root_open_tag(html)
        assert name == "main"


_MODULE = "djust.tests.test_root_selection_3031"


class ChildP3031(LiveView):
    """A child whose own template declares a ``dj-root``."""

    template = '<div dj-root><p class="child-body">child</p></div>'

    def mount(self, request, **kwargs):
        pass


class ParentP3031(LiveView):
    """A parent whose root is declared with ``dj-view`` only (the auto-inferred
    root) and which embeds the child above."""

    template = (
        '{% load live_tags %}<div dj-view="' + _MODULE + '.ParentP3031">'
        '<p class="count">{{ n }}</p>{% live_render "' + _MODULE + '.ChildP3031" %}</div>'
    )

    def mount(self, request, **kwargs):
        self.n = 1


@override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=[_MODULE])
def test_parent_with_embedded_dj_root_child_patches_through_the_view(rf):
    """The whole pipeline (Python extraction, Rust VDOM): the parent's own change
    produces a patch, and the VDOM is rooted at the parent, not the child."""
    request = rf.get("/")
    view = ParentP3031()
    view.request = request
    view.mount(request)
    html, _patches, _version = view.render_with_diff(request)
    assert html.startswith("<div "), html
    assert 'dj-view="%s.ParentP3031"' % _MODULE in html.split(">", 1)[0], html
    assert "child-body" in html
    view.n = 2
    _html, patches, _version = view.render_with_diff(request)
    assert patches is not None and "SetText" in patches, patches
