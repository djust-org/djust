"""
ExportDialog — descriptor for the ``{% export_dialog %}`` tag's UI state (#4126).

Usage::

    class MyPage(LiveView):
        exporter = ExportDialog()

    # template: {% export_dialog open=exporter.is_open formats=formats %}
    # ``open_export, close_export`` answered here, no app handler needed.
"""

from djust.decorators import event_handler

from .base import LiveComponent, TypedState, bound_state
from typing import Any


__all__ = ["ExportDialog"]


class ExportDialog(LiveComponent):
    """Open state of ``{% export_dialog %}``.

    ``event`` (``export``) stays the application's: it produces the file."""

    class State(TypedState):
        is_open: bool = False

    class Meta:
        # The ``{% export_dialog %}`` tag's default ``close_event`` (#4126).
        events = ("open_export", "close_export")

    @event_handler
    def open_export(self, **kwargs: Any) -> None:
        """Open the dialog."""
        bound_state(self).is_open = True

    @event_handler
    def close_export(self, **kwargs: Any) -> None:
        """Close the dialog (the tag's default ``close_event``)."""
        bound_state(self).is_open = False
