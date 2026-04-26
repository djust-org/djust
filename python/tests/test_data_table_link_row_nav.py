"""Regression tests for #1110 (link column type) and #1111 (row-level
navigation) in the {% data_table %} template tag.

Tests verify the STRUCTURAL output of `table.html` — presence of `<a>`
wrappers, `dj-click` / `data-href` attributes — rather than extracted
cell content. The cell-content render uses `row|dictsort:col.key|first`,
which depends on the Rust template engine's filter dispatch (Django's
stock engine doesn't extract dict values via that filter chain). The
structural conditional logic added by #1110/#1111 is what these tests
prove; cell-content rendering has separate test coverage.
"""

from pathlib import Path

from django.template import Context, Engine
from django.test import TestCase

# Read the template directly — djust.components is intentionally not in
# demo_project's INSTALLED_APPS, so Django's app-based template loader
# can't find this file by name.
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


def render_table(ctx):
    return _TABLE_TEMPLATE.render(Context(ctx))


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


class LinkColumnStructureTest(TestCase):
    """#1110: column with `link` key wraps the cell in an <a>."""

    def test_link_column_emits_anchor_tag(self):
        """When col.link is set, the cell contains an <a> element."""
        rows = [{"claim_number": "C-001", "claim_url": "/claims/1/"}]
        columns = [{"key": "claim_number", "label": "Claim", "link": "claim_url"}]
        out = render_table(_base_ctx(rows, columns))
        # The <a> tag is rendered (the href value is rust-template-rendered
        # at runtime; we just assert the wrapper structure here).
        self.assertIn("<a href=", out)

    def test_link_class_attribute_rendered(self):
        """col.link_class becomes class="..." on the <a>."""
        rows = [{"name": "Alice", "profile_url": "/u/alice/"}]
        columns = [
            {
                "key": "name",
                "label": "Name",
                "link": "profile_url",
                "link_class": "user-link",
            },
        ]
        out = render_table(_base_ctx(rows, columns))
        self.assertIn('class="user-link"', out)

    def test_no_link_no_anchor_pre_1110_compat(self):
        """col without `link` key renders the cell as plain <td> (no <a>)."""
        rows = [{"name": "Alice"}]
        columns = [{"key": "name", "label": "Name"}]
        out = render_table(_base_ctx(rows, columns))
        # Cell is <td>...</td> without an <a> wrap.
        # Header cells use `dj-click` for sort but those aren't <a>.
        # Find the row body specifically.
        body_start = out.find("<tbody>")
        body_end = out.find("</tbody>")
        body = out[body_start:body_end]
        self.assertNotIn("<a href=", body)


class RowClickEventStructureTest(TestCase):
    """#1111 Option B: row_click_event makes <tr> clickable with dj-click."""

    def test_row_click_event_attaches_dj_click_to_tr(self):
        rows = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
        columns = [{"key": "name", "label": "Name"}]
        out = render_table(_base_ctx(rows, columns, row_click_event="open_user"))
        self.assertIn('dj-click="open_user"', out)
        # cursor:pointer style is part of the affordance
        self.assertIn("cursor:pointer", out)

    def test_row_click_event_appears_on_every_tr(self):
        """One per row — count occurrences match row count."""
        rows = [{"id": 1}, {"id": 2}, {"id": 3}]
        columns = [{"key": "id", "label": "ID"}]
        out = render_table(_base_ctx(rows, columns, row_click_event="open"))
        self.assertEqual(out.count('dj-click="open"'), 3)

    def test_no_row_click_event_no_tr_dj_click(self):
        """Backwards-compat: without row_click_event, no dj-click on <tr>."""
        rows = [{"id": 1, "name": "Alice"}]
        columns = [{"key": "name", "label": "Name"}]
        out = render_table(_base_ctx(rows, columns))
        body_start = out.find("<tbody>")
        body_end = out.find("</tbody>")
        body = out[body_start:body_end]
        # Body should have <tr> tags but none with dj-click
        self.assertIn("<tr", body)
        for line in body.split("\n"):
            if "<tr" in line and not line.strip().startswith("{#"):
                self.assertNotIn(
                    "dj-click",
                    line,
                    f"<tr> got unexpected dj-click: {line.strip()}",
                )


class RowUrlStructureTest(TestCase):
    """#1111 Option A: row_url adds data-href + inline JS to <tr>."""

    def test_row_url_attaches_data_href_and_onclick(self):
        rows = [{"claim_url": "/claims/1/", "name": "Claim 1"}]
        columns = [{"key": "name", "label": "Name"}]
        out = render_table(_base_ctx(rows, columns, row_url="claim_url"))
        # data-href attribute is present (value extracted by Rust engine
        # at runtime; we just assert the wiring is in place).
        self.assertIn("data-href=", out)
        self.assertIn("window.location=this.dataset.href", out)
        self.assertIn("cursor:pointer", out)

    def test_row_click_event_takes_precedence_over_row_url(self):
        """Both set → dj-click wins, data-href is NOT rendered."""
        rows = [{"id": 1, "claim_url": "/claims/1/", "name": "Claim 1"}]
        columns = [{"key": "name", "label": "Name"}]
        out = render_table(
            _base_ctx(rows, columns, row_click_event="open_claim", row_url="claim_url")
        )
        self.assertIn('dj-click="open_claim"', out)
        # No data-href because the {% if row_click_event %} branch wins
        body_start = out.find("<tbody>")
        body_end = out.find("</tbody>")
        body = out[body_start:body_end]
        self.assertNotIn("data-href=", body)


class MixinDefaultsTest(TestCase):
    """Verify class-level defaults flow through to context."""

    def test_class_attributes_present(self):
        from djust.components.mixins.data_table import DataTableMixin

        self.assertEqual(DataTableMixin.table_row_click_event, "")
        self.assertEqual(DataTableMixin.table_row_click_value_key, "id")
        self.assertEqual(DataTableMixin.table_row_url, "")

    def test_post_mount_context_includes_new_keys(self):
        from djust.components.mixins.data_table import DataTableMixin

        view = DataTableMixin()
        view.init_table_state()
        view.table_rows = [{"id": 1, "name": "Alice"}]
        ctx = view.get_table_context()

        self.assertEqual(ctx["row_click_event"], "")
        self.assertEqual(ctx["row_click_value_key"], "id")
        self.assertEqual(ctx["row_url"], "")

    def test_pre_mount_default_includes_new_keys(self):
        from djust.components.mixins.data_table import _PRE_MOUNT_TABLE_CONTEXT

        self.assertIn("row_click_event", _PRE_MOUNT_TABLE_CONTEXT)
        self.assertIn("row_click_value_key", _PRE_MOUNT_TABLE_CONTEXT)
        self.assertIn("row_url", _PRE_MOUNT_TABLE_CONTEXT)

    def test_class_attributes_settable_per_view(self):
        """A view can override the defaults."""
        from djust.components.mixins.data_table import DataTableMixin

        class _ClickableTable(DataTableMixin):
            table_row_click_event = "open_claim"
            table_row_click_value_key = "uuid"

        view = _ClickableTable()
        view.init_table_state()
        view.table_rows = []
        ctx = view.get_table_context()

        self.assertEqual(ctx["row_click_event"], "open_claim")
        self.assertEqual(ctx["row_click_value_key"], "uuid")


class TemplateTagDispatcherTest(TestCase):
    """The {% data_table %} template-tag function passes the new params
    through to the inclusion-tag context dict."""

    def test_data_table_function_accepts_new_kwargs(self):
        """Smoke: calling the underlying function shouldn't TypeError."""
        from djust.components.templatetags.djust_components import data_table

        ctx = data_table(
            rows=[{"id": 1, "name": "A"}],
            columns=[{"key": "name"}],
            row_click_event="open",
            row_click_value_key="id",
            row_url="",
        )
        self.assertEqual(ctx["row_click_event"], "open")
        self.assertEqual(ctx["row_click_value_key"], "id")
        self.assertEqual(ctx["row_url"], "")

    def test_data_table_defaults_when_omitted(self):
        from djust.components.templatetags.djust_components import data_table

        ctx = data_table(
            rows=[],
            columns=[],
        )
        self.assertEqual(ctx["row_click_event"], "")
        self.assertEqual(ctx["row_click_value_key"], "id")
        self.assertEqual(ctx["row_url"], "")
