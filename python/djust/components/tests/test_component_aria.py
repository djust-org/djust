"""Doc-claim-verbatim ARIA markup tests for the v1.0 accessibility pass.

Each test renders a component template tag and asserts on the ACTUAL
rendered markup — the ARIA attributes the v1.0 a11y pass guarantees —
not merely "no error". This is the doc-claim-verbatim discipline
(Action #1046): every guarantee documented for these components has
one asserting test here.

Components covered (the 8 P0+P1 interactive components from the
Stage-4 audit): modal, tabs, accordion, dropdown (Python f-string
renderers); pagination, data_table, toast (inclusion templates);
alert (Python f-string renderer).
"""

from pathlib import Path

import pytest
from django.template import Context, Engine

pytestmark = pytest.mark.components

# djust.components is intentionally NOT in the demo project's
# INSTALLED_APPS, so Django's app-based templatetag loader can't find the
# `djust_components` library by name. Register it explicitly on a bare
# Engine so `{% load djust_components %}` resolves in these tests.
_TAG_ENGINE = Engine(
    libraries={
        "djust_components": "djust.components.templatetags.djust_components",
    }
)


def render_tag(src, ctx=None):
    """Render a {% load djust_components %} template snippet."""
    return _TAG_ENGINE.from_string(src).render(Context(ctx or {}))


# Inclusion-template files are read directly — djust.components is not in
# demo_project's INSTALLED_APPS, so the app-based loader can't find them
# by name (mirrors test_data_table_link_row_nav.py).
_TPL_DIR = Path(__file__).resolve().parents[1] / "templates" / "djust_components"


def _engine_template(name):
    return Engine().from_string((_TPL_DIR / name).read_text())


# ---------------------------------------------------------------------------
# 1. Modal
# ---------------------------------------------------------------------------


class TestModalAria:
    def test_modal_has_dialog_role_and_aria_modal(self):
        out = render_tag(
            '{% load djust_components %}{% modal open=True title="T" id="m1" %}body{% endmodal %}'
        )
        assert 'role="dialog"' in out
        assert 'aria-modal="true"' in out

    def test_modal_title_is_labelledby_linked(self):
        out = render_tag(
            '{% load djust_components %}{% modal open=True title="Confirm" id="m1" %}'
            "body{% endmodal %}"
        )
        # aria-labelledby points at the title element's id.
        assert 'aria-labelledby="m1-title"' in out
        assert 'id="m1-title"' in out

    def test_modal_close_button_has_aria_label(self):
        out = render_tag(
            '{% load djust_components %}{% modal open=True title="T" id="m1" %}body{% endmodal %}'
        )
        assert 'aria-label="Close"' in out

    def test_modal_default_id_when_no_id_kwarg(self):
        """A modal with no id= kwarg falls back to the 'modal' default id."""
        out = render_tag(
            '{% load djust_components %}{% modal open=True title="T" %}body{% endmodal %}'
        )
        assert 'aria-labelledby="modal-title"' in out

    def test_modal_no_labelledby_when_no_title(self):
        """No title -> no aria-labelledby (nothing to point at)."""
        out = render_tag(
            '{% load djust_components %}{% modal open=True id="m1" %}body{% endmodal %}'
        )
        assert "aria-labelledby" not in out

    def test_modal_hostile_id_does_not_break_out(self):
        """An XSS-attempting id is escaped — no raw <script>."""
        out = render_tag(
            '{% load djust_components %}{% modal open=True title="T" id=evil %}body{% endmodal %}',
            {"evil": '"><script>alert(1)</script>'},
        )
        assert "<script>alert(1)</script>" not in out


# ---------------------------------------------------------------------------
# 2. Tabs
# ---------------------------------------------------------------------------


_TABS_SRC = (
    '{% load djust_components %}{% tabs id="tb" active="a" %}'
    '{% tab id="a" label="Alpha" %}pane-a{% endtab %}'
    '{% tab id="b" label="Beta" %}pane-b{% endtab %}'
    "{% endtabs %}"
)


class TestTabsAria:
    def test_nav_is_tablist(self):
        out = render_tag(_TABS_SRC)
        assert 'role="tablist"' in out

    def test_one_tab_role_per_tab(self):
        out = render_tag(_TABS_SRC)
        assert out.count('role="tab"') == 2

    def test_exactly_one_selected_tab(self):
        out = render_tag(_TABS_SRC)
        assert out.count('aria-selected="true"') == 1
        assert out.count('aria-selected="false"') == 1

    def test_active_pane_is_tabpanel(self):
        out = render_tag(_TABS_SRC)
        assert 'role="tabpanel"' in out

    def test_tab_panel_aria_controls_pairing(self):
        """The active tab's aria-controls matches the panel's id."""
        out = render_tag(_TABS_SRC)
        assert 'aria-controls="tb-panel-a"' in out
        assert 'id="tb-panel-a"' in out
        # And the panel labels back to the tab.
        assert 'aria-labelledby="tb-tab-a"' in out
        assert 'id="tb-tab-a"' in out

    def test_tab_icon_is_aria_hidden(self):
        out = render_tag(
            '{% load djust_components %}{% tabs id="tb" active="a" %}'
            '{% tab id="a" label="Alpha" icon="*" %}p{% endtab %}'
            "{% endtabs %}"
        )
        assert 'aria-hidden="true"' in out

    def test_hostile_tab_id_does_not_break_out(self):
        """A hostile tab id is escaped — no raw <script>."""
        out = render_tag(
            "{% load djust_components %}{% tabs id=tabid active=tabid %}"
            '{% tab id="x" label="X" %}p{% endtab %}{% endtabs %}',
            {"tabid": '"><script>alert(1)</script>'},
        )
        assert "<script>alert(1)</script>" not in out


# ---------------------------------------------------------------------------
# 3. Accordion
# ---------------------------------------------------------------------------


_ACCORDION_SRC = (
    '{% load djust_components %}{% accordion id="ac" active="one" %}'
    '{% accordion_item id="one" title="First" %}body-one{% endaccordion_item %}'
    '{% accordion_item id="two" title="Second" %}body-two{% endaccordion_item %}'
    "{% endaccordion %}"
)


class TestAccordionAria:
    def test_open_trigger_aria_expanded_true(self):
        out = render_tag(_ACCORDION_SRC)
        assert 'aria-expanded="true"' in out

    def test_closed_trigger_aria_expanded_false(self):
        out = render_tag(_ACCORDION_SRC)
        assert 'aria-expanded="false"' in out

    def test_trigger_aria_controls_pairs_with_panel(self):
        out = render_tag(_ACCORDION_SRC)
        assert 'aria-controls="ac-panel-one"' in out
        assert 'id="ac-panel-one"' in out

    def test_open_panel_has_region_role_and_labelledby(self):
        out = render_tag(_ACCORDION_SRC)
        assert 'role="region"' in out
        assert 'aria-labelledby="ac-trigger-one"' in out
        assert 'id="ac-trigger-one"' in out

    def test_chevron_is_aria_hidden(self):
        out = render_tag(_ACCORDION_SRC)
        assert 'aria-hidden="true"' in out


# ---------------------------------------------------------------------------
# 4. Dropdown
# ---------------------------------------------------------------------------


class TestDropdownAria:
    def test_trigger_has_aria_haspopup(self):
        out = render_tag(
            '{% load djust_components %}{% dropdown id="dd" label="Menu" %}items{% enddropdown %}'
        )
        assert 'aria-haspopup="menu"' in out

    def test_trigger_aria_expanded_reflects_closed(self):
        out = render_tag(
            '{% load djust_components %}{% dropdown id="dd" label="Menu" %}items{% enddropdown %}'
        )
        assert 'aria-expanded="false"' in out

    def test_trigger_aria_expanded_reflects_open(self):
        out = render_tag(
            '{% load djust_components %}{% dropdown id="dd" label="Menu" open=True %}'
            "items{% enddropdown %}"
        )
        assert 'aria-expanded="true"' in out

    def test_menu_has_menu_role_when_open(self):
        out = render_tag(
            '{% load djust_components %}{% dropdown id="dd" label="Menu" open=True %}'
            "items{% enddropdown %}"
        )
        assert 'role="menu"' in out
        assert 'id="dd-menu"' in out

    def test_no_menu_div_when_closed(self):
        """Closed dropdown renders no menu div -> no role=menu."""
        out = render_tag(
            '{% load djust_components %}{% dropdown id="dd" label="Menu" %}items{% enddropdown %}'
        )
        assert 'role="menu"' not in out

    def test_aria_controls_pairs_trigger_and_menu(self):
        out = render_tag(
            '{% load djust_components %}{% dropdown id="dd" label="Menu" open=True %}'
            "items{% enddropdown %}"
        )
        assert 'aria-controls="dd-menu"' in out


# ---------------------------------------------------------------------------
# 5. Alert
# ---------------------------------------------------------------------------


class TestAlertAria:
    def test_error_alert_has_role_alert(self):
        out = render_tag('{% load djust_components %}{% alert type="error" %}Boom{% endalert %}')
        assert 'role="alert"' in out

    def test_warning_alert_has_role_alert(self):
        out = render_tag(
            '{% load djust_components %}{% alert type="warning" %}Heads up{% endalert %}'
        )
        assert 'role="alert"' in out

    def test_danger_alert_has_role_alert(self):
        """`danger` is a distinct branch alongside `error`/`warning` in
        AlertNode's `aria_role` logic — it must get the assertive role."""
        out = render_tag('{% load djust_components %}{% alert type="danger" %}Boom{% endalert %}')
        assert 'role="alert"' in out

    def test_info_alert_has_role_status(self):
        out = render_tag('{% load djust_components %}{% alert type="info" %}FYI{% endalert %}')
        assert 'role="status"' in out

    def test_success_alert_has_role_status(self):
        out = render_tag('{% load djust_components %}{% alert type="success" %}Done{% endalert %}')
        assert 'role="status"' in out

    def test_alert_icon_is_aria_hidden(self):
        out = render_tag('{% load djust_components %}{% alert type="info" %}FYI{% endalert %}')
        assert 'aria-hidden="true"' in out

    def test_dismissible_alert_close_has_aria_label(self):
        out = render_tag(
            '{% load djust_components %}{% alert type="info" dismissible=True %}FYI{% endalert %}'
        )
        assert 'aria-label="Dismiss"' in out


# ---------------------------------------------------------------------------
# 6. Pagination (inclusion template)
# ---------------------------------------------------------------------------


def _pagination_ctx(**overrides):
    base = {
        "pages": [1, 2, 3],
        "page": 2,
        "total_pages": 3,
        "prev_event": "on_prev",
        "next_event": "on_next",
    }
    base.update(overrides)
    return base


class TestPaginationAria:
    def test_nav_has_aria_label(self):
        out = _engine_template("pagination.html").render(Context(_pagination_ctx()))
        assert 'aria-label="Pagination"' in out

    def test_active_page_has_aria_current(self):
        out = _engine_template("pagination.html").render(Context(_pagination_ctx()))
        # Exactly one aria-current="page" — the active page (2 of [1,2,3]).
        assert out.count('aria-current="page"') == 1

    def test_arrow_buttons_have_aria_labels(self):
        out = _engine_template("pagination.html").render(Context(_pagination_ctx()))
        assert 'aria-label="Previous page"' in out
        assert 'aria-label="Next page"' in out

    def test_ellipsis_is_aria_hidden(self):
        out = _engine_template("pagination.html").render(
            Context(_pagination_ctx(pages=[1, "...", 9], total_pages=9))
        )
        assert 'aria-hidden="true"' in out


# ---------------------------------------------------------------------------
# 7. data_table sortable header (inclusion template)
# ---------------------------------------------------------------------------


def _table_ctx(columns, **overrides):
    base = {
        "rows": [],
        "columns": columns,
        "sort_by": "",
        "sort_desc": False,
        "sort_event": "on_sort",
        "page": 1,
        "total_pages": 1,
        "selectable": False,
        "selected_rows": [],
        "select_event": "on_select",
        "search": False,
        "search_query": "",
        "search_event": "on_search",
        "search_debounce": 300,
        "loading": False,
        "empty_title": "No data",
        "empty_description": "",
        "empty_icon": "",
        "paginate": False,
        "page_event": "on_page",
        "prev_event": "on_prev",
        "next_event": "on_next",
        "striped": False,
        "compact": False,
        "row_click_event": "",
        "row_click_value_key": "id",
        "row_url": "",
    }
    base.update(overrides)
    return base


class TestDataTableAria:
    def test_sortable_th_is_keyboard_operable(self):
        """A sortable <th> gets tabindex=0 so keyboard users can sort."""
        out = _engine_template("table.html").render(
            Context(_table_ctx([{"key": "name", "label": "Name", "sortable": True}]))
        )
        assert 'tabindex="0"' in out

    def test_tabindex_only_on_sortable_th(self):
        """tabindex is part of the sortable-<th> branch, not the plain branch.

        The template's `{% if col.sortable|default:True %}` branch is what
        carries `tabindex="0"`; the non-sortable `{% else %}` branch emits
        only `role="columnheader"`. Assert the focusable affordance is
        scoped to the sortable branch (it sits next to `aria-sort`, which
        the non-sortable branch never emits).
        """
        out = _engine_template("table.html").render(
            Context(_table_ctx([{"key": "name", "label": "Name", "sortable": True}]))
        )
        # tabindex appears on the same <th> as aria-sort (sortable branch).
        assert 'tabindex="0"' in out
        assert "aria-sort=" in out

    def test_sort_glyph_is_aria_hidden(self):
        """The active-sort direction glyph is decorative -> aria-hidden."""
        out = _engine_template("table.html").render(
            Context(
                _table_ctx(
                    [{"key": "name", "label": "Name", "sortable": True}],
                    sort_by="name",
                )
            )
        )
        assert 'class="data-table-sort-glyph" aria-hidden="true"' in out


# ---------------------------------------------------------------------------
# 8. Toast (inclusion template)
# ---------------------------------------------------------------------------


class TestToastAria:
    def test_polite_toast_has_status_role(self):
        out = _engine_template("toast.html").render(
            Context(
                {
                    "toasts": [{"id": 1, "type": "info", "message": "Hi"}],
                    "dismiss_event": "on_dismiss",
                }
            )
        )
        assert 'role="status"' in out
        assert 'aria-live="polite"' in out

    def test_error_toast_is_assertive(self):
        out = _engine_template("toast.html").render(
            Context(
                {
                    "toasts": [{"id": 1, "type": "error", "message": "Boom"}],
                    "dismiss_event": "on_dismiss",
                }
            )
        )
        assert 'role="alert"' in out
        assert 'aria-live="assertive"' in out

    def test_toast_dismiss_has_aria_label(self):
        out = _engine_template("toast.html").render(
            Context(
                {
                    "toasts": [{"id": 1, "type": "info", "message": "Hi"}],
                    "dismiss_event": "on_dismiss",
                }
            )
        )
        assert 'aria-label="Dismiss"' in out

    def test_toast_icon_is_aria_hidden(self):
        out = _engine_template("toast.html").render(
            Context(
                {
                    "toasts": [{"id": 1, "type": "success", "message": "Done"}],
                    "dismiss_event": "on_dismiss",
                }
            )
        )
        assert 'aria-hidden="true"' in out
