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
from typing import Any, Optional


__all__ = ["Lightbox"]


#: Without ``total``, an index above this is refused as absurd.
MAX_INDEX = 10_000


def _index(state: Any, value: Any) -> Optional[int]:
    """The image index ``value`` names, or None to keep the current one.

    With ``total`` it wraps like ``Carousel`` (the previous button on the first
    image sends -1). Without it a negative or absurdly large index is refused.
    """
    if isinstance(value, bool):
        return None
    try:
        index = int(value)
    except (TypeError, ValueError):
        return None
    if state.total > 0:
        return index % state.total
    if index < 0 or index > MAX_INDEX:
        return None
    return index


class Lightbox(LiveComponent):
    """Open state and current image of ``{% lightbox %}``.

    Give it ``total`` (the number of images) so previous and next wrap around:
    ``Lightbox(total=len(photos))`` or ``self.viewer.total = ...`` in
    ``mount()``. Open it from your own control with ``open_lightbox``
    (``dj-click="open_lightbox" dj-value-value="2"`` opens the third image)."""

    class State(TypedState):
        is_open: bool = False
        active: int = 0
        total: int = 0

    class Meta:
        # The ``{% lightbox %}`` tag's default ``close_event`` and ``navigate_event`` (#4126).
        events = ("open_lightbox", "close_lightbox", "lightbox_navigate")

    @event_handler
    def open_lightbox(self, value: Any = "", **kwargs: Any) -> None:
        """Open the lightbox, at image ``value`` when it is an index."""
        state = bound_state(self)
        state.is_open = True
        index = _index(state, value)
        if index is not None:
            state.active = index

    @event_handler
    def close_lightbox(self, **kwargs: Any) -> None:
        """Close the lightbox (the tag's default ``close_event``)."""
        bound_state(self).is_open = False

    @event_handler
    def lightbox_navigate(self, value: Any = "", **kwargs: Any) -> None:
        """Show image ``value`` (the tag's default ``navigate_event``)."""
        state = bound_state(self)
        index = _index(state, value)
        if index is not None:
            state.active = index
