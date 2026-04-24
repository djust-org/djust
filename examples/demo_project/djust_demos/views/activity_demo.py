"""
{% dj_activity %} demo — 3-tab panel with preserved form state.

Demonstrates React 19.2 ``<Activity>`` parity: switching tabs hides the
inactive panels via the HTML ``hidden`` attribute, but their DOM subtree
(including form input values, scroll position, and transient JS state)
is preserved. No re-render, no state loss.
"""

from djust.decorators import event_handler
from djust_shared.views import BaseViewWithNavbar


class ActivityDemoView(BaseViewWithNavbar):
    """Three-tab demo of ``{% dj_activity %}`` with preserved form state."""

    template_name = "demos/activity_demo.html"

    def mount(self, request, **kwargs):
        # Server-side "active tab" is the single source of visibility truth.
        # Every panel still renders — the inactive ones just get the
        # ``hidden`` attribute on their wrapper.
        self.active_tab = "profile"

        # Per-panel public state. Each panel uses its own assigns so a
        # handler that only updates one tab doesn't re-render the others.
        self.profile_name = ""
        self.profile_email = ""
        self.notes_text = ""
        self.counter = 0

    @event_handler
    def switch_tab(self, tab: str = "", **kwargs):
        """Switch the active tab. Inactive panels get ``hidden`` on next render."""
        if tab in {"profile", "notes", "counter"}:
            self.active_tab = tab

    @event_handler
    def set_profile_name(self, value: str = "", **kwargs):
        self.profile_name = value

    @event_handler
    def set_profile_email(self, value: str = "", **kwargs):
        self.profile_email = value

    @event_handler
    def set_notes(self, value: str = "", **kwargs):
        self.notes_text = value

    @event_handler
    def increment(self, **kwargs):
        self.counter += 1

    @event_handler
    def decrement(self, **kwargs):
        self.counter -= 1
