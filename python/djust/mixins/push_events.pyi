"""
Type stubs for PushEventMixin.

These stubs provide type hints for methods that are used at runtime
but may not be fully discoverable by static analysis tools.
"""

from typing import Any, Dict, Optional

from djust.js import JSChain

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

    def push_commands(self, chain: JSChain) -> None:
        """
        Push a JS Command chain to the current session for immediate execution.

        Server-side half of backend-driven UI automation (ADR-002 Phase 1a).
        Serializes the chain's ops list and sends it via the ``djust:exec``
        push event. A framework-provided auto-executor on every page runs
        the chain via ``window.djust.js._executeOps(ops, null)``.

        Args:
            chain: A ``djust.js.JSChain`` instance.
        """
        ...
