"""
Toasts — descriptor for the toasts ``{% toast_container %}`` shows (#4134).

Usage::

    class MyPage(LiveView):
        toasts = Toasts()

        @event_handler
        def save(self, **kwargs):
            ...
            self.toasts.show("Saved", type="success")

    # template: {% toast_container toasts.items %}
    # ``dismiss_toast`` answered here, no app handler needed.
"""

from djust.decorators import event_handler

from .base import LiveComponent, TypedState, bound_state
from typing import Any, Sequence


__all__ = ["Toasts"]


#: The most toasts kept at once: showing another drops the oldest.
MAX_TOASTS = 50
#: The types ``{% toast_container %}`` draws an icon for.
TYPES = ("success", "error", "warning", "info")


class Toasts(LiveComponent):
    """The toasts on screen, each ``{"id", "type", "message"}``.

    ``show()`` adds one; the × of ``{% toast_container %}`` sends
    ``dismiss_toast`` with the toast's id, which removes it. An id that names no
    toast on screen is ignored."""

    class State(TypedState):
        items: Sequence[dict] = ()
        #: The id the next ``show()`` gives.
        next_id: int = 1

    class Meta:
        # The ``{% toast_container %}`` tag's default ``dismiss_event`` (#4134).
        events = ("dismiss_toast",)

    def show(self, message: str, type: str = "info") -> str:
        """Add a toast and return its id. ``type`` is one of ``TYPES`` (else ``info``).

        At most ``MAX_TOASTS`` are kept; the oldest go first."""
        state = bound_state(self)
        number = state.next_id if isinstance(state.next_id, int) else 1
        toast_id = "t%s" % number
        state.next_id = number + 1
        kind = type if type in TYPES else "info"
        items = [*(state.items or ()), {"id": toast_id, "type": kind, "message": str(message)}]
        state.items = items[-MAX_TOASTS:]
        return toast_id

    #: Never called from a template (``{{ toasts.show }}``).
    show.alters_data = True  # type: ignore[attr-defined]

    @event_handler
    def dismiss_toast(self, value: Any = "", **kwargs: Any) -> None:
        """Remove toast ``value`` (the × button's default ``dismiss_event``)."""
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            return
        state = bound_state(self)
        items = list(state.items or ())
        kept = [t for t in items if not (isinstance(t, dict) and str(t.get("id")) == str(value))]
        if len(kept) != len(items):
            state.items = kept
