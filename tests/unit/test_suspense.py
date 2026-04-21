"""Tests for the ``{% dj_suspense %}`` block tag handler (v0.5.0)."""

from __future__ import annotations

from unittest.mock import patch

from djust.async_result import AsyncResult
from djust.components.suspense import SuspenseTagHandler, _DEFAULT_FALLBACK_HTML


def _handler():
    return SuspenseTagHandler()


# ---------------------------------------------------------------------------
# State resolution
# ---------------------------------------------------------------------------


def test_all_ok_renders_body():
    ctx = {"metrics": AsyncResult.succeeded({"n": 42})}
    out = _handler().render(['await="metrics"'], "<p>body</p>", ctx)
    assert out == "<p>body</p>"


def test_any_loading_renders_default_fallback():
    ctx = {
        "metrics": AsyncResult.succeeded({"n": 1}),
        "chart": AsyncResult.pending(),
    }
    out = _handler().render(['await="metrics,chart"'], "<p>body</p>", ctx)
    assert out == _DEFAULT_FALLBACK_HTML
    assert "<p>body</p>" not in out


def test_any_failed_renders_error_div():
    ctx = {
        "metrics": AsyncResult.errored(RuntimeError("db down")),
    }
    out = _handler().render(['await="metrics"'], "<p>body</p>", ctx)
    assert 'class="djust-suspense-error"' in out
    assert "db down" in out
    assert "<p>body</p>" not in out


def test_error_message_is_html_escaped():
    ctx = {"metrics": AsyncResult.errored(ValueError("<script>x</script>"))}
    out = _handler().render(['await="metrics"'], "<p>body</p>", ctx)
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_no_await_arg_passthrough():
    ctx = {"metrics": AsyncResult.pending()}
    out = _handler().render([], "<p>body</p>", ctx)
    assert out == "<p>body</p>"


def test_unknown_ref_treated_as_loading():
    # ``metrics`` is missing from the context entirely — must default to
    # loading, NOT silently render the body.
    ctx: dict = {}
    out = _handler().render(['await="metrics"'], "<p>body</p>", ctx)
    assert out == _DEFAULT_FALLBACK_HTML


def test_non_async_result_value_treated_as_loading():
    # Plain strings / numbers in place of AsyncResult are treated as still
    # loading to avoid accidentally passing through a non-wrapped value.
    ctx = {"metrics": "raw-not-async"}
    out = _handler().render(['await="metrics"'], "<p>body</p>", ctx)
    assert out == _DEFAULT_FALLBACK_HTML


def test_fallback_arg_missing_returns_default_spinner():
    ctx = {"metrics": AsyncResult.pending()}
    out = _handler().render(['await="metrics"'], "<p>body</p>", ctx)
    assert "djust-suspense-fallback" in out
    assert "Loading" in out


def test_fallback_template_loaded_via_django():
    ctx = {"metrics": AsyncResult.pending()}
    with patch(
        "django.template.loader.render_to_string", return_value="<span>SKELETON</span>"
    ) as mock_render:
        out = _handler().render(['await="metrics"', 'fallback="skel.html"'], "<p>body</p>", ctx)
    mock_render.assert_called_once_with("skel.html", ctx)
    assert out == "<span>SKELETON</span>"


def test_fallback_template_error_falls_back_to_default():
    ctx = {"metrics": AsyncResult.pending()}
    with patch(
        "django.template.loader.render_to_string",
        side_effect=RuntimeError("template missing"),
    ):
        out = _handler().render(['await="metrics"', 'fallback="missing.html"'], "<p>body</p>", ctx)
    assert out == _DEFAULT_FALLBACK_HTML


# ---------------------------------------------------------------------------
# Nesting
# ---------------------------------------------------------------------------


def test_nested_suspense_resolves_independently():
    # Inner boundary awaits ``inner`` which is still loading.
    # Outer boundary awaits ``outer`` which is ok. The outer body contains
    # the already-rendered inner result (fallback), verifying that nesting
    # composes by the Rust renderer rendering inner first, then outer.
    inner_ctx = {"inner": AsyncResult.pending()}
    inner_rendered = _handler().render(['await="inner"'], "<b>inner-body</b>", inner_ctx)
    assert inner_rendered == _DEFAULT_FALLBACK_HTML

    outer_ctx = {"outer": AsyncResult.succeeded("yes")}
    outer_rendered = _handler().render(
        ['await="outer"'], f"outer-pre {inner_rendered} outer-post", outer_ctx
    )
    assert "outer-pre" in outer_rendered
    assert "outer-post" in outer_rendered
    assert _DEFAULT_FALLBACK_HTML in outer_rendered


def test_comma_separated_await_names_whitespace_tolerant():
    ctx = {
        "a": AsyncResult.succeeded(1),
        "b": AsyncResult.succeeded(2),
        "c": AsyncResult.succeeded(3),
    }
    out = _handler().render(['await=" a , b , c "'], "<p>all ok</p>", ctx)
    assert out == "<p>all ok</p>"
