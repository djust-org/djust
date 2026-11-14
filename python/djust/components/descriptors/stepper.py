"""
Stepper — descriptor for the ``{% stepper %}`` tag's UI state (#4126).

Usage::

    class MyPage(LiveView):
        steps = Stepper(active=0)

    # template: {% stepper steps=step_list active=steps.active %} (or {% wizard ... active=steps.active %})
    # ``set_step`` answered here, no app handler needed.
"""

from djust.decorators import event_handler

from .base import LiveComponent, TypedState, attribute_name, bound_state, warn_once
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
        """Go to the clicked step (the tags' default ``event``).

        An ``int`` ``active`` takes a non-negative step index; a ``str`` one a
        step id. A value that does not fit is refused, and the first refusal
        logs a warning naming the component.
        """
        state = bound_state(self)
        if isinstance(state.active, int) and not isinstance(state.active, bool):
            step = None
            if not isinstance(value, bool):
                try:
                    step = int(value)
                except (TypeError, ValueError):
                    step = None
            if step is None or step < 0:
                name = attribute_name(self)
                warn_once(
                    self,
                    "index",
                    "Stepper %r counts steps by index (active=%r), so set_step %r was "
                    "refused. For {%% wizard %%}, which sends step ids, declare "
                    '%s = Stepper(active="<first step id>").',
                    name,
                    state.active,
                    value,
                    name,
                )
                return
            state.active = step
        elif isinstance(value, str) and value:
            state.active = value
        else:
            warn_once(
                self,
                "id",
                "Stepper %r counts steps by id, so set_step %r was refused.",
                attribute_name(self),
                value,
            )
