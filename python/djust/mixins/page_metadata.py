"""
PageMetadataMixin — Dynamic document title and meta tag updates for LiveView.

Provides property setters that queue side-channel commands for the WebSocket
consumer to flush to the client, without requiring a VDOM diff cycle:

    class ChatView(LiveView):
        def mount(self, request, **kwargs):
            self.page_title = "Chat"

        def handle_new_message(self, **kwargs):
            self.unread += 1
            self.page_title = f"Chat ({self.unread} unread)"
            self.page_meta = {"description": "Active chat"}
"""

from typing import Dict, List


class PageMetadataMixin:
    """
    Mixin that provides page_title and page_meta property setters for LiveView.

    Setting these properties queues metadata commands that are flushed by the
    WebSocket consumer after the response is sent (same pattern as FlashMixin).

    For HTTP initial render, ``page_title`` and ``page_meta`` are accessible
    as properties on the view instance (``self.page_title``, ``self.page_meta``).
    To use them in templates, include them in your ``get_context_data()``::

        def get_context_data(self, **kwargs):
            ctx = super().get_context_data(**kwargs)
            ctx["page_title"] = self.page_title
            return ctx
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._pending_page_metadata: List[Dict] = []
        self._page_title: str = ""
        self._page_meta: Dict[str, str] = {}

    @property
    def page_title(self) -> str:
        """Get the current page title."""
        return self._page_title

    @page_title.setter
    def page_title(self, value: str) -> None:
        """Set the page title and queue a side-channel command."""
        self._page_title = value
        self._pending_page_metadata.append({"action": "title", "value": value})

    @property
    def page_meta(self) -> Dict[str, str]:
        """Get the current page meta tags."""
        return self._page_meta

    @page_meta.setter
    def page_meta(self, value: Dict[str, str]) -> None:
        """Set page meta tags and queue side-channel commands (one per key)."""
        self._page_meta = value
        for name, content in value.items():
            self._pending_page_metadata.append({"action": "meta", "name": name, "content": content})

    def _drain_page_metadata(self) -> List[Dict]:
        """
        Drain and return all pending page metadata commands.

        Called by the WebSocket consumer after sending the main response.
        """
        commands = self._pending_page_metadata
        self._pending_page_metadata = []
        return commands
