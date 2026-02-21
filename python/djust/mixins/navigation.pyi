"""
Type stubs for NavigationMixin.

These stubs provide type hints for methods that are used at runtime
but may not be fully discoverable by static analysis tools.
"""

from typing import Any, Dict, Optional

class NavigationMixin:
    """Adds URL navigation capabilities to a LiveView."""

    def live_patch(
        self,
        params: Optional[Dict[str, Any]] = None,
        path: Optional[str] = None,
        replace: bool = False,
    ) -> None:
        """
        Update the browser URL without remounting the view.

        Args:
            params: Query parameters to set. Merged with existing params.
                    Pass None to keep current params, {} to clear them.
            path: Optional new path. Defaults to current path.
            replace: If True, use replaceState instead of pushState.
        """
        ...

    def live_redirect(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        replace: bool = False,
    ) -> None:
        """
        Navigate to a different LiveView over the existing WebSocket.

        Args:
            path: URL path to navigate to (e.g. "/items/42/").
            params: Optional query parameters.
            replace: If True, use replaceState instead of pushState.
        """
        ...

    def handle_params(self, params: Dict[str, Any], uri: str) -> None:
        """
        Called when URL params change (via live_patch or browser back/forward).

        Override this to update view state based on URL params.

        Args:
            params: The new URL query parameters as a dict.
            uri: The full URI string.
        """
        ...
