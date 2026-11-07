"""A native handler decides on whether an argument was PASSED, not on whether it is truthy.

Native handlers read an argument as ``kw.get(name) or context.get(...)``. An explicitly
passed EMPTY value (``rows=[]``, ``items=none_var``, ``current_value=""``) is falsy, so the
handler used the render context's variable of the same job instead. The Python tag has no
context fallback: ``{% data_table rows=empty %}`` draws the empty state, and the native handler
(every render after the socket opens) must draw the same.

The rule: an argument that was passed is used as given, even when it is empty; the context is
consulted only when the argument is ABSENT (the handlers' documented convenience, kept).

Each case renders the same template through BOTH engines.
"""

from __future__ import annotations

import ast
from html.parser import HTMLParser
from pathlib import Path

import pytest

from django.template import Context, Engine

from djust._rust import RustLiveView
from djust.components import rust_handlers
from djust.components.rust_handlers import register_with_rust_engine

_ENGINE = Engine(
    app_dirs=True,
    libraries={
        "djust_components": "djust.components.templatetags.djust_components",
        "live_tags": "djust.templatetags.live_tags",
    },
)


def _django(src: str, ctx: dict | None = None) -> str:
    return _ENGINE.from_string("{% load djust_components %}" + src).render(Context(ctx or {}))


def _rust(src: str, ctx: dict | None = None) -> str:
    register_with_rust_engine()
    rv = RustLiveView("<div dj-root>" + src + "</div>")
    if ctx:
        rv.update_state(ctx)
    out = rv.render()
    return out[len("<div dj-root>") : -len("</div>")]


CONTEXT_MARKER = "CONTEXT-ROW-7f3a"

ROWS = [{"id": 1, "name": CONTEXT_MARKER}]
COLS = [{"key": "name", "label": "Name"}]


# --- the table ------------------------------------------------------------


@pytest.mark.parametrize("empty", [[], None, "", {}], ids=repr)
def test_an_explicit_empty_rows_is_not_replaced_by_the_context_rows(empty):
    ctx = {"rows": ROWS, "columns": COLS, "empty": empty}
    src = "{% data_table rows=empty columns=columns %}"
    assert CONTEXT_MARKER not in _django(src, ctx), "the Python tag is the reference"
    assert CONTEXT_MARKER not in _rust(src, ctx), "the native handler used the context's rows"


@pytest.mark.parametrize("empty", [0, False], ids=repr)
def test_an_explicit_falsy_scalar_rows_is_not_replaced_by_the_context_rows(empty):
    """The Python tag cannot iterate a number (it raises); the native handler treats any
    non-list as no rows. Either way the context's rows are not drawn."""
    ctx = {"rows": ROWS, "columns": COLS, "empty": empty}
    assert CONTEXT_MARKER not in _rust("{% data_table rows=empty columns=columns %}", ctx)


def test_an_explicit_empty_columns_is_not_replaced_by_the_context_columns():
    ctx = {"rows": ROWS, "columns": COLS, "no_cols": []}
    src = "{% data_table rows=rows columns=no_cols %}"
    assert "Name" not in _django(src, ctx)
    assert "Name" not in _rust(src, ctx)


def test_a_passed_rows_still_wins_over_the_context_rows():
    other = [{"id": 2, "name": "OTHER-ROW"}]
    ctx = {"rows": ROWS, "columns": COLS, "mine": other}
    out = _rust("{% data_table rows=mine columns=columns %}", ctx)
    assert "OTHER-ROW" in out and CONTEXT_MARKER not in out


def test_an_absent_rows_still_falls_back_to_the_context_rows():
    """The handlers' convenience, kept. A template cannot reach it for the table (the engine
    refuses ``data_table`` without ``rows`` at parse time), so the handler is called as the
    other ~400 tests call it: with the argument list it was given."""
    handler = rust_handlers.DataTableHandler()
    ctx = {"rows": ROWS, "columns": COLS}
    assert CONTEXT_MARKER in handler.render([], ctx)
    assert CONTEXT_MARKER not in handler.render(["rows=[]"], ctx)


def test_an_absent_argument_still_falls_back_to_the_context_variable():
    for src, ctx in (
        ("{% breadcrumb %}", {"breadcrumb_items": [{"label": CONTEXT_MARKER, "url": "/x/"}]}),
        ("{% stepper %}", {"steps": [{"label": CONTEXT_MARKER}]}),
        ('{% autocomplete name="n" %}', {"suggestions": [{"value": "v", "label": CONTEXT_MARKER}]}),
        ('{% dependent_select name="n" %}', {"options": [{"value": "v", "label": CONTEXT_MARKER}]}),
    ):
        assert CONTEXT_MARKER in _rust(src, ctx), src


# --- the other handlers that read the same way ------------------------------------------------

#: (tag source, context, a marker only the CONTEXT fallback would print)
CASES = {
    "breadcrumb": (
        "{% breadcrumb items=no_items %}",
        {"no_items": [], "breadcrumb_items": [{"label": CONTEXT_MARKER, "url": "/x/"}]},
    ),
    "stepper": (
        "{% stepper steps=no_steps %}",
        {"no_steps": [], "steps": [{"label": CONTEXT_MARKER}]},
    ),
    "dj_select": (
        '{% dj_select name="n" options=no_options options_var="big" %}',
        {"no_options": [], "big": [{"value": "v", "label": CONTEXT_MARKER}]},
    ),
    "autocomplete": (
        '{% autocomplete name="n" suggestions=no_sugg %}',
        {"no_sugg": [], "suggestions": [{"value": "v", "label": CONTEXT_MARKER}]},
    ),
    "dependent_select": (
        '{% dependent_select name="n" options=no_options %}',
        {"no_options": None, "options": [{"value": "v", "label": CONTEXT_MARKER}]},
    ),
}


@pytest.mark.parametrize("tag", sorted(CASES))
def test_an_explicit_empty_argument_does_not_pull_in_the_context_variable(tag):
    src, ctx = CASES[tag]
    assert CONTEXT_MARKER not in _rust(src, ctx), f"{tag}: the native handler read the context"


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


def _is_kw_get(call: ast.AST) -> bool:
    return (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "get"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id in ("kw", "kwargs")
    )


def _none_tested_name(test: ast.AST) -> str | None:
    """``x`` for ``x is None`` and ``not x``."""
    if (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Is)
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value is None
    ):
        return test.left.id
    if (
        isinstance(test, ast.UnaryOp)
        and isinstance(test.op, ast.Not)
        and isinstance(test.operand, ast.Name)
    ):
        return test.operand.id
    return None


def _offenders(name: str, tree: ast.AST) -> list[str]:
    """Where a handler lets a missing-OR-empty argument fall through to the render context:
    ``<arg> or context.get(...)``, and ``x = kw.get(...)`` then ``if x is None: ...context.get(...)``
    (also ``if not x``). An argument that was passed, even as ``None``, is used as given."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            if any(_reads_context(v) for v in node.values[1:]):
                out.append(f"{name}:{node.lineno} (or)")
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        arguments = {
            t.id
            for n in ast.walk(fn)
            if isinstance(n, ast.Assign) and _is_kw_get(n.value)
            for t in n.targets
            if isinstance(t, ast.Name)
        }
        for n in ast.walk(fn):
            if isinstance(n, ast.If) and _none_tested_name(n.test) in arguments:
                if any(_reads_context(stmt) for stmt in n.body):
                    out.append(f"{name}:{n.lineno} (is None)")
    return out


def test_no_handler_decides_on_truthiness_between_an_argument_and_the_context():
    """The caller set is every function in every handler module (found from source, not typed)."""
    offenders = [o for name, tree in _handler_sources() for o in _offenders(name, tree)]
    assert not offenders, (
        "a falsy or None ARGUMENT falls through to the render context here (decide on presence): "
        + ", ".join(offenders)
    )


def test_the_scan_is_not_vacuous():
    """The same walk finds both old shapes in samples, so a clean result means something."""
    assert len(_offenders("s", ast.parse('rows = kw.get("rows") or context.get("rows", [])'))) == 1
    sample = (
        "def render(self, args, context):\n"
        "    kw = _parse_args(args, context)\n"
        '    form = kw.get("form", None)\n'
        "    if form is None:\n"
        '        form = context.get("form", None)\n'
    )
    assert len(_offenders("s", ast.parse(sample))) == 1
    passed = sample.replace('kw.get("form", None)', "_passed_or_context(kw, 'form', context)")
    assert _offenders("s", ast.parse(passed)) == []


# --- handlers that read the context outside the ``or`` shape ----------------------------------


def test_form_errors_with_an_explicit_none_form_draws_nothing():
    """The Python tag draws nothing for ``form=None``; the handler took the context's ``form``."""

    class _Form:
        def non_field_errors(self):
            return ["A form-level problem"]

    handler = rust_handlers.FormErrorsHandler()
    ctx = {"form": _Form()}
    assert handler.render(["form=None"], ctx) == ""
    assert "A form-level problem" in handler.render([], ctx), "an ABSENT form still falls back"


def test_toast_container_uses_the_toasts_it_was_given():
    """The handler ignored its argument and always drew the context's ``toasts``; the Python tag
    draws what it is given."""
    ctx = {"toasts": [{"id": "1", "type": "info", "message": CONTEXT_MARKER}], "none": []}
    src = "{% toast_container toasts=none %}"
    assert CONTEXT_MARKER not in _django(src, ctx), "the Python tag is the reference"
    assert CONTEXT_MARKER not in _rust(src, ctx)
    other = {"toasts": ctx["toasts"], "mine": [{"id": "2", "type": "info", "message": "MINE"}]}
    out = _rust("{% toast_container toasts=mine %}", other)
    assert "MINE" in out and CONTEXT_MARKER not in out
    assert CONTEXT_MARKER in _rust("{% toast_container %}", ctx), (
        "an ABSENT argument still falls back"
    )


def test_toast_container_given_something_that_is_not_a_list_draws_an_empty_container():
    out = rust_handlers.ToastContainerHandler().render(["toasts=5"], {"toasts": [{"message": "x"}]})
    assert out == '<div class="toast-container"></div>'


# --- ToastContainer: the arguments it now reads ----------------------------------------------


class _Tags(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def _toast_markup(toast: dict, **ctx_extra) -> tuple[str, list[tuple[str, dict]]]:
    ctx = {"ts": [toast], **ctx_extra}
    args = ["toasts=ts"] + (["dismiss_event=ev"] if "ev" in ctx_extra else [])
    out = rust_handlers.ToastContainerHandler().render(args, ctx)
    parser = _Tags()
    parser.feed(out)
    return out, parser.tags


def test_toast_container_honours_dismiss_event():
    _out, tags = _toast_markup({"id": "1", "message": "m"}, ev="close_it")
    buttons = [a for t, a in tags if t == "button"]
    assert [b.get("dj-click") for b in buttons] == ["close_it"]
    default = rust_handlers.ToastContainerHandler().render(["toasts=ts"], {"ts": [{"id": "1"}]})
    assert 'dj-click="dismiss_toast"' in default, "absent: the default event"


@pytest.mark.parametrize(
    "odd",
    ['x" onclick="y', "<script>alert(1)</script>", "a&b", "x'><img src=x onerror=y>"],
    ids=["quote", "script", "ampersand", "tag"],
)
def test_toast_container_escapes_dismiss_event_in_the_attribute(odd):
    out, tags = _toast_markup({"id": "1", "message": "m"}, ev=odd)
    assert [t for t, _ in tags] == ["div", "div", "span", "button"], out
    attrs = tags[-1][1]
    assert set(attrs) == {"class", "dj-click", "data-value"}, attrs
    assert attrs["dj-click"] == odd, "the value arrives intact, as text"
    assert not any(name.startswith("on") for t, a in tags for name in a)


@pytest.mark.parametrize("odd", ['info" onclick="x', "x y", "<b>", "", "ERROR"])
def test_toast_container_types_outside_the_whitelist_render_as_info(odd):
    out, tags = _toast_markup({"id": "1", "type": odd, "message": "m"})
    classes = [a["class"] for t, a in tags if t == "div"]
    assert classes == ["toast-container", "toast toast-info"], out
    assert not any(name.startswith("on") for t, a in tags for name in a)


@pytest.mark.parametrize("good", ["info", "success", "warning", "error"])
def test_toast_container_keeps_the_whitelisted_types(good):
    _out, tags = _toast_markup({"id": "1", "type": good, "message": "m"})
    assert [a["class"] for t, a in tags if t == "div"][1] == f"toast toast-{good}"
