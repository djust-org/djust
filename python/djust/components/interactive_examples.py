"""Canonical interactive-component examples (ADR-037 D2).

The component catalogue shows this module's source and serves this view as
the entry's live preview, and the documentation harness executes the same
source (``djust.tests.doc_scenarios.generated``). One source, so the example
a reader copies is the example that is tested.
"""

from djust import LiveView
from djust.components.interactive import DropdownMenu


class DropdownMenuExample(LiveView):
    template_name = "djust_theming/catalogue/examples/interactive_dropdown_menu_page.html"
    login_required = False  # a public demo: it holds no user data

    project_menu = DropdownMenu(
        label="Project",
        items=[
            {"label": "Edit", "value": "edit"},
            {"label": "Archive", "value": "archive"},
            {"separator": True},
            {"label": "Delete", "value": "delete", "disabled": True},
        ],
    )
    row_menus = DropdownMenu.collection()

    def mount(self, request, **kwargs):
        self.selected_action = ""
        self.row_selection = ""
        self.rows = [{"id": 42, "name": "Alpha"}, {"id": 87, "name": "Beta"}]
        self.row_menus.sync(
            [
                (
                    str(row["id"]),
                    DropdownMenu(
                        label=row["name"],
                        items=[
                            {"label": "Details", "value": "details"},
                            {"label": "Rename", "value": "rename"},
                        ],
                    ),
                )
                for row in self.rows
            ]
        )

    @project_menu.on.selected
    def on_project_menu_selected(self, component: DropdownMenu, value: str) -> None:
        self.selected_action = value

    @row_menus.on.selected
    def on_row_menus_selected(self, component: DropdownMenu, value: str) -> None:
        self.row_selection = "%s:%s" % (component.key, value)
