"""Regression: Rust template renderer must support project-defined custom filters (#1121).

Issue #1121 reported that Django projects registering custom filters via
``@register.filter`` in their ``templatetags/`` modules see them work in the
Python render path but fail in the Rust ``RustLiveView`` render path with::

    RuntimeError: Template error: Unknown filter: lookup

Custom filters are a standard Django extension point. The most common use case
is ``{{ dict|lookup:var_key }}`` for dynamic dict-key access in partials —
Django templates don't support ``{{ dict[var_key] }}``. Without custom-filter
support, partial templates have to take the resolved value as a keyword
argument at every include site.

This test locks down the bridge: Django filter registries are walked at
bridge-init time and exposed to the Rust engine as a lazy lookup (cached by
filter name). The Rust engine, on a built-in filter miss, consults the bridge
and dispatches to the Python callable. ``is_safe`` and ``needs_autoescape``
flags from the Django filter object are honoured so the renderer's
auto-escape / safe-output policy works for custom filters the same way it
does for built-ins.
"""

from __future__ import annotations

import html

import pytest
from django import template
from django.utils.safestring import mark_safe

from djust._rust import RustLiveView, render_template

# A module-level Django Library so each test registers fresh filters
# without polluting another test module.
_test_library = template.Library()


@_test_library.filter(name="lookup")
def _lookup(mapping, key):
    """Dict-key lookup filter — the exact shape from issue #1121."""
    if isinstance(mapping, dict):
        return mapping.get(key, "")
    return ""


@_test_library.filter(name="exclaim")
def _exclaim(value):
    """Plain-text filter (no is_safe) — must be auto-escaped on output."""
    return f"{value}!"


@_test_library.filter(name="bold_html", is_safe=True)
def _bold_html(value):
    """Safe HTML-producing filter (is_safe=True) — must NOT be re-escaped."""
    return mark_safe(f"<b>{value}</b>")


@_test_library.filter(name="prefix")
def _prefix(value, arg):
    """Single-argument filter."""
    return f"{arg}-{value}"


@_test_library.filter(name="autoescape_aware", needs_autoescape=True)
def _autoescape_aware(value, autoescape=True):
    """Filter that adapts to the autoescape context."""
    if autoescape:
        return f"AE:{value}"
    return f"NO_AE:{value}"


# Register them once per test session via the bridge bootstrap. Importing
# `djust.template_filters` triggers the Django-libraries scan; we also
# inject this module's library directly so unit tests don't depend on a
# Django app's templatetags module being loaded.
@pytest.fixture(autouse=True, scope="module")
def _register_custom_filters():
    from djust.template_filters import register_django_filter

    register_django_filter("lookup", _lookup)
    register_django_filter("exclaim", _exclaim)
    register_django_filter("bold_html", _bold_html)
    register_django_filter("prefix", _prefix)
    register_django_filter("autoescape_aware", _autoescape_aware)
    yield
    # Teardown: clear so other modules' tests start clean.
    from djust._rust import clear_custom_filters

    clear_custom_filters()


# ---------------------------------------------------------------------------
# Issue body reproducer: {{ my_dict|lookup:some_key }}
# ---------------------------------------------------------------------------


def test_lookup_filter_resolves_dynamic_dict_key():
    """The exact shape from issue #1121 — bare-identifier filter arg
    is resolved against the context, then passed to the Python callable.
    """
    out = render_template(
        "{{ my_dict|lookup:some_key }}",
        {"my_dict": {"a": "alpha", "b": "beta"}, "some_key": "a"},
    )
    assert html.unescape(out) == "alpha"


def test_lookup_filter_with_missing_key_returns_empty():
    out = render_template(
        "{{ my_dict|lookup:some_key }}",
        {"my_dict": {"a": "alpha"}, "some_key": "missing"},
    )
    assert out == ""


# ---------------------------------------------------------------------------
# Filter signature shapes
# ---------------------------------------------------------------------------


def test_zero_argument_filter():
    """Custom filter with no argument."""
    out = render_template("{{ name|exclaim }}", {"name": "World"})
    assert html.unescape(out) == "World!"


def test_single_argument_filter_with_literal():
    """Single-argument filter with a quoted literal arg."""
    out = render_template('{{ name|prefix:"hello" }}', {"name": "world"})
    assert html.unescape(out) == "hello-world"


def test_single_argument_filter_with_context_variable():
    """Single-argument filter with a bare identifier resolved against context."""
    out = render_template(
        "{{ name|prefix:greeting }}",
        {"name": "world", "greeting": "hi"},
    )
    assert html.unescape(out) == "hi-world"


# ---------------------------------------------------------------------------
# is_safe / autoescape behaviour
# ---------------------------------------------------------------------------


def test_plain_text_filter_is_auto_escaped():
    """A custom filter without ``is_safe=True`` returns a plain string;
    the renderer must HTML-escape it like any other variable output.
    """
    out = render_template("{{ name|exclaim }}", {"name": "<script>"})
    # The literal "<script>" is auto-escaped at render time
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_is_safe_filter_output_is_not_re_escaped():
    """``is_safe=True`` filters return SafeString — the renderer must
    NOT re-escape them, otherwise ``<b>`` becomes ``&lt;b&gt;``.
    """
    out = render_template("{{ name|bold_html }}", {"name": "Hi"})
    assert "<b>Hi</b>" in out
    # Must not be double-escaped:
    assert "&lt;b&gt;" not in out


def test_needs_autoescape_filter_receives_kwarg():
    """``needs_autoescape=True`` filters must receive ``autoescape``
    as a kwarg from the renderer."""
    out = render_template("{{ name|autoescape_aware }}", {"name": "X"})
    # Default autoescape context is True
    assert html.unescape(out) == "AE:X"


# ---------------------------------------------------------------------------
# Negative case — unknown filter still raises
# ---------------------------------------------------------------------------


def test_unknown_filter_still_raises_clear_error():
    """The bridge must NOT silently swallow misses — an unknown filter
    name still produces ``Unknown filter: <name>`` so authors find typos
    and missing imports fast.
    """
    with pytest.raises(Exception) as exc_info:
        render_template(
            "{{ x|definitely_not_a_filter_xyz }}",
            {"x": "val"},
        )
    assert "definitely_not_a_filter_xyz" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Full RustLiveView render path — closing the gap to the original report.
# ---------------------------------------------------------------------------


def test_lookup_filter_in_rust_live_view():
    """The full RustLiveView render path — what issue #1121 actually hit."""
    rv = RustLiveView("<div>{{ urls|lookup:col }}</div>", [])
    rv.update_state(
        {
            "urls": {
                "claim_number": "/sort?col=claim_number",
                "filed_date": "/sort?col=filed_date",
            },
            "col": "filed_date",
        }
    )
    html_out = rv.render()
    assert "/sort?col=filed_date" in html.unescape(html_out)
