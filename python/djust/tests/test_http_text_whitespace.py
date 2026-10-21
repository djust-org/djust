"""The HTTP render keeps the white space of text, as Django and the socket do.

``TemplateMixin._strip_comments_and_whitespace`` normalizes every page an HTTP
GET (and the SSE / recovery paths) serves. It folded EVERY run of white space
outside ``pre``/``code``/``textarea``/``script``/``style`` to one space --
including the white space inside text. So a stored message
``"question\\n\\n[1 image]"`` shown in a ``white-space: pre-wrap`` element read
``"question [1 image]"`` on a fresh load, while the same text patched in over
the WebSocket kept its lines: the VDOM parser keeps a text node with content
verbatim, and Django renders it verbatim.

The fold now applies to markup (inside tags) and to whitespace-only text
between tags, which the inter-tag rule (#2999) then keeps or drops. Text that
has content keeps its white space. One rule in Rust
(``djust_core::html_whitespace::fold_whitespace_edits``) serves both the plain
string path and the provenance-tracking path.
"""

from __future__ import annotations

import os
import re

import pytest
from django.conf import settings
from django.template import Context, Template
from django.test import RequestFactory

from djust.mixins.template import TemplateMixin

TEXTS = {
    "line feeds": "question\n\n[1 image]",
    "space runs": "a  b   c",
    "tabs": "col1\tcol2",
    "leading and trailing": "\n  indented line\n",
    "markdown": "# Title\n\n- one\n- two\n\n```\ncode\n```",
}

SHELLS = {
    "pre-wrap class": '<p class="whitespace-pre-wrap">{{ t }}</p>',
    "pre-wrap style": '<div style="white-space: pre-wrap">{{ t }}</div>',
    "plain paragraph": "<p>{{ t }}</p>",
    "safe markup": '<div class="md"><p>{{ t|safe }}</p></div>',
}


def _inner(html: str) -> str:
    """The element body: what a browser lays out under ``white-space``."""
    match = re.search(r"<(p|div)\b[^>]*>(.*)</\1>", html, re.S)
    assert match, html
    return re.sub(r' dj-id="[^"]*"', "", match.group(2))


def _django(shell: str, text: str) -> str:
    return _inner(Template(shell).render(Context({"t": text})))


def _http_get(shell: str, text: str, directory: str) -> str:
    """A real ``LiveView`` HTTP GET -- the path that folded."""
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.signed_cookies import SessionStore

    from djust.live_view import LiveView

    name = "ws_%x.html" % (abs(hash(shell)) & 0xFFFFFFF)
    with open(os.path.join(directory, name), "w") as handle:
        handle.write("<div dj-root>" + shell + "</div>")
    view = type(
        "WhitespaceView",
        (LiveView,),
        {"template_name": name, "mount": lambda self, request, **kw: setattr(self, "t", text)},
    )
    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    request.session = SessionStore()
    from djust.tests.test_tag_bridge_object_parity_2731 import _repoint_template_engines

    original = settings.TEMPLATES[0]["DIRS"]
    settings.TEMPLATES[0]["DIRS"] = [directory]
    try:
        _repoint_template_engines()
        html = view.as_view()(request).content.decode()
    finally:
        settings.TEMPLATES[0]["DIRS"] = original
        _repoint_template_engines()
    root = re.search(r"<div dj-root[^>]*>(.*)</div>", html, re.S)
    assert root, html
    return _inner(root.group(1))


def _socket(shell: str, text: str) -> str:
    from djust._rust import RustLiveView

    view = RustLiveView("<div>" + shell + "</div>")
    view.set_state("t", text)
    html, _patches, _version = view.render_with_diff()
    return _inner(re.search(r"<div[^>]*>(.*)</div>", html, re.S).group(1))


class TestHttpTextMatchesDjangoAndTheSocket:
    @pytest.mark.parametrize("shell", sorted(SHELLS))
    @pytest.mark.parametrize("text", sorted(TEXTS))
    def test_http_get_matches_django(self, shell, text, tmp_path):
        source, value = SHELLS[shell], TEXTS[text]
        assert _http_get(source, value, str(tmp_path)) == _django(source, value)

    @pytest.mark.parametrize("text", sorted(TEXTS))
    def test_http_get_matches_the_socket_render(self, text, tmp_path):
        source, value = SHELLS["pre-wrap class"], TEXTS[text]
        assert _http_get(source, value, str(tmp_path)) == _socket(source, value)


class TestTheNormalizer:
    @pytest.fixture
    def strip(self):
        return TemplateMixin()._strip_comments_and_whitespace

    def test_text_with_content_keeps_its_white_space(self, strip):
        html = '<p class="whitespace-pre-wrap">a\n\nb  c</p>'
        assert strip(html) == html

    def test_markup_white_space_is_still_folded(self, strip):
        assert (
            strip('<div\n    class="x"\n    id="y">\n  <span>a</span>\n</div>')
            == '<div class="x" id="y"><span>a</span></div>'
        )

    def test_indentation_between_blocks_is_still_dropped(self, strip):
        assert strip("<ul>\n  <li>a</li>\n  <li>b</li>\n</ul>") == "<ul><li>a</li><li>b</li></ul>"

    def test_the_space_between_inline_elements_is_still_kept(self, strip):
        assert strip("<p><b>A</b>\n   <i>B</i></p>") == "<p><b>A</b> <i>B</i></p>"

    def test_pre_textarea_script_unchanged(self, strip):
        html = "<div><pre>a\n  b</pre><textarea>x\n\ny</textarea><script>// c\nf()</script></div>"
        assert strip(html) == html


class TestOneRuleOnEveryPath:
    """The plain string, the provenance-tracking string, and the provenance
    string holding preserved blocks all fold the same bytes (#1646)."""

    SAMPLES = [
        '<div\n class="a">\n  <p class="whitespace-pre-wrap">a\n\nb</p>\n</div>',
        "<p>one  two</p>\n\n<p>\tthree</p>",
        "  \n<span>x</span>   y\n",
    ]

    @pytest.mark.parametrize("html", SAMPLES)
    def test_plain_and_tracked_agree(self, html):
        from djust._render_provenance import RenderedHTML, fold

        tracked = fold(RenderedHTML(html, ((0, len(html.encode())),)))
        assert isinstance(tracked, RenderedHTML)
        assert str(tracked) == fold(html)

    @pytest.mark.parametrize("html", SAMPLES)
    def test_tracked_with_held_blocks_agrees(self, html):
        from djust._render_provenance import RenderedHTML, fold, join

        token = "__PRESERVED_BLOCK_0__"
        held = RenderedHTML(token, held=((0, len(token), "<pre>z</pre>"),))
        combined = join([RenderedHTML(html), held, RenderedHTML(html)])
        assert combined.held
        assert str(fold(combined)) == fold(html + token + html)
