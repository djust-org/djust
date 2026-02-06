"""
PushEventMixin — Server-to-client event pushing for LiveView.

Allows handlers to push arbitrary events to client-side JS hooks:

    class MyView(LiveView):
        def handle_save(self):
            self.save_data()
            self.push_event("flash", {"message": "Saved!", "type": "success"})
            self.push_event("scroll_to", {"selector": "#bottom"})
"""

from typing import Any, Dict, List, Tuple


class PushEventMixin:
    """
    Mixin that provides push_event() for sending events to client JS.

    Events are queued during handler execution and flushed by the WebSocket
    consumer after the response is sent.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._pending_push_events: List[Tuple[str, Dict[str, Any]]] = []

    def push_event(self, event: str, payload: Dict[str, Any] = None) -> None:
        """
        Push an event to the connected client(s).

        The event will be dispatched to:
        - All dj-hook instances that registered for this event via handleEvent()
        - A CustomEvent on document for non-hook listeners

        Args:
            event: Event name (e.g. "chart_update", "djust:scroll_to")
            payload: Dict of data to send with the event

        Example::

            def handle_save(self):
                self.save_data()
                self.push_event("flash", {"message": "Saved!", "type": "success"})
                self.push_event("scroll_to", {"selector": "#result"})
        """
        if payload is None:
            payload = {}
        self._pending_push_events.append((event, payload))

    def _drain_push_events(self) -> List[Tuple[str, Dict[str, Any]]]:
        """
        Drain and return all pending push events.

        Called by the WebSocket consumer after sending the main response.
        """
        events = self._pending_push_events
        self._pending_push_events = []
        return events
