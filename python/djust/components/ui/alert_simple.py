"""
Alert Component - Notification messages with optional dismissal.

A stateless alert component for displaying messages/notifications.

Example:
    from djust.components.ui import Alert

    # Simple alert
    alert = Alert(text="Operation successful!", variant="success")

    # Dismissable alert
    alert = Alert(
        text="Warning: This action cannot be undone",
        variant="warning",
        dismissable=True
    )

One renderer (#4002): ``_render_custom`` returns the markup of ``AlertComponent``, built from this
class's arguments (the mapping is in the class docstring), so the widget has one renderer.
"""

from typing import Any

from djust.components._alias_deprecation import warn_alias

from ..base import Component
from ._twin import plain_text, render_twin
from .alert import AlertComponent

#: The alert types ``AlertComponent`` draws; it draws any other as ``info``.
_TYPES = frozenset({"info", "success", "warning", "danger", "error"})


class Alert(Component):
    """
    Simple stateless Alert component, drawn by ``AlertComponent``.

    Every value is HTML-escaped, whatever it is marked as.

    Args:
        text: Alert message text (``AlertComponent``'s ``message``)
        variant: Alert type (``type``): info, success, warning, danger or error. Any other
            value (primary, secondary, light, dark) draws as ``info`` and warns
            (deprecated, #4002).
        dismissable: Whether alert can be dismissed (``dismissible``). It is rendered
            ``standalone``: under Bootstrap the close button is ``data-bs-dismiss="alert"``.
    """

    def __init__(
        self,
        text: str,
        variant: str = "info",
        dismissable: bool = False,
    ):
        super().__init__(text=text, variant=variant, dismissable=dismissable)

        # Declared for type checkers; the base class already set each from the kwargs.
        self.text = text
        self.variant = variant
        self.dismissable = dismissable

        self._warn_unmapped()

    def _warn_unmapped(self) -> None:
        """Warn for an argument value the twin has no equivalent for (#4002).

        ``stacklevel=5`` points at the caller of ``__init__`` / ``update``.
        """
        if self.variant not in _TYPES:
            warn_alias(
                "Alert",
                "variant outside info/success/warning/danger/error",
                "one of those types (any other draws as info)",
                stacklevel=5,
            )

    def update(self, **kwargs: Any) -> Component:
        """Update the properties, warning again for an argument the twin cannot draw."""
        super().update(**kwargs)
        self._warn_unmapped()
        return self

    def _render_custom(self) -> str:
        return render_twin(
            AlertComponent,
            self.id,
            message=plain_text(self.text),
            type=self.variant,
            dismissible=bool(self.dismissable),
            standalone=True,
        )
