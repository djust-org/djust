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
from typing import Any, List, Union

__all__ = ["Accordion"]


class Accordion(LiveComponent):
    """Accordion component with open/close state management."""

    class State(TypedState):
        # ``active`` is a single item id (``str``) in single mode, or a list
        # of open item ids when ``multiple`` is True.
        active: Union[str, List[str]] = ""
        multiple: bool = False

    class Meta:
        event = "accordion_toggle"

    def _handle_event(self, state: "State", value: str = "", **kwargs: Any) -> None:
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
