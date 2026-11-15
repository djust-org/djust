"""A value marked safe for text is escaped when a native handler writes it into an attribute.

A tuple operand reaches a native handler with its members as they are (#3768), so a member the
developer marked safe (``mark_safe``, meant for text) arrived as a ``SafeString``; the handlers
wrote some members into attribute values with ``conditional_escape``, which keeps a
``SafeString`` raw, so the markup could leave the attribute. Each site now uses ``escape``.

Each case renders through the native engine with the member in a list AND in a tuple (the two
shapes a template operand has), and a dict-valued operand through a direct handler call (the only
way a ``SafeString`` dict value reaches a handler). The page is parsed: no element may carry an
``on...`` attribute, and the payload's text must still be in the page (escaped), so a case that
dropped the value would not pass.
"""

from __future__ import annotations

import pytest
from django.utils.safestring import mark_safe

from djust.tests.test_components_csp_aria import _rust
from djust.tests.test_data_table_row_url_link_3887 import view

BREAKOUT = 'x" onmouseover="alert(1)" y="'
ESCAPED = "x&quot; onmouseover=&quot;alert(1)&quot; y=&quot;"
ROWS = [{"id": 1, "name": "N"}]
COLS = [{"key": "name", "label": "Name"}]


def _marked() -> str:
    return mark_safe(BREAKOUT)


#: ``(id, template, context builder)``: the builder takes ``wrap`` (``list`` or ``tuple``) and
#: returns the context with the marked-safe member inside that shape.
SITES = [
    (
        "form_array-value",
        "{% form_array name='f' rows=rows %}{% endform_array %}",
        lambda wrap: {"rows": wrap([_marked()])},
    ),
    (
        "form_array-value-dict-row",
        "{% form_array name='f' rows=rows %}{% endform_array %}",
        lambda wrap: {"rows": wrap([{"value": _marked()}])},
    ),
    (
        "data_table-export_formats",
        "{% data_table rows=rows columns=cols exportable=True export_formats=fmts %}",
        lambda wrap: {"rows": ROWS, "cols": COLS, "fmts": wrap([_marked()])},
    ),
    (
        "data_table-import_formats",
        "{% data_table rows=rows columns=cols importable=True import_formats=fmts %}",
        lambda wrap: {"rows": ROWS, "cols": COLS, "fmts": wrap([_marked()])},
    ),
    (
        "data_table-bulk_actions-name",
        "{% data_table rows=rows columns=cols bulk_actions=ba selected_rows=sel %}",
        lambda wrap: {"rows": ROWS, "cols": COLS, "ba": wrap([_marked()]), "sel": [1]},
    ),
    (
        "data_table-bulk_actions-key",
        "{% data_table rows=rows columns=cols bulk_actions=ba selected_rows=sel %}",
        lambda wrap: {
            "rows": ROWS,
            "cols": COLS,
            "ba": wrap([{"key": _marked(), "label": "L"}]),
            "sel": [1],
        },
    ),
    (
        "data_table-computed_columns",
        "{% data_table rows=rows columns=cols computed_columns=cc %}",
        lambda wrap: {"rows": ROWS, "cols": COLS, "cc": wrap([{"key": _marked(), "label": "C"}])},
    ),
    (
        "data_table-row-key",
        "{% data_table rows=rows columns=cols selectable=True %}",
        lambda wrap: {"rows": wrap([{"id": _marked(), "name": "N"}]), "cols": COLS},
    ),
    (
        "data_table-column-key",
        "{% data_table rows=rows columns=cols column_visibility=True %}",
        lambda wrap: {"rows": ROWS, "cols": wrap([{"key": _marked(), "label": "L"}])},
    ),
]


@pytest.mark.parametrize("wrap", [list, tuple], ids=["list", "tuple"])
@pytest.mark.parametrize(("name", "src", "build"), SITES, ids=[s[0] for s in SITES])
def test_a_marked_safe_member_is_escaped_in_its_attribute(name, src, build, wrap) -> None:
    html = _rust(src, build(wrap))
    assert view(html).handlers == [], html
    assert ESCAPED in html, html


def _direct(**kw: object) -> str:
    from djust.components.rust_handlers import DataTableHandler

    args = ["rows=rows", "columns=cols", *(f"{k}={k}" for k in kw)]
    return str(DataTableHandler().render(args, {"rows": ROWS, "cols": COLS, **kw}))


@pytest.mark.parametrize(
    ("name", "kw"),
    [
        ("row_class_map", {"row_class_map": {"name": {"N": None}}}),
        ("footer_aggregations", {"footer_aggregations": {"name": None}}),
        (
            "active_expressions",
            {"column_expressions": {"name": "E"}, "active_expressions": {"name": None}},
        ),
        ("column_expressions", {"column_expressions": {"name": None}}),
    ],
)
def test_a_marked_safe_dict_value_is_escaped_in_its_attribute(name: str, kw: dict) -> None:
    def fill(value: object) -> object:
        if isinstance(value, dict):
            return {k: fill(v) for k, v in value.items()}
        return _marked() if value is None else value

    html = _direct(**{k: fill(v) for k, v in kw.items()})
    assert view(html).handlers == [], html
    assert ESCAPED in html, html


def test_a_marked_safe_filter_value_is_escaped_in_its_attribute() -> None:
    from djust.components.rust_handlers import DataTableHandler

    cols = [{"key": "name", "label": "Name", "filterable": True}]
    html = str(
        DataTableHandler().render(
            ["rows=rows", "columns=cols", "filters=filters"],
            {"rows": ROWS, "cols": cols, "filters": {"name": _marked()}},
        )
    )
    assert view(html).handlers == [], html
    assert ESCAPED in html, html
