"""A `[dj-virtual]` windowed list that exercises the ADR-026 failure modes.

Built for the #2017 iteration-3 gate: ROADMAP.md requires real browser evidence
before flipping `virtual_keyed_ops` on, because the #1988/#1989 failure class is
DOM state that unit tests structurally cannot see.

Each handler drives one of the cases the keyed splice ops exist for:

* ``edit_first``    — update a row while it is scrolled OUT of the window
                      (#2017 item 3; the #2136 bug was an edit landing on the
                      wrong row after a scroll)
* ``insert_middle`` — a server-side insert in the MIDDLE, which the append-only
                      absorb heuristic lands at the tail instead (#2017 item 4)
* ``remove_middle`` — a removal, which index-addressed ops mis-target once the
                      window offset differs from the item offset
* ``reorder``       — a wholesale reorder, the case the keyed ops are named for

The rows carry a stable ``dj-key`` and a visible index so a wrong landing is
readable on screen rather than only in the DOM.
"""

from __future__ import annotations

from djust import LiveView
from djust.decorators import event_handler

ROWS = 60


class VirtualKeyedDemoView(LiveView):
    template_name = "djust_tests/virtual_keyed_demo.html"

    def mount(self, request, **kwargs):
        self.items = [{"key": f"k{i}", "label": f"row {i}", "note": ""} for i in range(ROWS)]
        self.log = "mounted"

    def get_context_data(self, **kwargs):
        return {"items": self.items, "log": self.log, "count": len(self.items)}

    @event_handler()
    def edit_first(self, **kwargs):
        """Mutate row 0 — the case that must land on row 0 even after scrolling."""
        self.items[0] = {**self.items[0], "note": "EDITED"}
        self.log = "edited k0 (must appear on row 0, nowhere else)"

    @event_handler()
    def insert_middle(self, **kwargs):
        """Insert at position 5 — must land at 5, not appended at the tail."""
        n = len([i for i in self.items if i["key"].startswith("ins")])
        self.items.insert(5, {"key": f"ins{n}", "label": f"INSERTED {n}", "note": ""})
        self.log = f"inserted ins{n} at position 5 (must appear at 5, not the tail)"

    @event_handler()
    def remove_middle(self, **kwargs):
        """Remove position 3 — the rows after it must shift, none may duplicate."""
        if len(self.items) > 4:
            gone = self.items.pop(3)
            self.log = f"removed {gone['key']} from position 3"

    @event_handler()
    def reorder(self, **kwargs):
        """Reverse the list — the keyed-splice case proper."""
        self.items = list(reversed(self.items))
        self.log = "reversed the list"
