"""
Type stubs for PushEventMixin.

These stubs provide type hints for methods that are used at runtime
but may not be fully discoverable by static analysis tools.
"""

from typing import Any, Dict, Optional

class PushEventMixin:
    """Mixin that provides push_event() for sending events to client JS."""

    def push_event(self, event: str, payload: Optional[Dict[str, Any]] = None) -> None:
        """
        Push an event to the connected client(s).

        The event will be dispatched to:
        - All dj-hook instances that registered for this event via handleEvent()
        - A CustomEvent on document for non-hook listeners

        Args:
            event: Event name (e.g. "chart_update", "djust:scroll_to")
            payload: Dict of data to send with the event
        """
        ...
