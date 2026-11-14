"""
Stepper — descriptor for the ``{% stepper %}`` tag's UI state (#4126).

Usage::

    class MyPage(LiveView):
        steps = Stepper(active=0)

    # template: {% stepper steps=step_list active=steps.active %} (or {% wizard ... active=steps.active %})
    # ``set_step`` answered here, no app handler needed.
"""

from djust.decorators import event_handler

from .base import LiveComponent, TypedState, bound_state
from typing import Any


__all__ = ["Stepper"]


class Stepper(LiveComponent):
    """Current step of ``{% stepper %}`` or ``{% wizard %}``.

    ``{% stepper %}`` counts steps by index (``active=0``, an ``int``) and
    ``{% wizard %}`` by step id (``active="account"``, a ``str``): the step a
    click sends is stored with the type of the declared ``active``. Validate
    before moving on by writing your own ``set_step`` handler, which wins."""

    class State(TypedState):
        active: Any = 0

    class Meta:
        # The ``{% stepper %}`` and ``{% wizard %}`` tags' default ``event`` (#4126).
        events = ("set_step",)

    @event_handler
    def set_step(self, value: Any = "", **kwargs: Any) -> None:
        """Go to the clicked step (the tags' default ``event``)."""
        state = bound_state(self)
        if isinstance(state.active, int) and not isinstance(state.active, bool):
            try:
                state.active = int(value)
            except (TypeError, ValueError):
                # Not a step index; keep the current step.
                return
        else:
            state.active = str(value)
