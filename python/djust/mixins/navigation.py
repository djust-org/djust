"""
Navigation mixin for LiveView — URL state management.

Provides live_patch() (update URL without remount) and live_redirect()
(navigate to a different view over the same WebSocket connection).

Inspired by Phoenix LiveView's live_patch and live_redirect.
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class NavigationMixin:
    """
    Adds URL navigation capabilities to a LiveView.

    Methods:
        live_patch(params=None, path=None, replace=False):
            Update URL query params without remounting. Triggers re-render.

        live_redirect(path, params=None, replace=False):
            Navigate to a different view over the existing WebSocket.

    The mixin queues navigation commands that are flushed to the client
    after each handler execution, similar to push_event.
    """

    def _init_navigation(self):
        """Initialize navigation state. Called from __init__."""
        self._pending_navigation: List[Dict[str, Any]] = []

    def live_patch(
        self,
        params: Optional[Dict[str, Any]] = None,
        path: Optional[str] = None,
        replace: bool = False,
    ) -> None:
        """
        Update the browser URL without remounting the view.

        The view's state is updated and re-rendered, but mount() is NOT called
        again. The browser URL changes via history.pushState (or replaceState
        if replace=True).

        Args:
            params: Query parameters to set. Merged with existing params.
                    Pass None to keep current params, {} to clear them.
            path: Optional new path. Defaults to current path.
            replace: If True, use replaceState instead of pushState.

        Example::

            @event_handler
            def filter_results(self, category="all", **kwargs):
                self.category = category
                self.live_patch(params={"category": category, "page": 1})
        """
        nav = {
            "type": "live_patch",
            "replace": replace,
        }
        if params is not None:
            nav["params"] = params
        if path is not None:
            nav["path"] = path
        self._pending_navigation.append(nav)

    def live_redirect(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        replace: bool = False,
    ) -> None:
        """
        Navigate to a different LiveView over the existing WebSocket.

        The current view is unmounted and the new view is mounted without
        a full page reload or WebSocket reconnection.

        Args:
            path: URL path to navigate to (e.g. "/items/42/").
            params: Optional query parameters.
            replace: If True, use replaceState instead of pushState.

        Example::

            @event_handler
            def go_to_detail(self, item_id, **kwargs):
                self.live_redirect(f"/items/{item_id}/")
        """
        nav = {
            "type": "live_redirect",
            "path": path,
            "replace": replace,
        }
        if params is not None:
            nav["params"] = params
        self._pending_navigation.append(nav)

    def _drain_navigation(self) -> List[Dict[str, Any]]:
        """Drain and return pending navigation commands."""
        commands = self._pending_navigation
        self._pending_navigation = []
        return commands

    def handle_params(self, params: Dict[str, Any], uri: str) -> None:
        """
        Called when URL params change (via live_patch or browser back/forward).

        Override this to update view state based on URL params.

        Args:
            params: The new URL query parameters as a dict.
            uri: The full URI string.

        Example::

            def handle_params(self, params, uri):
                self.category = params.get("category", "all")
                self.page = int(params.get("page", 1))
        """
        pass
