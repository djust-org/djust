"""Regression tests for TagInput (#932 + #949).

#932 — TagInput rendered no ``name=`` attribute on the hidden input, so
form POSTs dropped the tag list silently.

#949 — The hidden input's value was a comma-joined string, ambiguous if a
tag contained a comma. Fixed by serializing as JSON.
"""

import json
import re

import tests.conftest  # noqa: F401  -- configure Django settings

from djust.components.components.tag_input import TagInput


# ---- #932 regressions ---------------------------------------------------


def test_tag_input_renders_name_attribute():
    """Bug #932: TagInput output must include ``name="<self.name>"``."""
    comp = TagInput(name="skills", tags=["python", "rust"])
    html = comp._render_custom()

    assert 'name="skills"' in html, (
        "TagInput output must carry a `name=` attribute so form POSTs include the field value"
    )


def test_tag_input_hidden_input_carries_json_array_of_tags():
    """The hidden input's value is the JSON-encoded tag list (#949)."""
    comp = TagInput(name="topics", tags=["a", "b", "c"])
    html = comp._render_custom()

    assert 'type="hidden"' in html
    assert 'name="topics"' in html
    # JSON array of the three tags. `json.dumps` produces
    # `["a", "b", "c"]`; `html.escape` converts the `"` to `&quot;`.
    assert "&quot;a&quot;" in html
    assert "&quot;b&quot;" in html
    assert "&quot;c&quot;" in html
    # Old CSV format MUST NOT appear — that would be the #949 regression.
    assert 'value="a,b,c"' not in html


def test_tag_input_no_name_emits_no_hidden_input():
    """With an empty name we omit the hidden input entirely."""
    comp = TagInput(name="", tags=["x"])
    html = comp._render_custom()

    # No stray hidden input when the component has no name
    assert 'type="hidden"' not in html


def test_tag_input_hidden_input_value_is_html_escaped():
    """Tag values containing HTML-special chars must be escaped inside the hidden input."""
    comp = TagInput(name="tags", tags=['<script>alert("x")</script>'])
    html = comp._render_custom()

    assert "<script>" not in html.split('type="hidden"', 1)[-1].split(">", 1)[0] + ">"
    assert "&lt;script&gt;" in html


# ---- #949 regression: commas-in-values round-trip ------------------------


def _hidden_value_from(html_str: str) -> str:
    """Extract the raw (HTML-escaped) value from the hidden input."""
    m = re.search(r'<input type="hidden" name="[^"]+" value="([^"]*)"', html_str)
    assert m, f"no hidden input found in: {html_str!r}"
    return m.group(1)


def _decode_hidden_value(html_str: str) -> list:
    """Reverse `html.escape(json.dumps(...))` — the server-side path."""
    import html as _html_mod

    raw = _hidden_value_from(html_str)
    # html.escape -> html.unescape to recover the JSON text.
    return json.loads(_html_mod.unescape(raw))


def test_tag_input_values_with_commas_round_trip():
    """Bug #949: tags containing commas must decode intact on the server.

    Before the fix the hidden value was ``"tag,with,commas,plain"``, which
    ``.split(",")`` would wrongly yield four items. After the fix the
    value is JSON-encoded, so ``json.loads`` recovers the original list
    verbatim — including any commas inside individual tag values.
    """
    tags = ["tag,with,commas", "plain"]
    comp = TagInput(name="labels", tags=tags)
    html = comp._render_custom()

    decoded = _decode_hidden_value(html)
    assert decoded == tags, "tags containing commas must survive the hidden-input round-trip"
    assert len(decoded) == 2  # specifically, NOT 4 as the old CSV split would yield


def test_tag_input_values_with_quotes_and_html_round_trip():
    """Tags containing quotes / HTML-special chars also round-trip via JSON."""
    tags = ['he said "hi"', "a<b&c", "trailing,comma,"]
    comp = TagInput(name="labels", tags=tags)
    html = comp._render_custom()

    decoded = _decode_hidden_value(html)
    assert decoded == tags
