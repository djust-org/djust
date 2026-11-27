"""The Rust shape walk answers exactly what the Python walk answers.

``_render_digest._walk_shape`` calls ``djust._rust.render_shape_walk`` and keeps
``_py_walk_shape`` as the reference. The shape is a checksum the client
computes from its own DOM, so one byte of difference would answer every
fallback turn on such a page with the whole page; these tests hold the two
walks together on the shared client vectors, on generated documents built to
hit every branch (raw text, opaque regions, comments, character references,
non-ASCII whitespace, unbalanced tags), and on the fallback paths.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from djust import _render_digest as rd

pytestmark = pytest.mark.skipif(rd._native_walk is None, reason="no Rust extension")

_FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "js" / "fixtures"


def _both(html: str):
    native = rd._native_walk(html, rd.unescape)
    ref = rd._py_walk_shape(html)
    if ref is None:
        return native, None
    return native, (ref.shape, ref.opaque_paths, ref.client_owned)


def _same(html: str) -> None:
    native, ref = _both(html)
    if native is not None:
        native = (native[0], [tuple(p) for p in native[1]], native[2])
    assert native == ref, html


def _fixture_html():
    vectors = json.loads((_FIXTURES / "dom_shape_vectors_4108.json").read_text())["vectors"]
    pages = json.loads((_FIXTURES / "dom_shape_random_4001.json").read_text())["pages"]
    return [v["html"] for v in vectors] + [p["html"] for p in pages]


@pytest.mark.parametrize("html", _fixture_html())
def test_the_shared_vectors(html):
    _same(html)


_TAGS = ["div", "p", "span", "ul", "li", "pre", "textarea", "script", "style", "title", "code", "B", "Td"]
_VOIDS = ["br", "img", "input", "hr", "meta"]
_TEXT = [
    "x",
    " ",
    "  ",
    "\n",
    "\nlead",
    "a  b",
    "&amp;",
    "&lt;&gt;",
    "&nbsp;",
    "&#x1F600;",
    "&bogus;",
    "é漢",
    " ",
    " x",
    "\x1c",
    "\t\r\f",
    "😀",
    "'\"",
    "a > b",
]
_ATTRS = [
    'id="a"',
    "id='b'",
    "id=c",
    "ID=dup id=second",
    'class="skip"',
    'aria-label="skip"',
    'data-dj-x="skip"',
    'data-x="&amp;&quot;"',
    "dj-update=ignore",
    'dj-update="append"',
    "dj-virtual",
    "dj-virtual-height=3",
    'name="csrfmiddlewaretoken" value="zzz"',
    "checked",
    "title='a > b'",
    'z="1" a="2" m="3"',
    "é=1",
    "x =  y",
    "/",
]
_COMMENTS = ["<!-- dj-if -->", "<!--/dj-if-->", "<!--dj-if\tid=1-->", "<!-- other -->", "<!--x--!>", "<!-- dj-if -->"]


def _node(rng: random.Random, depth: int) -> str:
    roll = rng.random()
    if roll < 0.3 or depth > 4:
        return rng.choice(_TEXT)
    if roll < 0.38:
        return rng.choice(_COMMENTS)
    if roll < 0.45:
        return "<%s%s>" % (rng.choice(_VOIDS), (" " + rng.choice(_ATTRS)) if rng.random() < 0.5 else "")
    tag = rng.choice(_TAGS)
    attrs = "".join(" " + rng.choice(_ATTRS) for _ in range(rng.randrange(3)))
    inner = "".join(_node(rng, depth + 1) for _ in range(rng.randrange(4)))
    close = tag
    if rng.random() < 0.05:
        close = tag.upper()
    if rng.random() < 0.02:
        close = "other"  # unbalanced
    end = "</%s%s>" % (close, rng.choice(["", " ", "\n"]))
    if rng.random() < 0.02:
        end = ""  # unclosed
    return "<%s%s>%s%s" % (tag, attrs, inner, end)


def test_generated_documents():
    rng = random.Random(4108)
    unbalanced = 0
    for _ in range(3000):
        root_attrs = rng.choice(["", " dj-root", ' dj-root="" dj-update="ignore"', " id=r/"])
        html = "<div%s>%s</div>" % (root_attrs, "".join(_node(rng, 0) for _ in range(rng.randrange(6))))
        native, ref = _both(html)
        unbalanced += ref is None
        _same(html)
    assert unbalanced  # the generator does reach the None answers


@pytest.mark.parametrize(
    "html",
    [
        "",
        "plain text",
        "<div dj-root>",
        "<div dj-root></div><p></p>",
        "<div dj-root><script>if (a</b) {}</script></div>",
        "<div dj-root><script>x</SCRIPT ></div>",
        "<div dj-root><script>x</ſcript></div>",  # long s folds to s under IGNORECASE
        "<div dj-root><textarea>\n\nab &amp;</textarea></div>",
        "<div dj-root><pre>\nkeep</pre><listing>\nx</listing></div>",
        "<div dj-root><p dj-update=ignore><script>no end",
        "<div dj-root><p dj-update=ignore><b><style>a</style></b></p>tail</div>",
        "<div dj-root><title dj-update=ignore>t &amp;</title></div>",
        "<br>",
        "<div dj-root><p></div>",
        "<div dj-root>　</div>",
    ],
)
def test_edge_documents(html):
    _same(html)


def test_walk_shape_uses_the_native_walk_and_falls_back(monkeypatch):
    html = '<div dj-root><p dj-update="ignore">x</p><i>&amp;</i></div>'
    found = rd._walk_shape(html)
    ref = rd._py_walk_shape(html)
    assert (found.shape, found.opaque_paths, found.client_owned) == (ref.shape, ref.opaque_paths, ref.client_owned)
    assert found.opaque_paths == [(0,)]

    # A lone surrogate cannot cross into Rust: the Python walk answers.
    lone = "<div dj-root><p>\ud800</p></div>"
    assert rd._walk_shape(lone).shape == rd._py_walk_shape(lone).shape

    # No extension: the Python walk answers.
    monkeypatch.setattr(rd, "_native_walk", None)
    assert rd._walk_shape(html).shape == ref.shape


def test_an_error_from_the_native_walk_falls_back(monkeypatch):
    def broken(html, unescape):
        raise RuntimeError("djust internal error")

    monkeypatch.setattr(rd, "_native_walk", broken)
    html = "<div dj-root><p>x</p></div>"
    assert rd._walk_shape(html).shape == rd._py_walk_shape(html).shape


def test_generated_attribute_text():
    """The hand scanner Rust uses for attributes against Python's ``_ATTRS``
    regex: quotes left open, ``=`` with nothing after it, ``==``, values
    holding ``<`` and ``/``, Unicode spaces around ``=``, names in any case."""
    rng = random.Random(4365)
    alphabet = ['a', 'B', 'é', '-', '=', '=', '"', "'", ' ', '  ', '\t', '\n', ' ', ' ', '\x1c', '<', '/', '&amp;', 'x1', 'dj-update', 'ignore']
    for _ in range(4000):
        attrs = "".join(rng.choice(alphabet) for _ in range(rng.randrange(1, 14)))
        if attrs.count('"') % 2 or attrs.count("'") % 2:
            # An odd quote would let the tag run on to a later ">" (or none);
            # balanced or not, the token regex decides that, not the scanner.
            attrs += rng.choice(['"', "'", ""])
        html = '<div dj-root><p %s>x</p><i %s></i></div>' % (attrs, attrs[::-1])
        _same(html)
