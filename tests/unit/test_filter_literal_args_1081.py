"""Regression: literal string filter args must not produce JSON-quoted output (#1081).

Issue #1081 reported that ``{{ value|date:"M d, Y" }}`` and
``{{ value|default:"fallback" }}`` rendered with literal double quotes
wrapping the result, e.g. ``"Apr 25, 2026"`` and ``"fallback"``. Root
cause would be the parser preserving surrounding quotes on literal
filter args (intentional, for dep-tracking — see #787) without the
renderer stripping them at render time.

The fix landed in v0.5.2rc1 via ``strip_filter_arg_quotes`` (called from
``render_node_with_loader`` at the two filter-application sites), and a
third inline strip in ``get_value`` for filter chains parsed inside
expressions. This test locks down every shape of literal filter arg the
issue reporter listed across both the issue body and follow-up comments,
plus the equivalent shapes for filter chains and HTML escaping.

If a future refactor regresses any of these, the test fails — preventing
the silent re-emergence of the JSON-quoting bug.
"""

from __future__ import annotations

import html

from djust._rust import render_template


# ---------------------------------------------------------------------------
# |date with literal format string — issue body
# ---------------------------------------------------------------------------


def test_date_filter_literal_format_M_d_Y():
    """``|date:"M d, Y"`` produces ``Apr 25, 2026`` — no surrounding quotes."""
    out = render_template('{{ d|date:"M d, Y" }}', {"d": "2026-04-25"})
    assert html.unescape(out) == "Apr 25, 2026"
    assert "&quot;" not in out
    assert '"' not in html.unescape(out)


def test_date_filter_literal_format_F_j_Y():
    out = render_template('{{ d|date:"F j, Y" }}', {"d": "2026-04-25"})
    assert html.unescape(out) == "April 25, 2026"
    assert "&quot;" not in out


def test_date_filter_literal_format_single_quoted():
    out = render_template("{{ d|date:'M d, Y' }}", {"d": "2026-04-25"})
    assert html.unescape(out) == "Apr 25, 2026"
    assert "&quot;" not in out
    assert "&#x27;" not in out


def test_date_filter_via_dotted_path():
    """``{{ obj.field|date:"M d, Y" }}`` matches a Django-model field path."""
    out = render_template(
        '{{ claim.filed_date|date:"M d, Y" }}',
        {"claim": {"filed_date": "2026-04-25"}},
    )
    assert html.unescape(out) == "Apr 25, 2026"


# ---------------------------------------------------------------------------
# |default with literal string — issue comment 2 and 3
# ---------------------------------------------------------------------------


def test_default_filter_literal_simple_word():
    out = render_template('{{ x|default:"fallback" }}', {"x": ""})
    assert html.unescape(out) == "fallback"
    assert "&quot;" not in out


def test_default_filter_literal_multi_word():
    """Comment 3 — ``|default:"Claims Examiner"`` was reported as quote-wrapped."""
    out = render_template(
        '{{ user_role_display|default:"Claims Examiner" }}',
        {"user_role_display": ""},
    )
    assert html.unescape(out) == "Claims Examiner"
    assert "&quot;" not in out


def test_default_filter_literal_slash():
    out = render_template('{{ x|default:"N/A" }}', {"x": ""})
    assert html.unescape(out) == "N/A"
    assert "&quot;" not in out


def test_default_filter_literal_em_dash():
    out = render_template('{{ x|default:"—" }}', {"x": ""})
    assert html.unescape(out) == "—"
    assert "&quot;" not in out


def test_default_filter_literal_dash():
    out = render_template('{{ x|default:"-" }}', {"x": ""})
    assert html.unescape(out) == "-"
    assert "&quot;" not in out


def test_default_filter_literal_yes_no():
    out = render_template('{{ x|default:"No" }}', {"x": ""})
    assert html.unescape(out) == "No"


def test_default_filter_single_quoted():
    out = render_template("{{ x|default:'fallback' }}", {"x": ""})
    assert html.unescape(out) == "fallback"
    assert "&#x27;" not in out


def test_default_filter_with_truthy_value_passes_through():
    """Literal arg shouldn't appear at all when value is truthy."""
    out = render_template('{{ x|default:"fallback" }}', {"x": "real"})
    assert html.unescape(out) == "real"
    assert "fallback" not in out


def test_default_filter_none_uses_fallback():
    out = render_template('{{ x|default:"fallback" }}', {"x": None})
    assert html.unescape(out) == "fallback"


# ---------------------------------------------------------------------------
# Filter chains — both filters with literal args
# ---------------------------------------------------------------------------


def test_chain_date_then_default():
    out = render_template(
        '{{ d|date:"M d, Y"|default:"N/A" }}',
        {"d": "2026-04-25"},
    )
    assert html.unescape(out) == "Apr 25, 2026"
    assert "&quot;" not in out


def test_chain_default_then_upper():
    out = render_template(
        '{{ x|default:"fallback"|upper }}',
        {"x": ""},
    )
    assert html.unescape(out) == "FALLBACK"
    assert "&quot;" not in out


# ---------------------------------------------------------------------------
# HTML attribute context — html_escape_attr also escapes ``"``,
# so a literal quote left in the value would surface as ``&quot;``
# inside an attribute. Lock down both contexts.
# ---------------------------------------------------------------------------


def test_default_in_html_attribute_no_quote_wrap():
    out = render_template(
        "<div class=\"{{ cls|default:'box' }}\">x</div>",
        {"cls": ""},
    )
    # Attribute context: class="box", not class="&quot;box&quot;"
    assert 'class="box"' in out
    assert "&quot;" not in out


def test_date_in_html_attribute_no_quote_wrap():
    out = render_template(
        "<time datetime=\"{{ d|date:'Y-m-d' }}\">x</time>",
        {"d": "2026-04-25"},
    )
    assert 'datetime="2026-04-25"' in out
    assert "&quot;" not in out
