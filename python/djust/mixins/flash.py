"""
FlashMixin — Server-to-client flash messages for LiveView.

Provides Phoenix-style put_flash() / clear_flash() for showing transient
notifications (success, error, info, warning) in the browser:

    class MyView(LiveView):
        def handle_save(self):
            self.save_data()
            self.put_flash("success", "Record saved!")

        def handle_delete(self):
            self.delete_data()
            self.put_flash("error", "Record deleted.")
            self.clear_flash("success")
"""

from typing import Dict, List, Optional


class FlashMixin:
    """
    Mixin that provides put_flash() and clear_flash() for LiveView.

    Flash messages are queued during handler execution and flushed by the
    WebSocket consumer after the response is sent.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._pending_flash: List[Dict[str, str]] = []

    def put_flash(self, level: str, message: str) -> None:
        """
        Queue a flash message to be sent to the connected client.

        The message will be rendered into the ``#dj-flash-container`` element
        (inserted by the ``{% dj_flash %}`` template tag).

        Args:
            level: Severity / category string.  Common values are
                ``"info"``, ``"success"``, ``"warning"``, ``"error"`` but any
                string is accepted — it becomes a CSS class
                ``dj-flash-{level}``.
            message: Human-readable message text.

        Example::

            def handle_save(self):
                self.save_data()
                self.put_flash("success", "Changes saved successfully.")
        """
        self._pending_flash.append(
            {
                "action": "put",
                "level": level,
                "message": message,
            }
        )

    def clear_flash(self, level: Optional[str] = None) -> None:
        """
        Queue a command to clear flash messages on the client.

        Args:
            level: If provided, only clear messages with this level.
                If ``None``, clear all flash messages.

        Example::

            def handle_dismiss(self):
                self.clear_flash()          # clear all
                self.clear_flash("error")   # clear only errors
        """
        cmd: Dict[str, str] = {"action": "clear"}
        if level is not None:
            cmd["level"] = level
        self._pending_flash.append(cmd)

    def _drain_flash(self) -> List[Dict[str, str]]:
        """
        Drain and return all pending flash commands.

        Called by the WebSocket consumer after sending the main response.
        """
        commands = self._pending_flash
        self._pending_flash = []
        return commands
