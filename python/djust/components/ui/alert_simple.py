"""
Alert Component - Notification messages with optional dismissal

A stateless alert component for displaying messages/notifications.
Uses the automatic 3-tier performance waterfall.

Features:
- Variants (success, danger, warning, info, primary, secondary)
- Dismissable option
- Icon support

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

Performance:
    - Pure Rust: ~0.4 μs (if available)
    - Hybrid: ~2-4 μs (template cached)
    - Python: ~0.3 μs (f-string fallback)
"""

from ..base import Component

# Try to import Rust implementation
try:
    from djust._rust import RustAlert

    _RUST_AVAILABLE = True
except ImportError:
    _RUST_AVAILABLE = False
    RustAlert = None


class Alert(Component):
    """
    Simple stateless Alert component.

    Automatic performance waterfall:
    1. Pure Rust (RustAlert) - if available
    2. Rust template engine - with caching
    3. Python f-strings - fallback

    Args:
        text: Alert message text
        variant: Color variant (success, danger, warning, info, primary, secondary)
        dismissable: Whether alert can be dismissed
    """

    _rust_impl_class = RustAlert if _RUST_AVAILABLE else None

    # Template for hybrid rendering
    # Note: Using separate if blocks instead of elif (Rust template engine bug)
    template = """<div class="alert alert-{{ variant }}{% if dismissable %} alert-dismissible fade show{% endif %}" role="alert">
    {{ text }}{% if dismissable %}
    <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>{% endif %}
</div>"""

    def __init__(
        self,
        text: str,
        variant: str = "info",
        dismissable: bool = False,
    ):
        super().__init__(text=text, variant=variant, dismissable=dismissable)

        self.text = text
        self.variant = variant
        self.dismissable = dismissable

    def get_context_data(self):
        return {
            "text": self.text,
            "variant": self.variant,
            "dismissable": self.dismissable,
        }

    def _render_custom(self) -> str:
        """Python f-string fallback"""
        from djust.config import config

        framework = config.get("css_framework", "bootstrap5")

        if framework == "bootstrap5":
            return self._render_bootstrap()
        elif framework == "tailwind":
            return self._render_tailwind()
        else:
            return self._render_plain()

    def _render_bootstrap(self) -> str:
        """Render Bootstrap 5 alert"""
        classes = f"alert alert-{self.variant}"
        if self.dismissable:
            classes += " alert-dismissible fade show"

        parts = [f'<div class="{classes}" role="alert">']
        parts.append(f"    {self.text}")

        if self.dismissable:
            parts.append(
                '    <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>'
            )

        parts.append("</div>")
        return "\n".join(parts)

    def _render_tailwind(self) -> str:
        """Render Tailwind CSS alert"""
        # Map variants to Tailwind colors
        variant_map = {
            "success": "bg-green-50 border-green-200 text-green-800",
            "danger": "bg-red-50 border-red-200 text-red-800",
            "warning": "bg-yellow-50 border-yellow-200 text-yellow-800",
            "info": "bg-blue-50 border-blue-200 text-blue-800",
            "primary": "bg-blue-50 border-blue-200 text-blue-900",
            "secondary": "bg-gray-50 border-gray-200 text-gray-800",
        }
        colors = variant_map.get(self.variant, variant_map["info"])

        parts = [f'<div class="border rounded-lg p-4 {colors} relative" role="alert">']
        parts.append(f"    <span>{self.text}</span>")

        if self.dismissable:
            parts.append(
                '    <button type="button" class="absolute top-2 right-2 text-gray-400 hover:text-gray-600">'
            )
            parts.append('        <span class="sr-only">Close</span>')
            parts.append('        <svg class="h-5 w-5" fill="currentColor" viewBox="0 0 20 20">')
            parts.append(
                '            <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd"/>'
            )
            parts.append("        </svg>")
            parts.append("    </button>")

        parts.append("</div>")
        return "\n".join(parts)

    def _render_plain(self) -> str:
        """Render plain HTML alert"""
        parts = [f'<div class="alert alert-{self.variant}" role="alert">']
        parts.append(f"    {self.text}")

        if self.dismissable:
            parts.append(
                '    <button type="button" class="close" aria-label="Close">&times;</button>'
            )

        parts.append("</div>")
        return "\n".join(parts)
