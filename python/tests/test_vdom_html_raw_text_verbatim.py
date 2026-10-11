"""HTML raw-text content is written verbatim, exactly as the parser kept it.

In the HTML namespace the serializer writes the text of ``<script>`` and
``<style>`` back unchanged, as the HTML serialization algorithm does. That
holds when the text contains the element's own end tag in a place where the
tokenizer does not end the element: a script's
``<!-- … <script> … </script> … -->`` (the legacy ``document.write``
pattern).

The text-only fast path of ``render_binary_diff`` updates the old tree in
place; it must agree with a full parse, so it is taken only when the HTML's
text runs and the tree's text nodes line up one for one.
"""

from __future__ import annotations

import json
import re

import pytest

from djust._rust import RustLiveView

_DJ_ID = re.compile(r' dj-id="[^"]*"')

LEGACY_SCRIPT = '<script><!--\ndocument.write("<script src=a.js></script>");\n//--></script>'


def _render(template: str, **state) -> str:
    view = RustLiveView(template, [])
    view.update_state(state)
    return view.render_with_diff()[0]


def test_legacy_document_write_script_is_byte_identical():
    html = _render(f"<div dj-root>{LEGACY_SCRIPT}<p>after</p></div>")
    assert _DJ_ID.sub("", html) == f'<div dj-root="">{LEGACY_SCRIPT}<p>after</p></div>'


def test_inserted_legacy_script_is_verbatim():
    # A new {% if %} block in a loop reaches the client as InsertSubtree HTML.
    view = RustLiveView(
        "<div dj-root>{% for i in items %}{% if i %}"
        f"{LEGACY_SCRIPT}{{% endif %}}{{% endfor %}}<p>x</p></div>",
        [],
    )
    view.update_state({"items": []})
    view.render_with_diff()
    view.update_state({"items": [1]})
    patches = json.loads(view.render_with_diff()[1])
    inserted = [p["html"] for p in patches if p["type"] == "InsertSubtree"]
    assert inserted, patches
    assert any(LEGACY_SCRIPT in _DJ_ID.sub("", html) for html in inserted), inserted


@pytest.mark.parametrize(
    "template, before, after",
    [
        # Dropped whitespace before the first node.
        (
            "<div dj-root>\n<p>{{ a }}</p><p>{{ b }}</p></div>",
            {"a": "aaaa", "b": "aaaa"},
            {"a": "aaab", "b": "aaaa"},
        ),
        # Indentation, and two nodes with the same text.
        (
            "<div dj-root>    <p>{{ a }}</p><p>{{ b }}</p></div>",
            {"a": "aaaa", "b": "aaaa"},
            {"a": "bbbb", "b": "aaaa"},
        ),
        # Text outside the VDOM root.
        (
            "<html><head><title>T</title></head><body><div dj-root>"
            "<p>{{ a }}</p><p>{{ b }}</p></div></body></html>",
            {"a": "xy", "b": "yz"},
            {"a": "xz", "b": "yz"},
        ),
        # An auto-escaped value inside a script is not decoded.
        (
            '<div dj-root><script>var s="{{ v }}";</script></div>',
            {"v": "x"},
            {"v": "</script><img src=x onerror=alert(1)>"},
        ),
        # Text directly in a table is moved before it (foster parenting):
        # equal texts must not let the change land on the moved node.
        (
            "<div dj-root><table><tr><td>{{ a }}</td></tr>{{ b }}</table></div>",
            {"a": "same", "b": "same"},
            {"a": "changed", "b": "same"},
        ),
        # An ordinary change still works.
        ("<div dj-root><p>{{ v }}</p></div>", {"v": "x"}, {"v": "a & b"}),
    ],
)
def test_binary_text_update_matches_a_full_render(template, before, after):
    view = RustLiveView(template, [])
    view.update_state(before)
    view.render_binary_diff()
    view.update_state(after)
    html = view.render_binary_diff()[0]

    fresh = RustLiveView(template, [])
    fresh.update_state(after)
    assert html == fresh.render_binary_diff()[0]
    assert "<img" not in html


@pytest.mark.parametrize(
    "cell, value",
    [
        ("{{ a }}", "changed"),  # the fragment fast path
        ("{{ a|safe }}", "<i>changed</i>"),  # the text-region fast path
    ],
)
def test_foster_parented_text_change_matches_a_full_parse(cell, value):
    template = f"<div dj-root><table><tr><td>{cell}</td></tr>{{{{ b }}}}</table></div>"
    first = "<i>same</i>" if "safe" in cell else "same"

    def patches(full_parse: bool) -> str:
        view = RustLiveView(template, [])
        view.update_state({"a": first, "b": "same"})
        view.render_with_diff()
        if full_parse:
            view.clear_fragment_cache()
        view.update_state({"a": value, "b": "same"})
        return view.render_with_diff()[1]

    fast = patches(full_parse=False)
    assert fast == patches(full_parse=True)
    assert '"path":[0]' not in fast, fast
