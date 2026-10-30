"""The LiveView root of RENDERED HTML has one locator: the Rust scanner.

The WebSocket frame (``_extract_liveview_content``), the initial-GET shell
splice, ``_strip_liveview_root_in_html``, the streaming split
(``_split_for_streaming`` / ``arender_chunks``), the ``dj-view`` stamp and the
test client's dj-id helper all read the root from ``djust._rust``
(``root_content_range`` / ``root_element_span`` / ``dj_root_stamp_offsets``).
They used a Python twin that disagreed with the Rust scanner (and with
html5ever, which builds the VDOM) on three kinds of input:

1. ``{% if %}`` / ``{% else %}`` text in rendered output. The Python balancer
   read it as template branches, so the root closed early (its tail was cut
   off, or spliced in twice) or never.
2. ``<body>`` as the root beside another view's container (#3302, #3031): a
   ``<body>`` tag anywhere adds its attributes to the one body element.
3. A ``<`` or NUL inside a tag name: part of the name, so ``<div<x dj-root>``
   is a root the Python mask never saw.

The shared corpus (``python/tests/fixtures/root_selection_3031.json``) pins the
locator itself; this file pins each caller.
"""

from __future__ import annotations

import asyncio

import pytest

from djust import LiveView
from djust.http_streaming import ChunkEmitter
from djust.mixins.template import TemplateMixin

_IF_TEXT = "{% if a %}<div>{% else %}</div>{% endif %}"
_IF_PAGE = (
    "<!DOCTYPE html><html><head></head><body><div dj-root>"
    + _IF_TEXT
    + "<p>tail</p></div><footer>f</footer></body></html>"
)


def test_ws_frame_reads_template_syntax_as_text():
    """Class 1: the root closes at its own end tag, so its tail is kept."""
    assert LiveView()._extract_liveview_content(_IF_PAGE) == _IF_TEXT + "<p>tail</p>"


def test_strip_root_reads_template_syntax_as_text():
    html = _IF_PAGE.replace("<p>tail</p>", "<!-- c --><p>tail</p>").replace(
        "<footer>f", "<footer><!-- keep -->f"
    )
    stripped = LiveView()._strip_liveview_root_in_html(html)
    assert "<!-- c -->" not in stripped
    assert "<p>tail</p></div><footer><!-- keep -->f</footer>" in stripped


def test_streaming_split_finds_a_root_with_a_lt_in_its_name():
    """Class 3: ``<div<x dj-root>`` is an element named ``div<x``."""
    html = "<html><body><div<x dj-root>m</div<x></body></html>"
    shell_open, main, shell_close = LiveView()._split_for_streaming(html)
    assert shell_open == "<html><body>"
    assert main == "<div<x dj-root>m</div<x>"
    assert shell_close == "</body></html>"


def test_streaming_split_still_needs_dj_root():
    html = "<html><body><div dj-view>m</div></body></html>"
    assert LiveView()._split_for_streaming(html) == (html, "", "")


@pytest.mark.asyncio
async def test_arender_chunks_closes_the_root_at_its_own_end_tag(rf):
    view = LiveView()
    view.request = rf.get("/")
    emitter = ChunkEmitter(view.request)

    async def drain():
        return [chunk async for chunk in emitter]

    consumer = asyncio.create_task(drain())
    await view.arender_chunks(_IF_PAGE, emitter)
    await emitter.close()
    chunks = await consumer
    assert b"".join(chunks) == _IF_PAGE.encode()
    assert chunks[1] == b"<div dj-root>"
    assert chunks[2] == (_IF_TEXT + "<p>tail</p>").encode()
    assert chunks[3] == b"</div><footer>f</footer></body></html>"


def test_shell_splice_reads_template_syntax_as_text():
    """The initial GET replaces the shell's root with the VDOM render; with the
    root closed at the first ``</div>`` the tail was spliced in twice."""
    from djust.tests.test_render_full_template_djroot_substring_1746 import (
        _BASE,
        _TemplateHarness,
    )

    child = (
        '{% extends "base_1746.html" %}\n{% block content %}'
        "{{ snippet|safe }}<p>tail</p>{% endblock %}\n"
    )

    def mount(self, request, **kwargs):
        self.snippet = _IF_TEXT

    with _TemplateHarness(_BASE, child):
        cls = type("ShellIfText", (LiveView,), {"template_name": "child_1746.html", "mount": mount})
        view = cls()
        view.mount(None)
        view.get_template()
        html = view.render_full_template(None)
    assert html.count("<p>tail</p>") == 1, html
    assert html.count("{% endif %}") == 1, html
    assert html.lower().count("<footer") == 1, html


def test_stamp_finds_a_root_with_a_lt_in_its_name():
    html = "<main><div<x dj-root>m</div<x></main>"
    assert TemplateMixin._stamp_dj_view(html, "views.Page") == (
        '<main><div<x dj-root dj-view="views.Page">m</div<x></main>'
    )


def test_stamp_follows_the_body_html5ever_builds():
    """Class 2: a child document's ``<body dj-view>`` comes after the page's
    ``<body dj-root>``, so the body keeps the page's stamp."""
    html = (
        '<body dj-root><div dj-view data-djust-embedded="c">'
        '<body dj-view="c.V">c</body></div></body>'
    )
    assert TemplateMixin._stamp_dj_view(html, "views.Page") == html.replace(
        "<body dj-root>", '<body dj-root dj-view="views.Page">'
    )


def test_stamp_offsets_are_string_indices():
    html = '<p>é 漢</p><div class="ü" dj-root>x</div>'
    assert TemplateMixin._stamp_dj_view(html, "v.P") == html.replace(
        "dj-root>", 'dj-root dj-view="v.P">'
    )


def test_test_client_djids_start_at_the_root():
    from djust.testing import LiveViewTestClient

    html = '<p dj-id="0">x</p><div<x dj-root dj-id="1"><i dj-id="2">m</i></div<x>'
    assert LiveViewTestClient._djroot_djids(html) == ["1", "2"]
