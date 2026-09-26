"""ADR-034 C4 live surface: interactive dropdown acceptance cases.

Exercised by ``tests/playwright/test_interactive_acceptance.py``:

- an async output callback;
- a callback that raises after the component applied its selection (no
  rollback, no second call);
- the ``close`` action;
- duplicate and stale visibility observations;
- keyboard focus;
- isolation between two browsers;
- a legacy plain ``DropdownMenu`` still driven by its own view handlers.
"""

import asyncio

from djust import LiveView
from djust.components.interactive import DropdownMenu
from djust.components.components import DropdownMenu as LegacyDropdownMenu  # noqa: Q004
from djust.decorators import event_handler

#: Observer calls per process; the observer itself changes no view state.
OBSERVED: list = []


class InteractiveAcceptanceView(LiveView):
    template_name = "demos/interactive_acceptance.html"

    menu = DropdownMenu(
        label="Actions",
        items=[{"label": "Save", "value": "save"}, {"label": "Fail", "value": "fail"}],
    )
    quiet = DropdownMenu(
        label="Quiet", items=[{"label": "One", "value": "one"}], visibility="client"
    )

    def mount(self, request, **kwargs):
        self.result = "none"
        self.calls = 0
        self.observed = 0
        self.legacy = LegacyDropdownMenu(
            label="Legacy",
            items=[{"label": "Edit", "event": "legacy_edit"}],
            toggle_event="toggle_legacy",
        )

    @menu.on.selected
    async def on_menu_selected(self, component: DropdownMenu, value: str) -> None:
        self.calls += 1
        if value == "fail":
            raise RuntimeError("demo callback failure")
        await asyncio.sleep(0.05)
        self.result = "saved"

    @quiet.on.toggled
    def on_quiet_toggled(self, component: DropdownMenu, open: bool) -> None:
        OBSERVED.append(open)

    @event_handler()
    def refresh(self) -> None:
        """Render the counters on demand (an ordinary event)."""
        self.observed = len(OBSERVED)
        self.result = self.result

    @event_handler()
    def toggle_legacy(self, **kwargs) -> None:
        self.legacy.open = not self.legacy.open

    @event_handler()
    def legacy_edit(self, **kwargs) -> None:
        self.result = "legacy:edit"
        self.legacy.open = False
