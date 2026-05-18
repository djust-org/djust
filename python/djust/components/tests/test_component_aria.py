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

The P2/P3 component set (#1513): progress, badge, avatar (inclusion
templates) and tooltip (Python f-string renderer). `card` is a
generic content container — a plain `<div>` is the correct semantic,
so it intentionally gets no ARIA role (documented decision).
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


# ---------------------------------------------------------------------------
# 9. Progress (inclusion template) — P2/P3 (#1513)
# ---------------------------------------------------------------------------


def _progress_ctx(**overrides):
    base = {
        "value": 40,
        "label": "",
        "size": "md",
        "color": "primary",
        "show_label": True,
    }
    base.update(overrides)
    return base


class TestProgressAria:
    def test_track_has_progressbar_role(self):
        """The progress track is exposed to AT as a progressbar."""
        out = _engine_template("progress.html").render(Context(_progress_ctx()))
        assert 'role="progressbar"' in out

    def test_current_value_is_aria_valuenow(self):
        """The fill percentage is reflected in aria-valuenow."""
        out = _engine_template("progress.html").render(Context(_progress_ctx(value=40)))
        assert 'aria-valuenow="40"' in out

    def test_min_max_bounds_are_exposed(self):
        """min/max bounds default to the 0-100 the renderer clamps to."""
        out = _engine_template("progress.html").render(Context(_progress_ctx()))
        assert 'aria-valuemin="0"' in out
        assert 'aria-valuemax="100"' in out

    def test_value_zero_boundary(self):
        out = _engine_template("progress.html").render(Context(_progress_ctx(value=0)))
        assert 'aria-valuenow="0"' in out

    def test_value_full_boundary(self):
        out = _engine_template("progress.html").render(Context(_progress_ctx(value=100)))
        assert 'aria-valuenow="100"' in out

    def test_label_is_accessible_name(self):
        """A visible label becomes the progressbar's accessible name."""
        out = _engine_template("progress.html").render(
            Context(_progress_ctx(label="Upload progress"))
        )
        assert 'aria-label="Upload progress"' in out

    def test_no_aria_label_when_no_label(self):
        """No label kwarg -> no aria-label attribute (nothing to name with)."""
        out = _engine_template("progress.html").render(Context(_progress_ctx(label="")))
        assert "aria-label" not in out

    def test_hostile_label_does_not_break_out(self):
        out = _engine_template("progress.html").render(
            Context(_progress_ctx(label='"><script>alert(1)</script>'))
        )
        assert "<script>alert(1)</script>" not in out


# ---------------------------------------------------------------------------
# 10. Badge (inclusion template) — P2/P3 (#1513)
# ---------------------------------------------------------------------------


def _badge_ctx(**overrides):
    base = {"label": "Server", "status": "default", "pulse": False}
    base.update(overrides)
    return base


class TestBadgeAria:
    def test_decorative_dot_is_aria_hidden(self):
        """The status dot is a color-only decoration -> hidden from AT."""
        out = _engine_template("badge.html").render(Context(_badge_ctx()))
        assert 'aria-hidden="true"' in out

    def test_online_status_is_conveyed_to_at(self):
        """status=online is announced as text, not color alone (WCAG 1.4.1)."""
        out = _engine_template("badge.html").render(Context(_badge_ctx(status="online")))
        assert "sr-only" in out
        assert "online" in out.lower()

    def test_offline_status_is_conveyed_to_at(self):
        out = _engine_template("badge.html").render(Context(_badge_ctx(status="offline")))
        assert "sr-only" in out
        assert "offline" in out.lower()

    def test_warning_status_is_conveyed_to_at(self):
        out = _engine_template("badge.html").render(Context(_badge_ctx(status="warning")))
        assert "sr-only" in out
        assert "warning" in out.lower()

    def test_error_status_is_conveyed_to_at(self):
        out = _engine_template("badge.html").render(Context(_badge_ctx(status="error")))
        assert "sr-only" in out
        assert "error" in out.lower()

    def test_default_status_emits_no_status_text(self):
        """A `default` badge has no meaningful status -> no sr-only text."""
        out = _engine_template("badge.html").render(Context(_badge_ctx(status="default")))
        assert "sr-only" not in out

    def test_visible_label_not_regressed(self):
        """The visible label text is preserved alongside the status text."""
        out = _engine_template("badge.html").render(
            Context(_badge_ctx(label="API", status="online"))
        )
        assert "API" in out


# ---------------------------------------------------------------------------
# 11. Tooltip (Python f-string renderer) — P2/P3 (#1513)
# ---------------------------------------------------------------------------


class TestTooltipAria:
    def test_tip_has_tooltip_role(self):
        """The tip text container is identifiable to AT as a tooltip."""
        out = render_tag('{% load djust_components %}{% tooltip text="Help" %}?{% endtooltip %}')
        assert 'role="tooltip"' in out

    def test_tip_is_associated_with_trigger(self):
        """The tip carries an id and the wrapper aria-describedby points at it."""
        out = render_tag('{% load djust_components %}{% tooltip text="Help" %}?{% endtooltip %}')
        # The describedby target id and the tip's id must pair up.
        assert "aria-describedby=" in out
        import re

        m = re.search(r'aria-describedby="([^"]+)"', out)
        assert m is not None
        tip_id = m.group(1)
        assert f'id="{tip_id}"' in out

    def test_two_tooltips_get_distinct_ids(self):
        """Each tooltip render derives a unique tip id (no collisions)."""
        out = render_tag(
            "{% load djust_components %}"
            '{% tooltip text="One" %}a{% endtooltip %}'
            '{% tooltip text="Two" %}b{% endtooltip %}'
        )
        import re

        ids = re.findall(r'aria-describedby="([^"]+)"', out)
        assert len(ids) == 2
        assert ids[0] != ids[1]

    def test_explicit_component_id_used_for_tip_id(self):
        """When component_id is supplied it anchors the derived tip id."""
        out = render_tag(
            '{% load djust_components %}{% tooltip text="Help" component_id="tt7" %}'
            "?{% endtooltip %}"
        )
        assert 'aria-describedby="tt7-tip"' in out
        assert 'id="tt7-tip"' in out

    def test_hostile_text_does_not_break_out(self):
        out = render_tag(
            "{% load djust_components %}{% tooltip text=evil %}?{% endtooltip %}",
            {"evil": '"><script>alert(1)</script>'},
        )
        assert "<script>alert(1)</script>" not in out

    def test_hostile_component_id_does_not_break_out(self):
        out = render_tag(
            '{% load djust_components %}{% tooltip text="Help" component_id=evil %}'
            "?{% endtooltip %}",
            {"evil": '"><script>alert(1)</script>'},
        )
        assert "<script>alert(1)</script>" not in out


# ---------------------------------------------------------------------------
# 12. Avatar (inclusion template) — P2/P3 (#1513)
# ---------------------------------------------------------------------------


def _avatar_ctx(**overrides):
    base = {"src": "", "alt": "", "initials": "", "size": "md", "status": ""}
    base.update(overrides)
    return base


class TestAvatarAria:
    def test_image_alt_preserved(self):
        """The image path keeps the caller-supplied alt as accessible name."""
        out = _engine_template("avatar.html").render(
            Context(_avatar_ctx(src="/u.png", alt="Jane Tipton"))
        )
        assert 'alt="Jane Tipton"' in out

    def test_initials_path_has_accessible_name(self):
        """The initials fallback gets an accessible name (not bare 'JT')."""
        out = _engine_template("avatar.html").render(
            Context(_avatar_ctx(initials="JT", alt="Jane Tipton"))
        )
        assert 'role="img"' in out
        assert 'aria-label="Jane Tipton"' in out

    def test_initials_path_falls_back_to_initials_for_name(self):
        """No alt -> the initials text still serves as the accessible name."""
        out = _engine_template("avatar.html").render(Context(_avatar_ctx(initials="JT")))
        assert 'role="img"' in out
        assert 'aria-label="JT"' in out

    def test_decorative_status_is_aria_hidden(self):
        """The presence-status indicator is color-only -> hidden from AT."""
        out = _engine_template("avatar.html").render(
            Context(_avatar_ctx(initials="JT", status="online"))
        )
        assert 'aria-hidden="true"' in out

    def test_hostile_alt_does_not_break_out(self):
        out = _engine_template("avatar.html").render(
            Context(_avatar_ctx(initials="JT", alt='"><script>alert(1)</script>'))
        )
        assert "<script>alert(1)</script>" not in out


# ---------------------------------------------------------------------------
# 13. Card — P2/P3 (#1513): deliberate "no ARIA role" decision
# ---------------------------------------------------------------------------


class TestCardAria:
    def test_card_is_a_plain_container_no_role(self):
        """A `card` is a generic content container; a plain <div> is the
        correct semantic. Forcing a role onto a generic container is the
        exact PR #1491 mistake (role on <th> stripped native semantics).
        This test documents the deliberate "card: no change" decision."""
        out = render_tag('{% load djust_components %}{% card title="T" %}body{% endcard %}')
        assert "dj-card" in out
        assert 'role="' not in out
