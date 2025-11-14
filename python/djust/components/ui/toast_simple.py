"""
Simple Toast component for djust - stateless, high-performance.

Provides notification toasts for temporary messages.
This is a stateless Component optimized for performance.
For interactive toasts with state, use ToastComponent (LiveComponent).
"""

from ..base import Component


# Try to import Rust implementation
try:
    from djust._rust import RustToast

    _RUST_AVAILABLE = True
except (ImportError, AttributeError):
    _RUST_AVAILABLE = False
    RustToast = None


class Toast(Component):
    """
    Simple, stateless toast component with automatic Rust optimization.

    This component automatically uses pure Rust implementation if available,
    otherwise falls back to hybrid rendering with Rust template engine.

    Performance:
        - Pure Rust (if available): ~1μs per render
        - Hybrid template: ~5-10μs per render
        - Pure Python fallback: ~50-100μs per render

    Use Cases:
        - Display-only toasts (no interaction needed)
        - Success/error notifications
        - Temporary messages
        - Status updates
        - High-frequency rendering

    For interactive toasts with state (dismissible, animated), use ToastComponent (LiveComponent).

    Args:
        title: Toast title
        message: Toast message content
        variant: Color variant (success, info, warning, danger)
        dismissable: Show close button (True/False)
        show_icon: Show icon based on variant (True/False)
        auto_hide: Auto-hide after delay (True/False)

    Examples:
        # Simple usage (Rust automatically used if available)
        toast = Toast(title="Success", message="Item saved!", variant="success")
        html = toast.render()

        # In template
        {{ toast.render }}  # or just {{ toast }}

        # All variants
        success = Toast("Success", "Operation completed", variant="success")
        error = Toast("Error", "Something went wrong", variant="danger")
        warning = Toast("Warning", "Please check your input", variant="warning")
        info = Toast("Info", "New updates available", variant="info")

        # With options
        dismissable = Toast("Notice", "Click X to dismiss", variant="info", dismissable=True)
        with_icon = Toast("Success", "Saved", variant="success", show_icon=True)
        auto_hide = Toast("Info", "Will hide soon", variant="info", auto_hide=True)
    """

    # Link to Rust implementation if available
    _rust_impl_class = RustToast if _RUST_AVAILABLE else None

    # Fallback: Hybrid rendering with template
    # Bootstrap 5 toast structure
    template = """<div class="toast align-items-center text-bg-{{ variant }} border-0" role="alert" aria-live="assertive" aria-atomic="true"{% if auto_hide %} data-bs-autohide="true"{% endif %}>
    <div class="d-flex">
        <div class="toast-body">
            {% if title %}<strong>{{ title }}</strong>{% endif %}
            {% if title and message %}<br>{% endif %}
            {{ message }}
        </div>
        {% if dismissable %}
        <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
        {% endif %}
    </div>
</div>"""

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

        Args will be passed to Rust implementation if available,
        otherwise stored for hybrid rendering.

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

        # Store for hybrid rendering (if Rust not used)
        self.title = title
        self.message = message
        self.variant = variant
        self.dismissable = dismissable
        self.show_icon = show_icon
        self.auto_hide = auto_hide

    def get_context_data(self):
        """Context for hybrid rendering (if Rust not available)"""
        return {
            "title": self.title,
            "message": self.message,
            "variant": self.variant,
            "dismissable": self.dismissable,
            "show_icon": self.show_icon,
            "auto_hide": self.auto_hide,
        }

    def _render_custom(self) -> str:
        """
        Custom Python rendering (fallback if no Rust and template engine fails).

        This provides framework-specific rendering for maximum compatibility.
        """
        from djust.config import config

        framework = config.get("css_framework", "bootstrap5")

        if framework == "bootstrap5":
            return self._render_bootstrap()
        elif framework == "tailwind":
            return self._render_tailwind()
        else:
            return self._render_plain()

    def _render_bootstrap(self) -> str:
        """Render Bootstrap 5 toast"""
        variant_map = {
            "success": "success",
            "info": "info",
            "warning": "warning",
            "danger": "danger",
        }
        variant = variant_map.get(self.variant, "info")

        # Icon map (Bootstrap Icons or similar)
        icon_map = {
            "success": "✓",
            "info": "ℹ",
            "warning": "⚠",
            "danger": "✗",
        }
        icon = icon_map.get(self.variant, "ℹ") if self.show_icon else ""

        auto_hide_attr = ' data-bs-autohide="true"' if self.auto_hide else ""

        html = f'<div class="toast align-items-center text-bg-{variant} border-0" role="alert" aria-live="assertive" aria-atomic="true"{auto_hide_attr}>\n'
        html += '    <div class="d-flex">\n'
        html += '        <div class="toast-body">\n'

        if icon and self.show_icon:
            html += f'            <span class="me-2">{icon}</span>'

        if self.title:
            html += f"            <strong>{self.title}</strong>"

        if self.title and self.message:
            html += "<br>"

        if self.message:
            html += f"            {self.message}\n"

        html += "        </div>\n"

        if self.dismissable:
            html += '        <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>\n'

        html += "    </div>\n"
        html += "</div>"

        return html

    def _render_tailwind(self) -> str:
        """Render Tailwind CSS toast"""
        variant_map = {
            "success": "bg-green-500 text-white",
            "info": "bg-blue-500 text-white",
            "warning": "bg-yellow-500 text-white",
            "danger": "bg-red-500 text-white",
        }
        variant_classes = variant_map.get(self.variant, variant_map["info"])

        icon_map = {
            "success": "✓",
            "info": "ℹ",
            "warning": "⚠",
            "danger": "✗",
        }
        icon = icon_map.get(self.variant, "ℹ") if self.show_icon else ""

        html = f'<div class="flex items-center p-4 mb-4 rounded-lg shadow-lg {variant_classes}" role="alert">\n'

        if icon and self.show_icon:
            html += f'    <span class="inline-flex items-center justify-center flex-shrink-0 w-8 h-8 mr-3">{icon}</span>\n'

        html += '    <div class="flex-1">\n'

        if self.title:
            html += f'        <div class="font-semibold">{self.title}</div>\n'

        if self.message:
            html += f'        <div class="text-sm">{self.message}</div>\n'

        html += "    </div>\n"

        if self.dismissable:
            html += '    <button type="button" class="ml-auto -mx-1.5 -my-1.5 rounded-lg p-1.5 inline-flex h-8 w-8" aria-label="Close">\n'
            html += '        <span class="sr-only">Close</span>\n'
            html += '        <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd"></path></svg>\n'
            html += "    </button>\n"

        html += "</div>"

        return html

    def _render_plain(self) -> str:
        """Render plain HTML toast"""
        icon_map = {
            "success": "✓",
            "info": "ℹ",
            "warning": "⚠",
            "danger": "✗",
        }
        icon = icon_map.get(self.variant, "ℹ") if self.show_icon else ""

        html = f'<div class="toast toast-{self.variant}" role="alert">\n'

        if icon and self.show_icon:
            html += f'    <span class="toast-icon">{icon}</span>\n'

        html += '    <div class="toast-content">\n'

        if self.title:
            html += f'        <strong class="toast-title">{self.title}</strong>\n'

        if self.message:
            html += f'        <div class="toast-message">{self.message}</div>\n'

        html += "    </div>\n"

        if self.dismissable:
            html += '    <button type="button" class="toast-close" aria-label="Close">×</button>\n'

        html += "</div>"

        return html
