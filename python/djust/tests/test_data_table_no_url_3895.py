"""#3895 item 1: a ``data_table`` row with no URL under the key is not a link.

``table_cell`` writes a missing key as ``""`` and ``None`` as ``"None"``, so a link column drew
``<a href="">`` (the current page) / ``<a href="None">`` (``<page>/None``), and a ``row_url`` row
was clickable towards them. Both engines now draw the cell as plain text and the row as a plain
row when the value is missing, ``None`` or blank. Every other value (``0``, ``False``, ``"#"``,
a dangerous scheme) is still a link through the one URL rule.
"""

from __future__ import annotations

import pytest

from djust.tests.test_component_dj_attr_parity_3669 import _django, _rust
from djust.tests.test_data_table_row_url_link_3887 import LINKED, assert_same, view

NO_URL = {"missing": ..., "none": None, "empty": "", "spaces": "   "}
STILL_A_URL = {"zero": 0, "false": False, "hash": "#", "js": "javascript:alert(1)"}


def _row(value: object) -> dict:
    row: dict = {"id": 1, "name": "N", "age": 3}
    if value is not ...:
        row["u"] = value
    return row


@pytest.mark.parametrize("engine", [_django, _rust], ids=["python", "rust"])
@pytest.mark.parametrize("name", sorted(NO_URL))
def test_no_url_is_plain_text_and_a_plain_row(engine, name: str) -> None:
    ctx = {"rows": [_row(NO_URL[name])], "cols": LINKED}
    html = engine('{% data_table rows=rows columns=cols row_url="u" %}', ctx)
    page = view(html)
    assert len(page.rows) == 1, html
    row = page.rows[0]
    assert row["anchors"] == [], html
    assert "data-href" not in row["attrs"], html
    assert "data-table-row-clickable" not in html, html
    assert ">N<" in html, html  # the cell's text is still drawn


@pytest.mark.parametrize("name", sorted(NO_URL))
def test_no_url_agrees_between_the_engines(name: str) -> None:
    assert_same(
        '{% data_table rows=rows columns=cols row_url="u" %}',
        {"rows": [_row(NO_URL[name])], "cols": LINKED},
    )


@pytest.mark.parametrize("engine", [_django, _rust], ids=["python", "rust"])
@pytest.mark.parametrize("name", sorted(STILL_A_URL))
def test_any_other_value_is_still_a_link(engine, name: str) -> None:
    ctx = {"rows": [_row(STILL_A_URL[name])], "cols": LINKED}
    html = engine('{% data_table rows=rows columns=cols row_url="u" %}', ctx)
    row = view(html).rows[0]
    assert len(row["anchors"]) == 1, html
    assert "data-href" in row["attrs"], html


def test_a_mixed_table_links_only_the_rows_with_a_url() -> None:
    rows = [_row("/1"), _row(None), _row(...), _row("/4")]
    _py, rs = assert_same(
        '{% data_table rows=rows columns=cols row_url="u" %}', {"rows": rows, "cols": LINKED}
    )
    assert [r["attrs"].get("data-href") for r in view(rs).rows] == ["/1", None, None, "/4"]
    assert [len(r["anchors"]) for r in view(rs).rows] == [1, 0, 0, 1]
