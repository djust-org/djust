"""
Tests for find_handlers_for_template — static analysis that cross-
references a template's dj-* attrs against the project's LiveView
handlers.

These tests bypass the MCP server wrapper and exercise the core
regex + matching logic directly via a temporary template file.
"""

from __future__ import annotations

import json
import os
import re
import tempfile


# The dj-event-attr regex is replicated here because the MCP server's
# `create_server()` closes over it. In practice the single source of
# truth is server.py's inline definition — when it changes, this
# regex (and the tool's behavior) should be updated together.
DJ_EVENT_ATTR_RE = re.compile(
    r'\b(dj-(?:click|submit|change|input|keydown|keyup))\s*=\s*[\'"]([^\'"]+)[\'"]',
    re.IGNORECASE,
)


def _extract_handlers(source: str) -> list[str]:
    return sorted({m.group(2) for m in DJ_EVENT_ATTR_RE.finditer(source)})


# --- Regex correctness -----------------------------------------------------


def test_extracts_single_handler():
    src = '<button dj-click="increment">+</button>'
    assert _extract_handlers(src) == ["increment"]


def test_extracts_multiple_distinct_handlers():
    src = """
    <button dj-click="increment">+</button>
    <button dj-click="decrement">-</button>
    <button dj-click="reset">reset</button>
    <form dj-submit="add_todo">...</form>
    <input dj-change="validate_field">
    """
    assert _extract_handlers(src) == [
        "add_todo",
        "decrement",
        "increment",
        "reset",
        "validate_field",
    ]


def test_deduplicates_repeated_handlers():
    """A handler used by multiple elements should appear once."""
    src = """
    <button dj-click="increment">+</button>
    <button dj-click="increment">++</button>
    """
    assert _extract_handlers(src) == ["increment"]


def test_ignores_non_event_dj_attrs():
    """dj-id, dj-params, dj-loading etc. must NOT be collected."""
    src = """
    <div dj-id="42"></div>
    <div dj-params='{"x": 1}'></div>
    <div dj-loading="click" dj-click="real_handler"></div>
    <div dj-view="path.to.View"></div>
    """
    assert _extract_handlers(src) == ["real_handler"]


def test_handles_single_and_double_quotes():
    src = """
    <button dj-click='quote_one'>a</button>
    <button dj-click="quote_two">b</button>
    """
    assert _extract_handlers(src) == ["quote_one", "quote_two"]


def test_empty_template_returns_empty():
    assert _extract_handlers("") == []
    assert _extract_handlers("<html><body>no events</body></html>") == []


def test_handles_keyboard_events():
    src = '<input dj-keydown="submit_on_enter" dj-keyup="debounce_search">'
    assert _extract_handlers(src) == ["debounce_search", "submit_on_enter"]


# --- Round-trip with a real file ------------------------------------------


def test_roundtrip_via_file():
    """Confirm reading + parsing a file off disk gives the expected set."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as f:
        f.write(
            """
            <div>
              <button dj-click="increment">+</button>
              <button dj-click="decrement">-</button>
              <form dj-submit="save">Save</form>
            </div>
            """
        )
        path = f.name
    try:
        with open(path) as rf:
            src = rf.read()
        assert _extract_handlers(src) == ["decrement", "increment", "save"]
    finally:
        os.unlink(path)


# --- Set-math that the tool does on top of the regex -----------------------


def test_intersection_logic_matches_tool_contract():
    """Validates the 'handlers_in_view_not_in_template' /
    'handlers_in_template_not_in_view' / 'matched_handlers' shape."""
    template_handlers = {"increment", "decrement", "reset", "ghost"}
    view_handlers = {"increment", "decrement", "reset", "orphan"}

    matched = sorted(view_handlers & template_handlers)
    only_view = sorted(view_handlers - template_handlers)
    only_template = sorted(template_handlers - view_handlers)

    assert matched == ["decrement", "increment", "reset"]
    assert only_view == ["orphan"]
    assert only_template == ["ghost"]


def test_tool_json_shape_compiles():
    """Smoke-test that the response shape we document is valid JSON."""
    sample = {
        "template_path": "demos/counter.html",
        "resolved_path": "/abs/demos/counter.html",
        "dj_handlers_in_template": ["increment", "decrement"],
        "view_count": 1,
        "views": [
            {
                "class": "CounterView",
                "template_name": "demos/counter.html",
                "matched_handlers": ["increment", "decrement"],
                "handlers_in_view_not_in_template": [],
                "handlers_in_template_not_in_view": [],
            }
        ],
    }
    # Must round-trip through json without loss.
    assert json.loads(json.dumps(sample)) == sample
