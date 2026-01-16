"""
Spinner component for djust.

Provides loading spinners for indicating activity.
"""

from typing import Dict, Any
from ..base import LiveComponent
from django.utils.safestring import mark_safe


class SpinnerComponent(LiveComponent):
    """
    Spinner/loading indicator component.

    Displays an animated spinner to indicate loading or processing state.

    Usage:
        from djust.components import SpinnerComponent

        # In your LiveView:
        def mount(self, request):
            self.loading_spinner = SpinnerComponent(
                variant="primary",
                size="md",
                label="Loading..."
            )

        # Show/hide spinner
        def start_loading(self):
            self.loading_spinner.show()

        def stop_loading(self):
            self.loading_spinner.hide()

        # In template:
        {{ loading_spinner.render }}
    """

    template_name = None  # Uses inline rendering

    def mount(self, **kwargs):
        """Initialize spinner state"""
        self.variant = kwargs.get(
            "variant", "primary"
        )  # primary, secondary, success, danger, warning, info, light, dark
        self.size = kwargs.get("size", "md")  # sm, md, lg, xl
        self.type = kwargs.get(
            "type", "border"
        )  # border, grow (for Bootstrap), dots, pulse, ring (general types)
        self.label = kwargs.get("label", None)  # Optional loading text
        self.center = kwargs.get("center", False)  # Center the spinner
        self.visible = kwargs.get("visible", True)  # Show/hide state
        self.inline = kwargs.get("inline", False)  # Inline vs block display

    def get_context(self) -> Dict[str, Any]:
        """Get spinner context"""
        return {
            "variant": self.variant,
            "size": self.size,
            "type": self.type,
            "label": self.label,
            "visible": self.visible,
        }

    def show(self):
        """Show the spinner"""
        self.visible = True
        self.trigger_update()

    def hide(self):
        """Hide the spinner"""
        self.visible = False
        self.trigger_update()

    def toggle(self):
        """Toggle spinner visibility"""
        self.visible = not self.visible
        self.trigger_update()

    def set_label(self, label: str):
        """Update spinner label"""
        self.label = label
        self.trigger_update()

    def render(self) -> str:
        """Render spinner with inline HTML"""
        if not self.visible:
            return ""

        from ...config import config

        framework = config.get("css_framework", "bootstrap5")

        if framework == "bootstrap5":
            return mark_safe(self._render_bootstrap())
        elif framework == "tailwind":
            return mark_safe(self._render_tailwind())
        else:
            return mark_safe(self._render_plain())

    def _render_bootstrap(self) -> str:
        """Render Bootstrap 5 spinner"""
        # Size mapping
        size_map = {"sm": "spinner-border-sm", "md": "", "lg": "", "xl": ""}
        size_class = size_map.get(self.size, "")

        # Custom size for lg/xl
        custom_size = ""
        if self.size == "lg":
            custom_size = ' style="width: 3rem; height: 3rem;"'
        elif self.size == "xl":
            custom_size = ' style="width: 5rem; height: 5rem;"'

        # Spinner type
        spinner_type = "spinner-border" if self.type == "border" else "spinner-grow"

        # Variant
        variant_class = f"text-{self.variant}"

        # Container classes
        container_class = ""
        if self.center:
            container_class = "d-flex justify-content-center align-items-center"
        elif self.inline:
            container_class = "d-inline-block"

        # Build HTML
        html = (
            f'<div class="{container_class}" id="{self.component_id}">'
            if container_class
            else f'<div id="{self.component_id}">'
        )

        html += (
            f'<div class="{spinner_type} {size_class} {variant_class}" role="status"{custom_size}>'
        )
        html += '<span class="visually-hidden">Loading...</span>'
        html += "</div>"

        # Label
        if self.label:
            html += f'<span class="ms-2">{self.label}</span>'

        html += "</div>"
        return html

    def _render_tailwind(self) -> str:
        """Render Tailwind CSS spinner"""
        # Size mapping
        size_map = {
            "sm": "h-4 w-4",
            "md": "h-8 w-8",
            "lg": "h-12 w-12",
            "xl": "h-16 w-16",
        }
        size_class = size_map.get(self.size, "h-8 w-8")

        # Variant colors
        variant_map = {
            "primary": "border-blue-600",
            "secondary": "border-gray-600",
            "success": "border-green-600",
            "danger": "border-red-600",
            "warning": "border-yellow-500",
            "info": "border-cyan-600",
            "light": "border-gray-300",
            "dark": "border-gray-900",
        }
        color_class = variant_map.get(self.variant, "border-blue-600")

        # Container classes
        container_class = "flex items-center"
        if self.center:
            container_class += " justify-center"
        if self.inline:
            container_class += " inline-flex"

        # Spinner animation based on type
        if self.type == "grow" or self.type == "pulse":
            # Pulse animation
            html = f'<div class="{container_class}" id="{self.component_id}">'
            html += f'<div class="{size_class} {color_class.replace("border-", "bg-")} rounded-full animate-pulse"></div>'
        elif self.type == "dots":
            # Dots animation
            html = f'<div class="{container_class} space-x-2" id="{self.component_id}">'
            for i in range(3):
                delay = f"animation-delay: {i * 150}ms;" if i > 0 else ""
                html += f'<div class="h-2 w-2 {color_class.replace("border-", "bg-")} rounded-full animate-bounce" style="{delay}"></div>'
            html += "</div>"
            if self.label:
                html += f'<span class="ml-2 text-gray-700">{self.label}</span>'
            return html
        else:
            # Border/ring spinner (default)
            html = f'<div class="{container_class}" id="{self.component_id}">'
            html += f'<div class="animate-spin rounded-full {size_class} border-4 border-gray-200 {color_class} border-t-transparent"></div>'

        # Label
        if self.label:
            html += f'<span class="ml-3 text-gray-700">{self.label}</span>'

        html += "</div>"
        return html

    def _render_plain(self) -> str:
        """Render plain HTML spinner"""
        # Size mapping
        size_map = {
            "sm": "spinner-sm",
            "md": "spinner-md",
            "lg": "spinner-lg",
            "xl": "spinner-xl",
        }
        size_class = size_map.get(self.size, "spinner-md")

        # Container
        container_class = "spinner-container"
        if self.center:
            container_class += " spinner-center"
        if self.inline:
            container_class += " spinner-inline"

        html = f'<div class="{container_class}" id="{self.component_id}">'

        # Spinner type
        if self.type == "dots":
            html += f'<div class="spinner-dots {size_class}">'
            html += "<span></span><span></span><span></span>"
            html += "</div>"
        elif self.type == "pulse":
            html += f'<div class="spinner-pulse {size_class} spinner-{self.variant}"></div>'
        else:
            # Default border spinner
            html += f'<div class="spinner {size_class} spinner-{self.variant}">'
            html += '<div class="spinner-border"></div>'
            html += "</div>"

        # Label
        if self.label:
            html += f'<span class="spinner-label">{self.label}</span>'

        html += "</div>"
        return html
