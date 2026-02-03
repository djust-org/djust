"""
AccessibilityMixin — WCAG-compliant accessibility features for LiveView.

Provides screen reader announcements, focus management, and ARIA configuration:

    class MyView(LiveView):
        def handle_save(self):
            self.save_data()
            self.announce("Data saved successfully!", priority="polite")
            self.focus("#result-message")

Features:
- announce(message, priority): Push screen reader announcements
- focus(selector): Set focus after DOM updates
- Configurable ARIA live region defaults
"""

from typing import Any, Dict, List, Optional, Tuple


class AccessibilityMixin:
    """
    Mixin that provides accessibility features for LiveView.

    Screen Reader Announcements:
        Use announce() to push messages to screen readers via ARIA live regions.
        Messages are queued during handler execution and sent to the client.

    Focus Management:
        Use focus() to programmatically set focus after DOM updates.
        Useful for focusing error messages, success notifications, etc.

    Configuration:
        Set class attributes to configure default accessibility behavior:
        - aria_live_default: Default priority for patched regions ("polite", "assertive", "off")
        - auto_focus_errors: Whether to auto-focus first error after form submission
        - announce_loading: Whether to announce loading states to screen readers
    """

    # Configuration defaults
    aria_live_default: str = "polite"  # Default ARIA live priority for patches
    auto_focus_errors: bool = True     # Auto-focus first error after validation
    announce_loading: bool = True      # Announce loading states to screen readers

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._pending_announcements: List[Tuple[str, str]] = []
        self._pending_focus: Optional[str] = None
        self._focus_options: Dict[str, Any] = {}

    def announce(self, message: str, priority: str = "polite") -> None:
        """
        Announce a message to screen readers via ARIA live region.

        The message will be displayed in a visually-hidden live region that
        screen readers will announce automatically.

        Args:
            message: Text to announce to screen readers
            priority: ARIA live priority - "polite" (default) or "assertive"
                     Use "assertive" for urgent messages (errors, critical alerts)
                     Use "polite" for non-urgent updates (success messages, status)

        Example::

            def handle_save(self):
                self.save_data()
                self.announce("Changes saved successfully!")

            def handle_error(self):
                self.announce("Form contains errors. Please review.", priority="assertive")
        """
        if priority not in ("polite", "assertive"):
            priority = "polite"
        self._pending_announcements.append((message, priority))

    def focus(
        self,
        selector: str,
        *,
        scroll: bool = True,
        prevent_scroll: bool = False,
        delay_ms: int = 0
    ) -> None:
        """
        Set focus to an element after DOM updates.

        Only the last focus() call per handler execution takes effect.
        Use CSS selectors to target the element.

        Args:
            selector: CSS selector for the element to focus (e.g., "#error-message", ".first-input")
            scroll: Whether to scroll the element into view (default: True)
            prevent_scroll: If True, prevents any scrolling (overrides scroll)
            delay_ms: Milliseconds to delay focus (useful for animations)

        Example::

            def handle_submit(self):
                if self.errors:
                    self.focus("#first-error", scroll=True)
                else:
                    self.focus("#success-message")
        """
        self._pending_focus = selector
        self._focus_options = {
            "scroll": scroll,
            "preventScroll": prevent_scroll,
            "delayMs": delay_ms,
        }

    def focus_first_error(self) -> None:
        """
        Focus the first form error element.

        Looks for elements with common error classes/attributes:
        - .error, .is-invalid, [aria-invalid="true"]
        - .field-error, .form-error

        Called automatically after form submission if auto_focus_errors is True.
        """
        # Use a special selector that client-side will resolve
        self._pending_focus = "__djust_first_error__"
        self._focus_options = {"scroll": True, "preventScroll": False, "delayMs": 0}

    def _drain_announcements(self) -> List[Tuple[str, str]]:
        """
        Drain and return all pending announcements.

        Called by the WebSocket consumer after sending the main response.
        """
        announcements = self._pending_announcements
        self._pending_announcements = []
        return announcements

    def _drain_focus(self) -> Optional[Tuple[str, Dict[str, Any]]]:
        """
        Drain and return pending focus command.

        Called by the WebSocket consumer after sending the main response.
        """
        if self._pending_focus:
            result = (self._pending_focus, self._focus_options)
            self._pending_focus = None
            self._focus_options = {}
            return result
        return None

    def get_accessibility_config(self) -> Dict[str, Any]:
        """
        Get the accessibility configuration for this view.

        Sent to the client during mount to configure client-side behavior.
        """
        return {
            "ariaLiveDefault": self.aria_live_default,
            "autoFocusErrors": self.auto_focus_errors,
            "announceLoading": self.announce_loading,
        }
