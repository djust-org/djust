"""MentionsInput and DashboardGrid ship their interactions (#2985, batch 4).

Each component rendered a ``dj-hook`` that no shipped script answered; each now
has a script under ``djust_components/``. What has to hold on every render path
(the component class, the Django template tag and the Rust-engine handler) is
that the structure the scripts read is still emitted, and that the catalogue
finds each script.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.template import Context, Template

from djust.components import rust_handlers as rh
from djust.components.components.dashboard_grid import DashboardGrid
from djust.components.components.mentions_input import MentionsInput

STATIC = Path(rh.__file__).resolve().parent / "static" / "djust_components"

MENTION_USERS = [{"id": "1", "name": "Alice Cooper"}, {"id": "2", "name": "Bob"}]
PANELS = [
    {
        "id": "chart",
        "title": "Revenue",
        "col": 1,
        "row": 1,
        "width": 2,
        "height": 1,
        "content": "x",
    },
    {"id": "stats", "title": "Users", "col": 3, "row": 1, "width": 1, "height": 2, "content": "y"},
]


def _tag(source: str, **context) -> str:
    return Template("{% load djust_components %}" + source).render(Context(context))


def _mentions_paths(disabled: bool = False) -> dict[str, str]:
    return {
        "class": MentionsInput(
            name="message", users=MENTION_USERS, event="send_message", disabled=disabled
        ).render(),
        "tag": _tag(
            "{% mentions_input name='message' users=users event='send_message' "
            f"disabled={disabled} %}}",
            users=MENTION_USERS,
        ),
    }


def _dashboard_paths() -> dict[str, str]:
    return {
        "class": DashboardGrid(
            panels=PANELS, columns=4, move_event="mv", resize_event="rs"
        ).render(),
        "tag": _tag(
            "{% dashboard_grid panels=panels columns=4 move_event='mv' resize_event='rs' %}"
            "{% enddashboard_grid %}",
            panels=PANELS,
        ),
    }


class TestMentionsInputMarkup:
    def test_the_structure_the_hook_reads(self):
        for path, html in _mentions_paths().items():
            assert 'dj-hook="MentionsInput"' in html, path
            assert 'class="dj-mentions__input"' in html and 'name="message"' in html, path
            assert 'dj-keydown.enter="send_message"' in html, path
            assert '<ul class="dj-mentions__dropdown" role="listbox">' in html, path
            assert (
                '<li class="dj-mentions__item" data-user-id="1" data-user-name="Alice Cooper" '
                'role="option">'
            ) in html, path
            assert 'data-user-id="2" data-user-name="Bob"' in html, path

    def test_disabled_is_visible_to_the_hook(self):
        for path, html in _mentions_paths(disabled=True).items():
            assert "dj-mentions--disabled" in html, path
            assert " disabled " in html, path

    def test_the_markup_has_no_attribute_the_hook_adds_itself(self):
        """Roles, ids and expanded state are set on the client, so the markup is unchanged."""
        for path, html in _mentions_paths().items():
            for attr in (
                "aria-expanded",
                "aria-controls",
                "aria-activedescendant",
                'role="combobox"',
            ):
                assert attr not in html, (path, attr)

    def test_a_name_is_escaped_in_the_attributes_the_hook_reads(self):
        evil = [{"id": '1"><x', "name": '"><img src=x onerror=1>'}]
        for html in (
            MentionsInput(users=evil).render(),
            _tag("{% mentions_input users=users %}", users=evil),
        ):
            assert "<img" not in html and "<x" not in html


class TestDashboardGridMarkup:
    def test_the_structure_the_hook_reads(self):
        for path, html in _dashboard_paths().items():
            assert 'dj-hook="DashboardGrid"' in html, path
            assert 'data-move-event="mv"' in html and 'data-resize-event="rs"' in html, path
            assert 'data-columns="4"' in html, path
            assert 'data-panel-id="chart"' in html and 'data-panel-id="stats"' in html, path
            # the hook reads the cells from here
            assert 'style="grid-column:1/span 2;grid-row:1/span 1"' in html, path
            assert 'style="grid-column:3/span 1;grid-row:1/span 2"' in html, path
            for cls in (
                "dj-dashboard-grid__panel-header",
                "dj-dashboard-grid__panel-title",
                "dj-dashboard-grid__panel-body",
                "dj-dashboard-grid__panel-resize",
            ):
                assert f'class="{cls}"' in html, (path, cls)

    def test_the_resize_handle_is_a_direct_child_of_the_panel_and_the_header_holds_the_title(self):
        for path, html in _dashboard_paths().items():
            panel = re.search(
                r'<div class="dj-dashboard-grid__panel".*?(?=<div class="dj-dashboard-grid__panel"|$)',
                html,
                re.S,
            )
            assert panel, path
            assert re.search(
                r'<div class="dj-dashboard-grid__panel-header"><span class="dj-dashboard-grid__panel-title">Revenue</span>',
                panel.group(0),
            ), path

    def test_a_panel_id_and_title_are_escaped(self):
        evil = [{"id": '"><x', "title": "<img src=x onerror=1>", "content": ""}]
        for html in (
            DashboardGrid(panels=evil).render(),
            _tag("{% dashboard_grid panels=panels %}{% enddashboard_grid %}", panels=evil),
        ):
            assert "<img" not in html and "<x" not in html

    def test_a_non_integer_position_renders_as_one(self):
        panels = [{"id": "p", "title": "t", "col": "x", "row": None, "width": "y", "height": 0.5}]
        html = _tag("{% dashboard_grid panels=panels %}{% enddashboard_grid %}", panels=panels)
        # a height of 0.5 used to render ``span 0`` (not a placement at all); it is one cell now
        assert 'style="grid-column:1/span 1;grid-row:1/span 1"' in html


class TestDashboardGridBounds:
    """A stored or forged number cannot ask the browser for a huge grid."""

    def _render_all(self, panels, columns=4):
        return {
            "class": DashboardGrid(panels=panels, columns=columns).render(),
            "tag": _tag(
                "{% dashboard_grid panels=panels columns=columns %}{% enddashboard_grid %}",
                panels=panels,
                columns=columns,
            ),
        }

    def test_rows_and_heights_are_capped_on_every_path(self):
        panels = [{"id": "p", "col": 1, "row": 10**9, "width": 1, "height": 10**9}]
        for path, html in self._render_all(panels).items():
            assert "grid-row:100/span 1" in html, path
        panels = [{"id": "p", "col": 1, "row": 3, "width": 1, "height": 10**9}]
        for path, html in self._render_all(panels).items():
            assert "grid-row:3/span 98" in html, path  # rows 3..100

    def test_columns_are_capped_and_a_panel_stays_inside_them(self):
        for path, html in self._render_all(
            [{"id": "p", "col": 10**9, "row": 1, "width": 10**9, "height": 1}], columns=10**9
        ).items():
            assert 'data-columns="48"' in html and "repeat(48,1fr)" in html, path
            assert "grid-column:48/span 1" in html, path
        for path, html in self._render_all(
            [{"id": "p", "col": 3, "row": 1, "width": 9, "height": 1}], columns=4
        ).items():
            assert "grid-column:3/span 2" in html, path

    def test_zero_and_negative_numbers_are_one(self):
        for path, html in self._render_all(
            [{"id": "p", "col": 0, "row": -4, "width": 0, "height": -1}], columns=0
        ).items():
            assert 'data-columns="1"' in html, path
            assert 'style="grid-column:1/span 1;grid-row:1/span 1"' in html, path

    def test_in_range_values_are_untouched(self):
        for path, html in _dashboard_paths().items():
            assert 'style="grid-column:1/span 2;grid-row:1/span 1"' in html, path
            assert 'style="grid-column:3/span 1;grid-row:1/span 2"' in html, path

    def test_the_helper_the_docstring_handlers_use_clamps_every_field(self):
        from djust.components.components.dashboard_grid import MAX_COLUMNS, MAX_ROWS, clamp_cells

        assert clamp_cells(4, 2, 3, 1, 1) == (2, 3, 1, 1)
        assert clamp_cells(4, 10**9, 10**9, 10**9, 10**9) == (4, MAX_ROWS, 1, 1)
        assert clamp_cells(4, 1, 1, 10**9, 10**9) == (1, 1, 4, MAX_ROWS)
        assert clamp_cells(4, -5, -5, -5, -5) == (1, 1, 1, 1)
        assert clamp_cells(10**9, 1, 1, 10**9, 1)[2] == MAX_COLUMNS
        for bad in ("x", None, [], {}, float("inf")):
            with pytest.raises((ValueError, TypeError, OverflowError)):
                clamp_cells(4, bad, 1, 1, 1)


@pytest.mark.parametrize(
    "component, hook, script",
    [
        ("mentions_input", "MentionsInput", "mentions-input.js"),
        ("dashboard_grid", "DashboardGrid", "dashboard-grid.js"),
    ],
)
class TestScriptsAreShippedAndFound:
    def test_the_catalogue_reports_the_script_and_the_hook(self, component, hook, script):
        from djust.theming.gallery.component_registry import component_client

        assert component_client(component) == {
            "hook": hook,
            "script": f"djust_components/{script}",
            "hook_shipped": True,
        }

    def test_the_script_never_writes_html_from_data(self, component, hook, script):
        """Names, text and ids reach the page as text, attributes or CSSOM only."""
        source = (STATIC / script).read_text()
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
            assert sink not in source, sink
        assert "console." not in source
        assert "new Function" not in source
