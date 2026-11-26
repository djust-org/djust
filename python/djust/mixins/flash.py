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

from typing import Any, Dict, List, Optional

from .commands import CommandsMixin


class FlashMixin(CommandsMixin):
    """
    Mixin that provides put_flash() and clear_flash() for LiveView.

    Flash messages are queued during handler execution on the view's one
    outbound queue (:class:`~djust.mixins.commands.CommandsMixin`) and sent
    with the turn's answer.
    """

    @property
    def _pending_flash(self) -> List[Dict[str, str]]:
        """The queued flash commands (a copy). Assigning a list replaces them."""
        return self._pending("flash")

    @_pending_flash.setter
    def _pending_flash(self, commands: Any) -> None:
        self._replace_pending("flash", list(commands or []))

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
        self._enqueue(
            "flash",
            {
                "action": "put",
                "level": level,
                "message": message,
            },
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
        self._enqueue("flash", cmd)

    def _drain_flash(self) -> List[Dict[str, str]]:
        """Drain and return the queued flash commands (only the ``flash`` effects)."""
        return [payload for _k, payload in self._drain_outbound(("flash",))]
