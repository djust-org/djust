"""A native handler decides on whether an argument was PASSED, not on whether it is truthy.

An explicit argument wins over a context variable of the same name. The native handlers used
to decide with ``kw.get(name) or context.get(...)``, which treats an explicitly passed empty
value (``rows=[]``, ``items=none_var``, ``current_value=""``) as absent. The Python tag has no
context fallback: ``{% data_table rows=empty %}`` draws the empty state, and the native handler
(every render after the socket opens) must draw the same.

The rule: an argument that was passed is used as given, even when it is empty; the context is
consulted only when the argument is ABSENT (the handlers' documented convenience, kept).

Each case renders the same template through BOTH engines.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from djust.components import rust_handlers
from djust.tests.test_component_dj_attr_parity_3669 import _django
from djust.tests.test_components_csp_aria import _rust

MARKER = "CONTEXT-ROW-7f3a"  # a marker only the context fallback would draw

ROWS = [{"id": 1, "name": MARKER}]
COLS = [{"key": "name", "label": "Name"}]


# --- the table ------------------------------------------------------------


@pytest.mark.parametrize("empty", [[], None, "", {}], ids=repr)
def test_an_explicit_empty_rows_is_not_replaced_by_the_context_rows(empty):
    ctx = {"rows": ROWS, "columns": COLS, "empty": empty}
    src = "{% data_table rows=empty columns=columns %}"
    assert MARKER not in _django(src, ctx), "the Python tag is the reference"
    assert MARKER not in _rust(src, ctx), "the native handler read the context rows"


@pytest.mark.parametrize("empty", [0, False], ids=repr)
def test_an_explicit_falsy_scalar_rows_is_not_replaced_by_the_context_rows(empty):
    """The Python tag cannot iterate a number (it raises); the native handler treats any
    non-list as no rows. Either way the context's rows are not drawn."""
    ctx = {"rows": ROWS, "columns": COLS, "empty": empty}
    assert MARKER not in _rust("{% data_table rows=empty columns=columns %}", ctx)


def test_an_explicit_empty_columns_is_not_replaced_by_the_context_columns():
    ctx = {"rows": ROWS, "columns": COLS, "no_cols": []}
    src = "{% data_table rows=rows columns=no_cols %}"
    assert "Name" not in _django(src, ctx)
    assert "Name" not in _rust(src, ctx)


def test_a_passed_rows_still_wins_over_the_context_rows():
    other = [{"id": 2, "name": "OTHER-ROW"}]
    ctx = {"rows": ROWS, "columns": COLS, "mine": other}
    out = _rust("{% data_table rows=mine columns=columns %}", ctx)
    assert "OTHER-ROW" in out and MARKER not in out


def test_an_absent_rows_still_falls_back_to_the_context_rows():
    """The handlers' convenience, kept. A template cannot reach it for the table (the engine
    refuses ``data_table`` without ``rows`` at parse time), so the handler is called as the
    other ~400 tests call it: with the argument list it was given."""
    handler = rust_handlers.DataTableHandler()
    ctx = {"rows": ROWS, "columns": COLS}
    assert MARKER in handler.render([], ctx)
    assert MARKER not in handler.render(["rows=[]"], ctx)


def test_an_absent_argument_still_falls_back_to_the_context_variable():
    for src, ctx in (
        ("{% breadcrumb %}", {"breadcrumb_items": [{"label": MARKER, "url": "/x/"}]}),
        ("{% stepper %}", {"steps": [{"label": MARKER}]}),
        ('{% autocomplete name="n" %}', {"suggestions": [{"value": "v", "label": MARKER}]}),
        ('{% dependent_select name="n" %}', {"options": [{"value": "v", "label": MARKER}]}),
    ):
        assert MARKER in _rust(src, ctx), src


# --- the other handlers that read the same way ------------------------------------------------

#: (tag source, context, a marker only the CONTEXT fallback would print)
CASES = {
    "breadcrumb": (
        "{% breadcrumb items=no_items %}",
        {"no_items": [], "breadcrumb_items": [{"label": MARKER, "url": "/x/"}]},
    ),
    "stepper": (
        "{% stepper steps=no_steps %}",
        {"no_steps": [], "steps": [{"label": MARKER}]},
    ),
    "dj_select": (
        '{% dj_select name="n" options=no_options options_var="big" %}',
        {"no_options": [], "big": [{"value": "v", "label": MARKER}]},
    ),
    "autocomplete": (
        '{% autocomplete name="n" suggestions=no_sugg %}',
        {"no_sugg": [], "suggestions": [{"value": "v", "label": MARKER}]},
    ),
    "dependent_select": (
        '{% dependent_select name="n" options=no_options %}',
        {"no_options": None, "options": [{"value": "v", "label": MARKER}]},
    ),
}


@pytest.mark.parametrize("tag", sorted(CASES))
def test_an_explicit_empty_argument_does_not_pull_in_the_context_variable(tag):
    src, ctx = CASES[tag]
    assert MARKER not in _rust(src, ctx), f"{tag}: the native handler read the context"


def test_a_radio_with_an_explicit_empty_current_value_is_not_checked_from_the_context():
    """``current_value_var`` is a native-only convenience (the Python tag rejects it); an
    explicit ``current_value`` wins over it, as the Python tag's ``current_value`` does."""
    ctx = {"cur": "", "picked": "a"}
    src = '{% dj_radio name="n" value="a" label="A" current_value=cur current_value_var="picked" %}'
    assert " checked" not in _rust(src, ctx)
    assert " checked" in _rust(src.replace("current_value=cur ", ""), ctx), "absent: falls back"


# --- the pattern, derived from source ---------------------------------------------------------


def _handler_sources() -> list[tuple[str, ast.Module]]:
    root = Path(rust_handlers.__file__).resolve().parent.parent
    files = [
        Path(rust_handlers.__file__),
        root / "theming" / "rust_handlers.py",
        root / "components" / "suspense.py",
    ]
    return [(str(p.relative_to(root)), ast.parse(p.read_text())) for p in files]


def _reads_context(node: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "get"
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "context"
        for n in ast.walk(node)
    )


def test_no_handler_decides_on_truthiness_between_an_argument_and_the_context():
    """``<argument> or context.get(...)`` is the shape. The caller set is every ``or``
    expression in every handler module (found from source, not typed), and each one that
    reads the context on its right-hand side is reported."""
    offenders = []
    for name, tree in _handler_sources():
        for node in ast.walk(tree):
            if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
                if any(_reads_context(v) for v in node.values[1:]):
                    offenders.append(f"{name}:{node.lineno}")
    assert not offenders, (
        "a falsy ARGUMENT falls through to the render context here (decide on presence): "
        + ", ".join(offenders)
    )


def test_the_scan_is_not_vacuous():
    """The same walk finds the old shape in a sample, so a clean result means something."""
    tree = ast.parse('rows = kw.get("rows") or context.get("rows", [])')
    hits = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.BoolOp)
        and isinstance(n.op, ast.Or)
        and any(_reads_context(v) for v in n.values[1:])
    ]
    assert len(hits) == 1
