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

One renderer (#4002): ``_render_custom`` draws the Bootstrap 5 markup the retired Rust
``RustAlert`` drew, pinned byte for byte by ``python/tests/test_ui_simple_golden_4002.py``.
"""

from ..base import Component

from djust.components.utils import attr_text


class Alert(Component):
    """
    Simple stateless Alert component (Bootstrap 5).

    Every value is HTML-escaped, whatever it is marked as.

    Args:
        text: Alert message text
        variant: Color variant (success, danger, warning, info, primary, secondary)
        dismissable: Whether alert can be dismissed
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

    def _render_custom(self) -> str:
        classes = f"alert alert-{attr_text(self.variant)}"
        if self.dismissable:
            classes += " alert-dismissible fade show"
        html = f'<div class="{classes}" role="alert">\n    {attr_text(self.text)}'
        if self.dismissable:
            html += (
                '\n    <button type="button" class="btn-close" data-bs-dismiss="alert"'
                ' aria-label="Close"></button>'
            )
        return html + "\n</div>"
