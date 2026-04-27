"""Regression tests for #1111 row-level navigation accessibility +
nested-control + CSP additions on top of the prior #1110/#1111
scaffolding.

The earlier ``python/tests/test_data_table_link_row_nav.py`` already
covers the structural template wiring (``dj-click`` / ``data-href`` on
each ``<tr>``, mixin defaults, template-tag dispatcher). This file
covers the v0.9.1 additions called out in the issue brief:

  * ``role="button"`` and ``tabindex="0"`` on row-clickable ``<tr>``
  * ``data-table-row-clickable`` marker class (drives the JS module)
  * No inline ``onclick=""`` (CSP-strict friendly)
  * Selectable composition — checkbox cell does NOT inherit
    ``dj-click`` from the row, and the row-level ``dj-click`` is NOT
    accidentally placed on the checkbox.
  * CSP nonce propagation — the template tag accepts a nonce-aware
    request without raising; the row-click feature is implemented as a
    static JS module (CSP-clean), so no inline-script nonce thread is
    required.
"""

from __future__ import annotations

from pathlib import Path

import django
from django.conf import settings
from django.template import Context, Engine
from django.test import SimpleTestCase

# Tests in tests/unit/ run under demo_project's settings module. Lazy
# Django setup for the bare-template render path.
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


def _base_ctx(rows, columns, **overrides):
    base = {
        "rows": rows,
        "columns": columns,
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
        "row_url": "",
    }
    base.update(overrides)
    return base


def _render(ctx):
    return _TABLE_TEMPLATE.render(Context(ctx))


def _tbody(html: str) -> str:
    start = html.find("<tbody>")
    end = html.find("</tbody>")
    return html[start:end] if start != -1 and end != -1 else ""


# ---------------------------------------------------------------------------
# Accessibility: role="button" + tabindex="0" on clickable rows
# ---------------------------------------------------------------------------


class RowClickAccessibilityTest(SimpleTestCase):
    """When row navigation is enabled, the <tr> must be reachable +
    activatable via keyboard. role="button" announces the affordance to
    assistive tech; tabindex="0" puts the row in the focus order."""

    def test_row_click_event_sets_role_button_and_tabindex(self):
        rows = [{"id": 1, "name": "Alice"}]
        columns = [{"key": "name", "label": "Name"}]
        out = _render(_base_ctx(rows, columns, row_click_event="open_user"))
        body = _tbody(out)
        self.assertIn('role="button"', body)
        self.assertIn('tabindex="0"', body)

    def test_row_url_sets_role_button_and_tabindex(self):
        rows = [{"claim_url": "/claims/1/", "name": "Claim 1"}]
        columns = [{"key": "name", "label": "Name"}]
        out = _render(_base_ctx(rows, columns, row_url="claim_url"))
        body = _tbody(out)
        self.assertIn('role="button"', body)
        self.assertIn('tabindex="0"', body)

    def test_no_row_navigation_no_role_button(self):
        """Backwards-compat: without row_click_event or row_url,
        the rendered <tr> must NOT advertise as role=button (it's just a
        plain row)."""
        rows = [{"id": 1, "name": "Alice"}]
        columns = [{"key": "name", "label": "Name"}]
        out = _render(_base_ctx(rows, columns))
        body = _tbody(out)
        self.assertNotIn("role=", body)
        self.assertNotIn("tabindex=", body)

    def test_role_and_tabindex_render_per_row(self):
        rows = [{"id": 1}, {"id": 2}, {"id": 3}]
        columns = [{"key": "id", "label": "ID"}]
        out = _render(_base_ctx(rows, columns, row_click_event="open"))
        # 3 rows → 3 each
        self.assertEqual(out.count('role="button"'), 3)
        self.assertEqual(out.count('tabindex="0"'), 3)


# ---------------------------------------------------------------------------
# Marker class: data-table-row-clickable (drives the JS module)
# ---------------------------------------------------------------------------


class RowClickableMarkerClassTest(SimpleTestCase):
    """The data-table-row-clickable marker class is what the JS
    component module hooks onto for keyboard activation + nested-control
    guard. Both row_click_event and row_url paths must emit it."""

    def test_row_click_event_sets_marker_class(self):
        rows = [{"id": 1, "name": "Alice"}]
        columns = [{"key": "name", "label": "Name"}]
        out = _render(_base_ctx(rows, columns, row_click_event="open"))
        body = _tbody(out)
        self.assertIn('class="data-table-row-clickable"', body)

    def test_row_url_sets_marker_class(self):
        rows = [{"claim_url": "/c/1/", "name": "C1"}]
        columns = [{"key": "name", "label": "Name"}]
        out = _render(_base_ctx(rows, columns, row_url="claim_url"))
        body = _tbody(out)
        self.assertIn('class="data-table-row-clickable"', body)

    def test_no_marker_class_when_no_row_navigation(self):
        rows = [{"id": 1, "name": "Alice"}]
        columns = [{"key": "name", "label": "Name"}]
        out = _render(_base_ctx(rows, columns))
        body = _tbody(out)
        self.assertNotIn("data-table-row-clickable", body)


# ---------------------------------------------------------------------------
# Cursor pointer affordance (already shipped, regression-locked here)
# ---------------------------------------------------------------------------


class RowClickAffordanceTest(SimpleTestCase):
    def test_cursor_pointer_present_for_event_path(self):
        rows = [{"id": 1, "name": "A"}]
        columns = [{"key": "name", "label": "Name"}]
        out = _render(_base_ctx(rows, columns, row_click_event="open"))
        self.assertIn("cursor:pointer", out)

    def test_cursor_pointer_present_for_url_path(self):
        rows = [{"claim_url": "/c/1/", "name": "C"}]
        columns = [{"key": "name", "label": "Name"}]
        out = _render(_base_ctx(rows, columns, row_url="claim_url"))
        self.assertIn("cursor:pointer", out)


# ---------------------------------------------------------------------------
# CSP-strict friendliness: no inline onclick on the row
# ---------------------------------------------------------------------------


class CSPInlineHandlerTest(SimpleTestCase):
    """The pre-#1111 row_url path used inline ``onclick=""`` which
    requires ``script-src 'unsafe-inline'``. The v0.9.1 implementation
    moves the navigation logic into the static JS component module
    (``data-table-row-click.js``), so neither path emits inline JS.
    Locks in the CSP-strict-compatible architecture against accidental
    regression."""

    def test_row_url_path_emits_no_inline_onclick(self):
        rows = [{"claim_url": "/claims/1/", "name": "Claim"}]
        columns = [{"key": "name", "label": "Name"}]
        out = _render(_base_ctx(rows, columns, row_url="claim_url"))
        body = _tbody(out)
        self.assertNotIn("onclick=", body)
        # Defensive: data-href is still wired up for the JS module to
        # read.
        self.assertIn("data-href=", body)

    def test_row_click_event_path_emits_no_inline_onclick(self):
        rows = [{"id": 1, "name": "Alice"}]
        columns = [{"key": "name", "label": "Name"}]
        out = _render(_base_ctx(rows, columns, row_click_event="open"))
        body = _tbody(out)
        self.assertNotIn("onclick=", body)


# ---------------------------------------------------------------------------
# Selectable + row navigation composition
# ---------------------------------------------------------------------------


class SelectableCompositionTest(SimpleTestCase):
    """selectable=True adds a <td><input type="checkbox" .../></td>
    cell. With row_click_event also set, the row-level dj-click must
    NOT be accidentally placed on the checkbox (the existing select_event
    is what fires for checkbox clicks). The JS module's nested-control
    guard handles the runtime side; this test locks in the structural
    side — the dj-click event count matches the row count, not the
    row-count + checkbox-count."""

    def test_selectable_does_not_double_dj_click_on_checkboxes(self):
        rows = [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]
        columns = [{"key": "name", "label": "Name"}]
        out = _render(
            _base_ctx(
                rows,
                columns,
                selectable=True,
                row_click_event="open_user",
            )
        )
        # Only the 2 <tr>s should have dj-click="open_user"; the
        # 2 checkboxes have dj-click="{{ select_event }}" (default
        # "table_select"), not "open_user".
        self.assertEqual(out.count('dj-click="open_user"'), 2)
        # And the select_event still wires up the checkboxes.
        self.assertEqual(out.count('dj-click="table_select"'), 3)  # 2 row + 1 select-all

    def test_selectable_checkbox_is_input_for_nested_guard(self):
        """The JS-side nested-control guard works because the checkbox
        is an <input>, which is in the NESTED_CONTROL_SELECTOR list.
        Lock in that the markup is in fact an <input>."""
        rows = [{"id": 1, "name": "A"}]
        columns = [{"key": "name", "label": "Name"}]
        out = _render(
            _base_ctx(
                rows,
                columns,
                selectable=True,
                row_click_event="open_user",
            )
        )
        body = _tbody(out)
        self.assertIn('<input type="checkbox"', body)


# ---------------------------------------------------------------------------
# CSP nonce propagation (smoke)
# ---------------------------------------------------------------------------


class CSPNonceTest(SimpleTestCase):
    """The data_table template-tag function and underlying template
    don't crash when rendered against a Context whose request carries a
    csp_nonce attribute (django-csp's middleware contract). The static
    JS module architecture means we never need to inject an inline
    <script> with the nonce, but the test verifies the render path is
    nonce-tolerant against future template additions that may need it."""

    def test_template_renders_with_csp_nonce_in_context(self):
        rows = [{"id": 1, "name": "Alice"}]
        columns = [{"key": "name", "label": "Name"}]
        ctx = _base_ctx(rows, columns, row_url="claim_url")
        # Synthetic Django request with csp_nonce — the template-tag
        # context has no access to the request directly (inclusion-tag
        # context is the dict returned by data_table()), but adding a
        # csp_nonce key must not break rendering.
        ctx["csp_nonce"] = "test-nonce-value-1234"
        out = _render(ctx)
        # Render succeeded and produced row markup.
        self.assertIn("<tbody>", out)
        # Sanity: no inline-script tag was emitted (CSP-clean
        # architecture).
        self.assertNotIn("<script", _tbody(out))
