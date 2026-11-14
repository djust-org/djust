"""#3895: the ``data_table`` differences left for a decision, pinned so they cannot drift.

Each is a case where the two engines draw a different cell or row for the same value, measured
with the body rows compared as markup. None is unsafe: the native side is the one that escapes or
draws less. They are pinned as they are, on both engines, so a change to either side shows up
here; fixing one means deciding which of the two outputs is the intended one (see #3895).

Pinned elsewhere, with the same status:

* a callable inside a column dict (Django calls it, the native handler gets its text):
  ``test_data_table_row_url_link_3887.test_a_callable_inside_a_column_dict_is_not_reachable_natively``;
* a row that is not a dict (the Python tag draws an empty row, the native handler omits it):
  ``test_data_table_row_url_link_3887.test_a_row_that_is_not_a_dict_is_dropped_natively``.
"""

from __future__ import annotations

import re

from django.contrib.auth.models import User
from django.utils.safestring import mark_safe

from djust.tests.test_component_dj_attr_parity_3669 import _django, _rust
from scripts.lib.template_library_parity.classify import norm_ws

LINKED = [{"key": "name", "label": "Name", "link": "u"}, {"key": "age", "label": "Age"}]


def _body(html: str) -> str:
    match = re.search(r"<tbody[^>]*>(.*?)</tbody>", html, re.S)
    assert match, html
    return re.sub(r' data-row-key="[^"]*"', "", norm_ws(match.group(1)))


def test_nan_is_text_and_a_url_in_python_and_none_natively() -> None:
    """``float("nan")`` reaches the native handler as ``None`` (the context's JSON round trip has
    no NaN), so the cell reads ``None`` and the URL is not one. The Python tag writes ``nan`` and
    links to the relative URL ``nan``."""
    rows = [{"id": 1, "name": "N", "age": float("nan"), "u": float("nan")}]
    src = '{% data_table rows=rows columns=cols row_url="u" %}'
    ctx = {"rows": rows, "cols": LINKED}
    assert _body(_django(src, ctx)) == (
        '<tr class="data-table-row-clickable" data-href="nan" role="button" tabindex="0"'
        ' style="cursor:pointer"><td><a href="nan">N</a></td><td>nan</td></tr>'
    )
    assert _body(_rust(src, ctx)) == "<tr><td>N</td><td>None</td></tr>"


def test_a_marked_safe_cell_is_markup_in_python_and_text_natively() -> None:
    """Django keeps a ``mark_safe`` cell's markup; the native handler is handed text and escapes
    it (the escaping policy of every native handler). A URL is never honoured as safe on either
    side (#3894)."""
    rows = [{"id": 1, "name": mark_safe("<i>N</i>"), "age": 3, "u": "/a"}]
    ctx = {"rows": rows, "cols": LINKED}
    src = "{% data_table rows=rows columns=cols %}"
    assert _body(_django(src, ctx)) == '<tr><td><a href="/a"><i>N</i></a></td><td>3</td></tr>'
    assert (
        _body(_rust(src, ctx))
        == '<tr><td><a href="/a">&lt;i&gt;N&lt;/i&gt;</a></td><td>3</td></tr>'
    )


def test_a_model_instance_row_is_empty_in_python_and_read_natively() -> None:
    """The realistic non-dict row. ``table_cell`` reads only mappings, so the Python tag draws
    empty cells; the native handler is handed the model serialised to a dict and draws its
    fields."""
    rows = [User(id=7, username="ann")]
    ctx = {"rows": rows, "cols": [{"key": "username", "label": "User"}]}
    src = "{% data_table rows=rows columns=cols %}"
    assert _body(_django(src, ctx)) == "<tr><td></td></tr>"
    assert _body(_rust(src, ctx)) == "<tr><td>ann</td></tr>"
