"""
Tests for attribute-context HTML escaping.

Variables rendered inside HTML attribute values get an attribute-
safe escape pass (`"` → `&quot;`, `'` → `&#x27;`) even though the
base escape already handles these characters. Ensures `|safe`
continues to bypass escaping in both contexts.
"""

from djust._rust import RustLiveView


def _render(template: str, **state) -> str:
    view = RustLiveView(template)
    view.update_state(state)
    return view.render()


def test_double_quote_escaped_inside_attribute():
    """A `"` in an href attr must render as `&quot;`."""
    html = _render(
        '<a href="{{ url }}">link</a>',
        url='https://example.com/?x="a"',
    )
    assert "&quot;" in html
    # The raw unescaped `"` from the url must NOT appear verbatim in
    # the rendered attribute value — it would break the attribute.
    assert 'x="a"' not in html


def test_safe_filter_bypasses_attribute_escape():
    """`|safe` inside an attribute must emit raw quotes."""
    html = _render(
        '<a href="{{ url|safe }}">link</a>',
        url='https://example.com/?x="a"',
    )
    # safe filter output is trusted — quote is emitted raw
    assert '?x="a"' in html
    # Because it's safe, no entity should be present
    assert "&quot;" not in html


def test_text_context_also_escapes_quotes():
    """Non-attribute context escape keeps Django parity (quotes still escaped)."""
    html = _render(
        "<div>{{ text }}</div>",
        text='"quote"',
    )
    assert "&quot;quote&quot;" in html


def test_single_quote_attribute_escapes_apostrophe():
    """A single-quoted attribute with a `'` value renders `&#x27;`."""
    html = _render(
        "<div title='{{ note }}'>body</div>",
        note="it's fine",
    )
    assert "&#x27;" in html


def test_no_attr_uses_regular_escape_with_ampersands():
    """Ampersand handling in plain text context."""
    html = _render(
        "<div>{{ a }}&amp;{{ b }}</div>",
        a="X",
        b="Y",
    )
    # Base text content should escape `<`, `>`, `&` consistently.
    assert html == "<div>X&amp;Y</div>"


def test_attribute_with_filter_still_escapes():
    """Running a filter before attribute rendering preserves escaping."""
    html = _render(
        '<input type="text" value="{{ v|upper }}">',
        v='a"b',
    )
    # |upper runs first, producing `A"B`, which is then attr-escaped.
    assert "&quot;" in html
    assert 'value="A"B"' not in html


def test_multiline_attribute_spans_escape():
    """Multi-line attribute values are handled (parser reuses token state)."""
    html = _render(
        '<div\n  title="{{ v }}">body</div>',
        v='a"b',
    )
    assert "&quot;" in html


def test_nested_quotes_in_attribute_value():
    """Both quote types in the same value get escaped."""
    html = _render(
        '<a href="{{ url }}">x</a>',
        url="foo\"bar'baz",
    )
    assert "&quot;" in html
    assert "&#x27;" in html
