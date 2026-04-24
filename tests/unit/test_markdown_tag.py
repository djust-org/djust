"""Tests for the ``{% djust_markdown %}`` template tag.

The tag is registered via the djust Rust tag-handler registry (same
mechanism as ``{% url %}`` / ``{% static %}``), so these tests drive it
by calling :func:`djust._rust.render_template` with the appropriate
template source.

Covers:

- Basic rendering of a context variable into HTML.
- ``provisional=False`` kwarg disables the streaming wrapper.
- ``tables=False`` kwarg disables GFM tables.
- **⭐ rule test**: ``<script>`` source payload is rendered as escaped text.
- Rendering inside a ``{% for %}`` loop.
- Streaming append sequence — the stable HTML for an already-completed line
  does not change as more characters are appended to the trailing line.
- (A090 info-level check lives in ``python/tests/test_checks.py``.)
"""

from __future__ import annotations

# Make sure the tag handler is registered before tests run. Importing the
# package executes ``template_tags/__init__._register_builtins()`` which in
# turn imports ``template_tags.markdown`` and triggers @register.
import djust  # noqa: F401

from djust._rust import render_template
from djust.template_tags.markdown import MarkdownTagHandler


def test_tag_basic_render():
    out = render_template(
        "{% djust_markdown body %}",
        {"body": "**bold** and *em*"},
    )
    assert "<strong>bold</strong>" in out
    assert "<em>em</em>" in out


def test_tag_provisional_kwarg_off():
    # Trailing mid-token line — with provisional=False the splitter is bypassed
    # and pulldown-cmark sees the unclosed ** as literal text.
    out = render_template(
        "{% djust_markdown body provisional=False %}",
        {"body": "Hello **wor"},
    )
    assert 'class="djust-md-provisional"' not in out
    # With the default (provisional=True) we'd see the wrapper.
    out_default = render_template(
        "{% djust_markdown body %}",
        {"body": "Hello **wor"},
    )
    assert 'class="djust-md-provisional"' in out_default


def test_tag_tables_kwarg():
    src = "| a | b |\n| - | - |\n| 1 | 2 |\n"
    on = render_template(
        "{% djust_markdown body %}",
        {"body": src},
    )
    off = render_template(
        "{% djust_markdown body tables=False %}",
        {"body": src},
    )
    assert "<table>" in on
    assert "<table>" not in off


def test_tag_escapes_script_tags():
    """⭐ Rule test: a <script> payload in the Markdown source is escaped."""
    out = render_template(
        "{% djust_markdown body %}",
        {"body": "<script>alert('xss')</script>"},
    )
    assert "<script>" not in out, "tag MUST NOT emit raw <script> from source text; got: %s" % out
    assert "&lt;script&gt;" in out


def test_tag_inside_for_loop():
    out = render_template(
        "{% for msg in msgs %}<div>{% djust_markdown msg %}</div>{% endfor %}",
        {"msgs": ["**one**", "`two`", "# three"]},
    )
    assert "<strong>one</strong>" in out
    assert "<code>two</code>" in out
    assert "<h1>three</h1>" in out
    # Three wrapping divs.
    assert out.count("<div>") == 3


def test_tag_streaming_append_sequence():
    """Simulate an LLM streaming one character at a time into `llm_output`.

    Assertion: once a line has ended with '\\n', its HTML must remain
    byte-stable across subsequent appends to the in-progress trailing line.
    """
    prefix = "hello world\n"
    out_prev = None
    for tail in ["", "*", "**w", "**wor", "**word"]:
        body = prefix + tail
        out = render_template(
            "{% djust_markdown body %}",
            {"body": body},
        )
        head, _, _ = out.partition('<p class="djust-md-provisional">')
        if out_prev is not None:
            assert head == out_prev, (
                "stable prefix changed between stream chunks:\n"
                "prev: %r\ncurrent: %r" % (out_prev, head)
            )
        out_prev = head
        # Paragraph for 'hello world' always present.
        assert "<p>hello world</p>" in out


def test_tag_import_error_fallback(monkeypatch):
    """If djust._rust.render_markdown is unavailable, the handler falls
    back to Django's escape() — the page still renders, just without
    Markdown formatting.
    """
    # Delete the attribute so `from djust._rust import render_markdown`
    # raises ImportError, exercising the fallback branch.
    monkeypatch.delattr(
        "djust._rust.render_markdown",
        raising=False,
    )

    handler = MarkdownTagHandler()
    out = handler.render(
        ["body"],
        {"body": "<script>alert(1)</script>"},
    )
    assert out == "&lt;script&gt;alert(1)&lt;/script&gt;", (
        "fallback path must Django-escape the source, got: %r" % out
    )
