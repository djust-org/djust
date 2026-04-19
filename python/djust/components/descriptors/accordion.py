"""
Accordion — descriptor-based accordion component.

Usage::

    class MyPage(LiveView):
        faq = Accordion(active="q1")
        settings = Accordion(multiple=True)

        # self.faq.active → "q1"
        # accordion_toggle event auto-registered
"""

from .base import LiveComponent, TypedState

__all__ = ["Accordion"]


class Accordion(LiveComponent):
    """Accordion component with open/close state management."""

    class State(TypedState):
        active: str = ""
        multiple: bool = False

    class Meta:
        event = "accordion_toggle"

    def _handle_event(self, state, value="", **kwargs):
        if state.multiple:
            actives = state.active
            if isinstance(actives, list):
                if value in actives:
                    actives.remove(value)
                else:
                    actives.append(value)
            else:
                state.active = [value]
        else:
            state.active = "" if state.active == value else value
