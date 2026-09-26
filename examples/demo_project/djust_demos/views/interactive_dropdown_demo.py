"""ADR-034 C2 live surface: interactive dropdowns in a real browser.

Exercised by ``tests/playwright/test_interactive_dropdown.py`` over
WebSocket, SSE and HTTP-only transports.

- ``project_menu`` and ``account_menu`` are two server-owned menus of the same
  type, each with its own ``selected`` callback. ``project_menu`` has a
  disabled item.
- ``quiet_menu``, ``live_menu`` and ``silent_menu`` are client-owned (native
  popover):
  - ``quiet_menu``'s ``toggled`` observer changes no state, so its reports
    must produce no render.
  - ``live_menu``'s observer records the reported visibility, so it renders.
  - ``silent_menu`` has no observer and must send nothing when toggled.

``observations`` counts every observer call, including the quiet ones, in a
plain (non-rendered) list, so the page can show it on demand without the
quiet observer itself changing state.
"""

from djust import LiveView
from djust.components.interactive import DropdownMenu
from djust.decorators import event_handler

#: Observer calls, per process. The quiet observer must not change view state.
OBSERVED: list = []


class InteractiveDropdownView(LiveView):
    template_name = "demos/interactive_dropdown.html"

    project_menu = DropdownMenu(
        label="Project",
        items=[
            {"label": "Edit", "value": "edit"},
            {"label": "Archive", "value": "archive"},
            {"separator": True},
            {"label": "Locked", "value": "locked", "disabled": True},
        ],
    )
    account_menu = DropdownMenu(
        label="Account",
        items=[{"label": "Settings", "value": "settings"}],
    )
    quiet_menu = DropdownMenu(
        label="Quiet", items=[{"label": "One", "value": "one"}], visibility="client"
    )
    live_menu = DropdownMenu(
        label="Live", items=[{"label": "Two", "value": "two"}], visibility="client"
    )
    silent_menu = DropdownMenu(
        label="Silent", items=[{"label": "Three", "value": "three"}], visibility="client"
    )

    def mount(self, request, **kwargs):
        self.result = "none"
        self.calls = 0
        self.live_open = "unknown"
        self.observed_count = 0

    @project_menu.on.selected
    def on_project_menu_selected(self, component: DropdownMenu, value: str) -> None:
        self.result = "project:%s" % value
        self.calls += 1

    @account_menu.on.selected
    def on_account_menu_selected(self, component: DropdownMenu, value: str) -> None:
        self.result = "account:%s" % value
        self.calls += 1

    @quiet_menu.on.toggled
    def on_quiet_menu_toggled(self, component: DropdownMenu, open: bool) -> None:
        OBSERVED.append(("quiet", open))

    @live_menu.on.toggled
    def on_live_menu_toggled(self, component: DropdownMenu, open: bool) -> None:
        OBSERVED.append(("live", open))
        self.live_open = "open" if open else "closed"

    @event_handler()
    def show_observed(self) -> None:
        """Render the observer log on demand (a separate, ordinary event)."""
        self.observed_count = len(OBSERVED)
