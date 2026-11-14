"""#3895 item 2: ``data_table`` columns given as bare names render on both engines.

``columns=["name", "age"]`` is drawn by the native handler (the name is the key and the label),
but ``table.html`` reads ``col.key``, and a filter argument that does not resolve raises
``VariableDoesNotExist``: the page's first render, the one under Django's engine, failed. The
Python tag now reads any column that is not a dict as the column whose key and label are its text
(an ``int`` keeps its value as the key), and that text is plain even when it is marked safe.

The comparison is the body rows and the header labels. The header's other attributes and the
native ``data-row-key`` differ for every column shape, dicts included (#3663, #3869), so they are
not this item; ``test_a_dict_column_and_its_bare_name_draw_the_same_python_markup`` pins that a
bare name is exactly its dict on the Python side.
"""

from __future__ import annotations

import re

import pytest
from django.utils.safestring import mark_safe

from djust.tests.test_component_dj_attr_parity_3669 import _django, _rust
from djust.tests.test_data_table_row_url_link_3887 import assert_same, view
from scripts.lib.template_library_parity.classify import norm_ws

ROWS = [
    {"id": 1, "name": "Ann", "age": 30, "u": "/a"},
    {"id": 2, "name": None, "age": 0, "u": None},
    {"id": 3, "age": "x<y&z"},
]


def _body(html: str) -> str:
    """The ``<tbody>`` with whitespace between tags removed, and the native-only row key dropped."""
    match = re.search(r"<tbody[^>]*>(.*?)</tbody>", html, re.S)
    assert match, html
    return re.sub(r' data-row-key="[^"]*"', "", norm_ws(match.group(1)))


def _labels(html: str) -> list[str]:
    return [norm_ws(t) for t in re.findall(r"<th\b[^>]*>(.*?)</th>", html, re.S)]


@pytest.mark.parametrize(
    "cols",
    [["name", "age"], ["name", {"key": "age", "label": "Age"}]],
    ids=["bare", "mixed"],
)
@pytest.mark.parametrize("row_url", ["", 'row_url="u"'])
def test_bare_name_columns_render_and_agree(cols: list, row_url: str) -> None:
    src = "{% data_table rows=rows columns=cols " + row_url + " %}"
    py, rs = assert_same(src, {"rows": ROWS, "cols": cols})
    assert _body(py) == _body(rs), (py, rs)
    assert _labels(py) == _labels(rs) == ["name", "Age" if isinstance(cols[1], dict) else "age"]
    assert ">Ann<" in _body(py) and "x&lt;y&amp;z" in _body(py)


@pytest.mark.parametrize("engine", [_django, _rust], ids=["python", "rust"])
def test_a_numeric_bare_column_reads_its_key(engine) -> None:
    html = engine("{% data_table rows=rows columns=cols %}", {"rows": [{5: "five"}], "cols": [5]})
    assert ">five<" in _body(html), html
    assert _labels(html) == ["5"], html


NON_DICT = {"float": (1.5, "1.5"), "bool": (True, "True"), "none": (None, "None")}


@pytest.mark.parametrize("engine", [_django, _rust], ids=["python", "rust"])
@pytest.mark.parametrize("name", sorted(NON_DICT))
def test_any_non_dict_column_is_the_column_of_its_text(engine, name: str) -> None:
    """Not only names and ints: every value that is not a dict is its text, as natively."""
    col, text = NON_DICT[name]
    html = engine(
        "{% data_table rows=rows columns=cols %}", {"rows": [{"id": 1, text: "v"}], "cols": [col]}
    )
    assert _labels(html) == [text], html
    assert ">v<" in _body(html), html


@pytest.mark.parametrize("name", sorted(NON_DICT))
def test_any_non_dict_column_agrees_between_the_engines(name: str) -> None:
    col, text = NON_DICT[name]
    ctx = {"rows": [{"id": 1, text: "v"}], "cols": [col]}
    py, rs = assert_same("{% data_table rows=rows columns=cols %}", ctx)
    assert _body(py) == _body(rs) and _labels(py) == _labels(rs)


@pytest.mark.parametrize("engine", [_django, _rust], ids=["python", "rust"])
def test_a_marked_safe_bare_name_is_text_on_both_engines(engine) -> None:
    """A bare name is a key, not markup: a header with markup is the dict form's ``label``."""
    name = mark_safe("<b>H</b>")
    html = engine(
        "{% data_table rows=rows columns=cols %}",
        {"rows": [{"id": 1, "<b>H</b>": "v"}], "cols": [name]},
    )
    assert _labels(html) == ["&lt;b&gt;H&lt;/b&gt;"], html
    assert "<b>H</b>" not in html, html
    assert ">v<" in _body(html), html


def test_a_dict_column_and_its_bare_name_draw_the_same_python_markup() -> None:
    src = '{% data_table rows=rows columns=cols row_url="u" %}'
    bare = _django(src, {"rows": ROWS, "cols": ["name", "age"]})
    spelled = _django(
        src,
        {"rows": ROWS, "cols": [{"key": "name", "label": "name"}, {"key": "age", "label": "age"}]},
    )
    assert bare == spelled


def test_a_bare_column_row_with_no_url_is_a_plain_row() -> None:
    """The neighbour of #3895 item 1: a ``row_url`` row with ``None`` there is not clickable."""
    py, rs = assert_same(
        '{% data_table rows=rows columns=cols row_url="u" %}', {"rows": ROWS, "cols": ["name"]}
    )
    assert [r["attrs"].get("data-href") for r in view(py).rows] == ["/a", None, None]
