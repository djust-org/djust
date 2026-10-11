"""Text in SVG/MathML ``<style>`` and ``<script>`` is serialized escaped.

The HTML serializer writes a raw-text element's text verbatim only for HTML
elements. Inside ``<svg>`` or ``<math>`` a ``<style>`` or ``<script>`` is a
foreign element: the parser decodes its text, so serializing it must escape
that text again, or ``&lt;/style&gt;`` comes back as a real end tag. These run
the page render path (``RustLiveView.render_with_diff``) and check that
parsing the HTML it returns again gives the same tree.
"""

from __future__ import annotations

import json
import re

import msgpack
import pytest

from djust._rust import RustLiveView, diff_html

PAYLOAD = "</style></script><img src=x onerror=alert(1)> a & b"
ESCAPED = "&lt;/style&gt;&lt;/script&gt;&lt;img src=x onerror=alert(1)&gt; a &amp; b"
_DJ_ID = re.compile(r' dj-id="[^"]*"')


def _render(template: str, **state) -> str:
    view = RustLiveView(template, [])
    view.update_state(state)
    return view.render_with_diff()[0]


def _reparsed(html: str) -> str:
    """``html`` parsed again by the same parser and serialized, dj-ids removed."""
    return _DJ_ID.sub("", _render(html))


@pytest.mark.parametrize(
    "open_tag, close_tag",
    [
        ("<svg><style>", "</style></svg>"),
        ("<svg><script>", "</script></svg>"),
        ("<math><style>", "</style></math>"),
    ],
)
def test_foreign_style_and_script_text_is_escaped(open_tag, close_tag):
    html = _render(f"<div dj-root>{open_tag}{{{{ x }}}}{close_tag}</div>", x=PAYLOAD)
    assert f">{ESCAPED}</" in html
    assert "<img" not in html
    assert _reparsed(html) == _DJ_ID.sub("", html), "a re-parse gives the same tree"


def test_html_style_and_script_are_unchanged():
    css = ".a > .b { content: '&amp;' }"
    js = "if (a < b && c > d) { s = '&amp;'; }"
    html = _render(
        "<div dj-root><style>{{ css|safe }}</style><script>{{ js|safe }}</script>"
        "<svg><style>.c { fill: red }</style></svg></div>",
        css=css,
        js=js,
    )
    assert f">{css}</style>" in html
    assert f">{js}</script>" in html
    assert ">.c { fill: red }</style></svg>" in html


def test_foreign_object_content_is_html():
    html = _render(
        "<div dj-root><svg><foreignObject><style>{{ x|safe }}</style></foreignObject></svg></div>",
        x="a &amp; b > c",
    )
    assert ">a &amp; b > c</style></foreignObject>" in html


def test_inserted_subtree_never_writes_foreign_text_as_markup():
    # A new {% if %} block inside <svg> reaches the client as InsertSubtree
    # HTML, which the client parses in an HTML context.
    view = RustLiveView(
        "<div dj-root><svg>{% for i in items %}{% if i %}<style>{{ css }}</style>"
        "{% endif %}{% endfor %}<g></g></svg></div>",
        [],
    )
    view.update_state({"items": [], "css": PAYLOAD})
    view.render_with_diff()
    view.update_state({"items": [1]})
    patches = json.loads(view.render_with_diff()[1])
    inserted = [p["html"] for p in patches if p["type"] == "InsertSubtree"]
    assert inserted, patches
    for html in inserted:
        assert "<img" not in html
        assert ESCAPED.replace("&amp;", "&") not in html


@pytest.mark.parametrize(
    "middle",
    [
        "<svg></math>",
        "<svg><foreignObject><div></svg></div></foreignObject>",
        "<svg><desc><p></svg></p></desc>",
    ],
)
def test_text_change_after_an_ignored_foreign_end_tag_matches_a_full_parse(middle):
    # The tree builder ignores these </svg> / </math> tags, so the <style>
    # after them is still SVG and its <g> tags are elements. The text fast
    # path must give the patches a full parse gives.
    template = (
        "<div dj-root><textarea>a<b>{{ q|safe }}</textarea>"
        f"{middle}<style><g>Q</g><g>y</g></style></svg></div>"
    )

    def patches(full_parse: bool) -> str:
        view = RustLiveView(template, [])
        view.update_state({"q": "Q"})
        view.render_with_diff()
        if full_parse:
            view.clear_fragment_cache()
        view.update_state({"q": "W"})
        return view.render_with_diff()[1]

    fast = patches(full_parse=False)
    assert fast == patches(full_parse=True)
    assert '"text":"W"' not in fast


@pytest.mark.parametrize("method", ["render_with_diff", "render_binary_diff"])
@pytest.mark.parametrize(
    "template",
    [
        "<svg><g dj-root><style>{{ value }}</style></g></svg>",
        "<math><mrow dj-root><style>{{ value }}</style></mrow></math>",
        '<svg><g dj-root><g dj-update="ignore"><style>{{ value }}</style></g></g></svg>',
    ],
)
def test_a_root_inside_svg_or_math_serializes_its_style_as_foreign_text(method, template):
    # The root is an SVG / MathML element, so its <style> is too: the client
    # assigns this HTML as the root's innerHTML, which parses it as foreign
    # content, so the text must be escaped (an auto-escaped value, no |safe).
    view = RustLiveView(template, [])
    view.update_state({"value": PAYLOAD})
    html = getattr(view, method)()[0]
    assert f">{ESCAPED}</style>" in html, html
    assert "<img" not in html
    # A later render, through whichever path it takes, stays escaped.
    view.update_state({"value": PAYLOAD + " c"})
    html = getattr(view, method)()[0]
    assert "<img" not in html, html


def test_an_html_root_is_unchanged_by_the_root_namespace():
    css = ".a > .b { content: '&amp;' }"
    html = _render(
        "<svg><foreignObject><div dj-root><style>{{ css|safe }}</style></div></foreignObject></svg>",
        css=css,
    )
    assert f">{css}</style>" in html


_NOSCRIPT_TAG = re.compile(r"<(/?)noscript(?=[\s/>])", re.IGNORECASE)


def _elements(html: str) -> list[str]:
    """Element names of ``html`` parsed in an HTML context, as the client
    parses an ``InsertSubtree`` payload (``<template>.innerHTML``).

    A template's document has scripting disabled, so a ``<noscript>`` there
    holds markup. ``diff_html`` parses with scripting enabled, where
    ``<noscript>`` holds raw text; renamed to a custom element name it is
    parsed the way a scripting-disabled parser treats it (an ordinary
    element, in HTML and in foreign content alike).
    """
    html = _NOSCRIPT_TAG.sub(r"<\1dj-noscript", html)
    patches = json.loads(
        diff_html("<div dj-root><p></p></div>", f"<div dj-root><section>{html}</section></div>")
    )
    (replace,) = [p for p in patches if p["type"] == "Replace"]
    names = []

    def walk(node):
        for child in node["children"]:
            if not child["tag"].startswith("#"):
                names.append(child["tag"].replace("dj-noscript", "noscript"))
                walk(child)

    walk(replace["node"])
    return sorted(names)


@pytest.mark.parametrize("method", ["render_with_diff", "render_binary_diff"])
@pytest.mark.parametrize(
    "pre, branch, post",
    [
        ("<div dj-root><svg>", "<style><style>a</style>{{ v }}</style>", "</svg></div>"),
        ("<div dj-root><svg>", "<style><g><style>a</style></g>{{ v }}</style>", "</svg></div>"),
        ("<div dj-root><svg>", "<noembed><noembed>a</noembed>{{ v }}</noembed>", "</svg></div>"),
        ("<div dj-root><svg>", "<script><script>a</script>{{ v }}</script>", "</svg></div>"),
        (
            "<div dj-root><svg>",
            "<title><script></title><option>x</option></script>{{ v }}</title>",
            "</svg></div>",
        ),
        ("<svg><g dj-root>", "<style><style>a</style>{{ v }}</style>", "</g></svg>"),
        ("<math><mrow dj-root>", "<style><style>a</style>{{ v }}</style>", "</mrow></math>"),
    ],
)
def test_inserted_raw_text_element_holding_elements_never_ends_early(method, pre, branch, post):
    # An {% if %} / {% elif %} swap inside <svg> inserts a foreign <style>
    # that holds a <style> element and then an auto-escaped value. The
    # client reads the payload as HTML: the inner </style> must not end the
    # outer element, or the value after it is parsed as markup.
    template = (
        f"{pre}{{% if step == 1 %}}<g>one</g>{{% elif step == 2 %}}{branch}{{% endif %}}{post}"
    )
    value = "<img src=x onerror=alert(1)>"
    view = RustLiveView(template, [])
    view.update_state({"step": 1, "v": value})
    getattr(view, method)()
    view.update_state({"step": 2, "v": value})
    patches = getattr(view, method)()[1]
    if isinstance(patches, (bytes, bytearray)):
        patches = json.dumps(msgpack.unpackb(patches, raw=False))
    payloads = [p["html"] for p in json.loads(patches) if p["type"] == "InsertSubtree"]
    assert payloads, patches

    # The full render of the same state holds no <img>; the payload, read as
    # the client reads it, holds only the inserted element itself.
    fresh = RustLiveView(template, [])
    fresh.update_state({"step": 2, "v": value})
    assert "<img" not in fresh.render_with_diff()[0]
    outer = branch[1 : branch.index(">")]
    for html in payloads:
        assert _elements(html) == [outer], html


@pytest.mark.parametrize("method", ["render_with_diff", "render_binary_diff"])
@pytest.mark.parametrize(
    "pre, post",
    [
        ("<div dj-root><svg>", "</svg></div>"),
        ("<div dj-root><math>", "</math></div>"),
        ("<svg><g dj-root>", "</g></svg>"),
        ("<math><mrow dj-root>", "</mrow></math>"),
    ],
)
def test_inserted_foreign_noscript_text_stays_text(method, pre, post):
    # An {% if %} / {% elif %} swap inserting an SVG or MathML <noscript>
    # with an auto-escaped value. The client parses the payload with
    # scripting disabled, where <noscript> holds markup: the value must be
    # escaped there, as it is in the full render.
    template = (
        f"{pre}{{% if step == 1 %}}<g>one</g>"
        f"{{% elif step == 2 %}}<noscript>{{{{ v }}}}</noscript>{{% endif %}}{post}"
    )
    value = "<img src=x onerror=alert(1)>"
    view = RustLiveView(template, [])
    view.update_state({"step": 1, "v": value})
    getattr(view, method)()
    view.update_state({"step": 2, "v": value})
    patches = getattr(view, method)()[1]
    if isinstance(patches, (bytes, bytearray)):
        patches = json.dumps(msgpack.unpackb(patches, raw=False))
    payloads = [p["html"] for p in json.loads(patches) if p["type"] == "InsertSubtree"]
    assert payloads, patches

    fresh = RustLiveView(template, [])
    fresh.update_state({"step": 2, "v": value})
    assert "<img" not in fresh.render_with_diff()[0]
    for html in payloads:
        assert _elements(html) == ["noscript"], html
        assert "&lt;img src=x onerror=alert(1)&gt;</noscript>" in html, html
