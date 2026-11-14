"""
Lightbox — descriptor for the ``{% lightbox %}`` tag's UI state (#4126).

Usage::

    class MyPage(LiveView):
        viewer = Lightbox()

    # template: {% lightbox images=photos open=viewer.is_open active=viewer.active %}
    # ``open_lightbox, close_lightbox, lightbox_navigate`` answered here, no app handler needed.
"""

from djust.decorators import event_handler

from .base import LiveComponent, TypedState, bound_state
from typing import Any


__all__ = ["Lightbox"]


class Lightbox(LiveComponent):
    """Open state and current image of ``{% lightbox %}``.

    Open it from your own control with ``open_lightbox`` (``dj-click="open_lightbox"
    dj-value-value="2"`` opens the third image)."""

    class State(TypedState):
        is_open: bool = False
        active: int = 0

    class Meta:
        # The ``{% lightbox %}`` tag's default ``close_event`` and ``navigate_event`` (#4126).
        events = ("open_lightbox", "close_lightbox", "lightbox_navigate")

    @event_handler
    def open_lightbox(self, value: Any = "", **kwargs: Any) -> None:
        """Open the lightbox, at image ``value`` when it is an index."""
        state = bound_state(self)
        state.is_open = True
        try:
            state.active = max(0, int(value))
        except (TypeError, ValueError):
            # No index given: keep the current image.
            pass

    @event_handler
    def close_lightbox(self, **kwargs: Any) -> None:
        """Close the lightbox (the tag's default ``close_event``)."""
        bound_state(self).is_open = False

    @event_handler
    def lightbox_navigate(self, value: Any = "", **kwargs: Any) -> None:
        """Show image ``value`` (the tag's default ``navigate_event``)."""
        try:
            # The tag's previous button on the first image sends -1.
            bound_state(self).active = max(0, int(value))
        except (TypeError, ValueError):
            # Not an index; keep the current image.
            pass
