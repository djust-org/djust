"""Server-side contract test for #1171 — URL allowlist on data_table
``row_url``.

The actual defense against open-redirect via ``data-href`` lives in
``python/djust/components/static/djust_components/data-table-row-click.js``
(regex ``/^(https?:\\/\\/|\\/(?!\\/)|\\/.)/``), validated at click-time
on the client. PR #1170 added 3 JS regression tests for it.

This Python-side test documents the **server-side contract**:

  * The template rendering pipeline does NOT crash when a row dict
    contains a hostile URL value (``//evil.com/path``,
    ``javascript:alert(1)``, etc.) under ``row_url``.
  * The wiring (``data-href=`` attribute, ``data-table-row-clickable``
    marker class, ``role="button"``, ``tabindex="0"``) is unconditional
    once ``row_url`` is set — the server intentionally does NOT filter
    URLs at render time, because legitimate same-origin URLs may take
    many shapes (``/path``, ``./relative``, ``https://...``) and the
    server lacks the URL parser context to reject hostile values
    safely; doing it client-side at click-time both centralises the
    check and lets the developer pass any URL shape they trust.

The companion JS tests in
``tests/js/data_table_row_click.test.js`` (under "URL allowlist") verify
the actual rejection. This file locks in the server-side
"render-doesn't-crash, wiring-is-stable" contract that the JS layer
depends on.
"""

from __future__ import annotations

from pathlib import Path

import django
import pytest
from django.conf import settings
from django.template import Context, Engine

if not settings.configured:
    import os

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "demo_project.settings")
    django.setup()


_TABLE_HTML_PATH = (
    Path(__file__).resolve().parents[2]
    / "python"
    / "djust"
    / "components"
    / "templates"
    / "djust_components"
    / "table.html"
)
_TABLE_TEMPLATE = Engine().from_string(_TABLE_HTML_PATH.read_text())


def _ctx(row_url_value: str) -> dict:
    """Return a base data_table context with one row whose ``claim_url``
    cell holds ``row_url_value``."""
    return {
        "rows": [{"id": 1, "claim_url": row_url_value, "name": "Row"}],
        "columns": [{"key": "name", "label": "Name"}],
        "sort_by": "",
        "sort_desc": False,
        "sort_event": "table_sort",
        "page": 1,
        "total_pages": 1,
        "selectable": False,
        "selected_rows": [],
        "select_event": "table_select",
        "row_key": "id",
        "search": False,
        "search_query": "",
        "search_event": "table_search",
        "search_debounce": 300,
        "filters": {},
        "filter_event": "table_filter",
        "loading": False,
        "empty_title": "No data",
        "empty_description": "",
        "empty_icon": "",
        "paginate": False,
        "page_event": "table_page",
        "prev_event": "table_prev",
        "next_event": "table_next",
        "striped": False,
        "compact": False,
        "row_click_event": "",
        "row_click_value_key": "id",
        "row_url": "claim_url",
    }


def _render(ctx: dict) -> str:
    return _TABLE_TEMPLATE.render(Context(ctx))


# Mix of allowed and hostile URL shapes. The JS regex
# `/^(https?:\/\/|\/(?!\/)|\.)/` accepts the first three and rejects
# the last three. Server-side, ALL of them must render without raising
# and emit the standard `data-href=` wiring — the rejection is
# client-side.
_URL_CASES = [
    pytest.param("/claims/42", id="absolute-path"),
    pytest.param("./relative", id="relative-path"),
    pytest.param("https://example.com/x", id="https-absolute"),
    pytest.param("//evil.com/path", id="protocol-relative-hostile"),
    pytest.param("javascript:alert(1)", id="javascript-uri-hostile"),
    pytest.param("data:text/html,<script>1</script>", id="data-uri-hostile"),
]


@pytest.mark.parametrize("url_value", _URL_CASES)
def test_template_render_does_not_crash_for_url_shape(url_value: str):
    """Rendering must succeed regardless of URL shape — the JS guard,
    not the template, is responsible for filtering hostile values at
    click-time."""
    out = _render(_ctx(url_value))
    # Render produced a row body.
    assert "<tbody>" in out
    # The data-href wiring is in place (value extracted at runtime by
    # the Rust template engine; the Django Engine used here for unit
    # tests doesn't expand `dictsort:row_url|first` to the row value,
    # but the attribute is unconditionally emitted in the row_url
    # branch — see python/tests/test_data_table_link_row_nav.py for
    # the parallel "wiring is in place" assertion).
    body_start = out.find("<tbody>")
    body_end = out.find("</tbody>")
    body = out[body_start:body_end]
    assert "data-href=" in body
    # The marker class is on the <tr> so the JS module binds the
    # nested-control guard + click-time URL-allowlist check.
    assert "data-table-row-clickable" in body
    # Keyboard activation affordance.
    assert 'role="button"' in body
    assert 'tabindex="0"' in body


def test_no_inline_onclick_for_any_url_shape():
    """Defense-in-depth: even with hostile dict values, the row never
    emits an inline ``onclick=""`` attribute. The JS module is the
    only navigation entry point."""
    for case in _URL_CASES:
        # Each pytest.param wraps a tuple (values, id, marks); first
        # element is the actual URL value.
        url_value = case.values[0]
        out = _render(_ctx(url_value))
        body_start = out.find("<tbody>")
        body_end = out.find("</tbody>")
        body = out[body_start:body_end]
        assert "onclick=" not in body, f"Inline onclick leaked for url shape {url_value!r}"
