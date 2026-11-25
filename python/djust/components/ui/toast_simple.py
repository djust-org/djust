"""
Simple Toast component for djust - stateless.

Provides notification toasts for temporary messages.
For interactive toasts with state, use ToastComponent (LiveComponent).

One renderer (#4002): ``_render_custom`` draws the Bootstrap 5 markup the retired Rust
``RustToast`` drew, pinned byte for byte by ``python/tests/test_ui_simple_golden_4002.py``.
"""

from ..base import Component

from djust.components.utils import attr_text

_ICON = {"success": "✓", "warning": "⚠", "danger": "✗"}


class Toast(Component):
    """
    Simple, stateless toast component (Bootstrap 5).

    Use Cases:
        - Display-only toasts (no interaction needed)
        - Success/error notifications
        - Status updates

    For interactive toasts with state (dismissible, animated), use ToastComponent (LiveComponent).

    Args:
        title: Toast title (escaped)
        message: Toast message content (escaped)
        variant: Color variant (success, info, warning, danger)
        dismissable: Show close button (True/False)
        show_icon: Show icon based on variant (True/False)
        auto_hide: Auto-hide after delay (True/False)

    Examples:
        toast = Toast(title="Success", message="Item saved!", variant="success")
        html = toast.render()

        # In template
        {{ toast.render }}  # or just {{ toast }}
    """

    def __init__(
        self,
        title: str = "",
        message: str = "",
        variant: str = "info",
        dismissable: bool = True,
        show_icon: bool = True,
        auto_hide: bool = False,
    ):
        """
        Initialize toast component.

        Args:
            title: Toast title
            message: Toast message content
            variant: Color variant (success, info, warning, danger)
            dismissable: Show close button
            show_icon: Show icon based on variant
            auto_hide: Auto-hide after delay
        """
        super().__init__(
            title=title,
            message=message,
            variant=variant,
            dismissable=dismissable,
            show_icon=show_icon,
            auto_hide=auto_hide,
        )

        # Declared for type checkers; the base class already set each from the kwargs.
        self.title = title
        self.message = message
        self.variant = variant
        self.dismissable = dismissable
        self.show_icon = show_icon
        self.auto_hide = auto_hide

    def _render_custom(self) -> str:
        title, message = str(self.title), str(self.message)
        html = f'<div class="toast align-items-center text-bg-{attr_text(self.variant)} border-0"'
        html += ' role="alert" aria-live="assertive" aria-atomic="true"'
        if self.auto_hide:
            html += ' data-bs-autohide="true"'
        html += '>\n    <div class="d-flex">\n        <div class="toast-body">\n'
        if self.show_icon:
            html += f'            <span class="me-2">{_ICON.get(str(self.variant), "ℹ")}</span>'
        if title:
            html += f"            <strong>{attr_text(title)}</strong>"
        if title and message:
            html += "<br>"
        if message:
            html += f"            {attr_text(message)}\n"
        html += "        </div>\n"
        if self.dismissable:
            html += (
                '        <button type="button" class="btn-close btn-close-white me-2 m-auto"'
                ' data-bs-dismiss="toast" aria-label="Close"></button>\n'
            )
        return html + "    </div>\n</div>"
