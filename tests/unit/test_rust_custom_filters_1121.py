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

.. note::
   This file is **not safe to run in parallel** with other tests that
   touch the Rust filter registry (#1162). The module-scoped autouse
   fixture ``_register_custom_filters`` registers filters at module
   setup and clears the global ``FILTER_REGISTRY`` at teardown.
   Parallel test runs (e.g. ``pytest-xdist``) that interleave
   registration calls from another module would race. The
   ``scope="module"`` keeps the registry stable across the tests in
   this file; cross-module isolation requires running serially.
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
#
# IMPORTANT (#1162): scope="module" — registration happens once per file
# import, teardown clears the registry once after the last test in this
# file. A function-scoped clear-then-register would race with parallel
# tests in another file that register at the same global Mutex. Even at
# module scope, this file is **not safe to run in parallel with other
# files** that touch the Rust filter registry; run those serially or in
# separate worker processes.
@pytest.fixture(autouse=True, scope="module")
def _register_custom_filters():
    from djust.template_filters import register_django_filter

    register_django_filter("lookup", _lookup)
    register_django_filter("exclaim", _exclaim)
    register_django_filter("bold_html", _bold_html)
    register_django_filter("prefix", _prefix)
    register_django_filter("autoescape_aware", _autoescape_aware)
    yield
    # Teardown: clear so other modules' tests start clean. Note: clear
    # only empties the HashMap — the per-process
    # ``ANY_CUSTOM_FILTERS_REGISTERED`` AtomicBool guard intentionally
    # stays ``true`` for the rest of the process. See the Rust
    # ``filter_registry`` doc comment for why.
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
    as a kwarg from the renderer.

    Limitation (#1180 item 2, acknowledged): this test only confirms
    the **API surface** — the ``autoescape`` kwarg arrives — not the
    new **dynamic semantics**. The renderer hardcodes ``autoescape=true``
    at every call site (``renderer.rs:287, 349, 1602`` per the v0.9.2
    PR #1179 retro), so the filter receives ``True`` regardless of
    whether the dispatcher's parameterization at ``filter_registry.rs:290``
    is wired correctly. A test that genuinely exercises the bool would
    need either:

    - A renderer where the autoescape supply path can vary (i.e., the
      future ``{% autoescape %}`` block-tracking work; ~4 sites need
      plumbing per the corrected CHANGELOG entry for #1162).
    - A Python-exposed ``apply_custom_filter`` shim that lets a test
      pass an explicit ``autoescape: bool`` (would need a new PyO3
      binding; the current ``apply_custom_filter`` is internal Rust).

    Both are larger than #1180's scope. The test is left in this weak
    form intentionally, with this docstring as the audit trail; the
    deferred strengthening rides on the next ``{% autoescape %}`` work.
    """
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

    Tightened (#1162): assert the canonical message shape
    ``Unknown filter: <name>`` rather than just substring-matching the
    name. A future regression that, say, reformatted the error to
    ``filter not found`` would slip past the looser check.
    """
    # ``RuntimeError`` is what the Rust extension raises for any template
    # error reaching Python. We pin the type to catch a future regression
    # where the bridge swallows it into a different error class.
    with pytest.raises(RuntimeError) as exc_info:
        render_template(
            "{{ x|definitely_not_a_filter_xyz }}",
            {"x": "val"},
        )
    msg = str(exc_info.value)
    # Canonical shape: ``Unknown filter: <name>`` (from filters.rs:510).
    assert "Unknown filter:" in msg, f"unexpected error shape: {msg!r}"
    assert "definitely_not_a_filter_xyz" in msg


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


# ---------------------------------------------------------------------------
# Polish from #1162 — Stage 11 review of #1161
# ---------------------------------------------------------------------------


class TestNewBehavior_1162:
    """New regression cases added in PR #1162 for the polish sub-items
    deferred from PR #1161's Stage 11 review.

    These cover:
    - sub-item 2: ``autoescape`` param flows from renderer through
      ``apply_filter_full`` → ``apply_custom_filter`` → Python kwarg.
    - sub-item 6: async (``async def``) filters are rejected loudly
      with a clear error instead of silently emitting
      ``"<coroutine object ...>"`` into the rendered HTML.
    """

    def test_async_filter_raises_clear_error(self):
        """An ``async def`` filter must be rejected with a clear,
        actionable error — not silently stringify the coroutine into
        the HTML output (sub-item 6 of #1162).
        """
        from djust._rust import register_custom_filter, unregister_custom_filter

        async def _async_broken(value):
            return f"async:{value}"

        register_custom_filter("async_broken_1162", _async_broken, False, False)
        try:
            with pytest.raises(RuntimeError) as exc_info:
                render_template(
                    "{{ x|async_broken_1162 }}",
                    {"x": "v"},
                )
            msg = str(exc_info.value)
            # Error must name the filter, name the problem, and suggest
            # a fix. We don't pin the exact phrasing — but all three
            # must be present.
            assert "async_broken_1162" in msg, msg
            assert "async" in msg.lower(), msg
            assert "sync" in msg.lower() or "def" in msg or "coroutine" in msg.lower(), msg
        finally:
            unregister_custom_filter("async_broken_1162")

    def test_needs_autoescape_filter_receives_autoescape_kwarg(self):
        """The ``autoescape`` param threads from the renderer through
        the bridge (sub-item 2 of #1162). Today the renderer always
        passes ``true``; this test asserts the kwarg arrives at the
        Python callable so the future ``{% autoescape off %}`` block
        feature has a working substrate.
        """
        from djust._rust import register_custom_filter, unregister_custom_filter

        captured = {}

        def _autoescape_capturing(value, autoescape=None):
            captured["autoescape"] = autoescape
            return f"v={value}|ae={autoescape}"

        # Mark needs_autoescape via the function attribute Django uses.
        _autoescape_capturing.needs_autoescape = True

        register_custom_filter("ae_capture_1162", _autoescape_capturing, False, True)
        try:
            out = render_template(
                "{{ x|ae_capture_1162 }}",
                {"x": "hi"},
            )
            # Today: renderer hardcodes True (the historical behaviour).
            # When autoescape-block tracking lands, the renderer will
            # vary this — but the kwarg MUST flow through either way.
            assert captured["autoescape"] is True
            assert "ae=True" in html.unescape(out)
        finally:
            unregister_custom_filter("ae_capture_1162")
