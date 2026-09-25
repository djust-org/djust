"""ADR-034 C3 live surface: a keyed collection of interactive dropdowns.

Exercised by ``tests/playwright/test_interactive_collection.py`` over
WebSocket, SSE and HTTP-only transports. Each row owns a stateful menu keyed
by its record id:

- choosing "Remove" syncs the collection without that row, from the
  collection's own callback;
- "Reverse" reorders the rows;
- "Restore" re-adds every row.

Row "c" has client-owned visibility and an observer.
"""

from djust import LiveView
from djust.components.interactive import DropdownMenu
from djust.decorators import event_handler

RECORDS = [("a", "Alpha"), ("b", "Beta"), ("c", "Gamma")]


def _row(key, name):
    return (
        key,
        DropdownMenu(
            label=name,
            items=[
                {"label": "Details", "value": "details"},
                {"label": "Remove", "value": "remove"},
            ],
            visibility="client" if key == "c" else "server",
        ),
    )


class InteractiveCollectionView(LiveView):
    template_name = "demos/interactive_collection.html"
    rows = DropdownMenu.collection()

    def mount(self, request, **kwargs):
        self.result = "none"
        self.calls = 0
        self.observed = "none"
        self.order = [key for key, _ in RECORDS]
        self._sync()

    def _sync(self):
        names = dict(RECORDS)
        self.rows.sync([_row(key, names[key]) for key in self.order])

    @rows.on.selected
    def on_rows_selected(self, component: DropdownMenu, value: str) -> None:
        self.result = "%s:%s" % (component.key, value)
        self.calls += 1
        if value == "remove":
            self.order = [key for key in self.order if key != component.key]
            self._sync()

    @rows.on.toggled
    def on_rows_toggled(self, component: DropdownMenu, open: bool) -> None:
        self.observed = "%s:%s" % (component.key, "open" if open else "closed")

    @event_handler()
    def reverse(self) -> None:
        self.order = list(reversed(self.order))
        self._sync()

    @event_handler()
    def restore(self) -> None:
        self.order = [key for key, _ in RECORDS]
        self._sync()
