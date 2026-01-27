"""
Alert component for djust.

Provides dismissible alert/notification messages with framework-aware styling.
"""

from typing import Dict, Any
from ..base import LiveComponent
from django.utils.safestring import mark_safe


class AlertComponent(LiveComponent):
    """
    Pre-built alert/notification component.

    Supports multiple alert types (info, success, warning, danger/error) and
    dismissible variants. Automatically adapts to the configured CSS framework
    (Bootstrap 5, Tailwind, or Plain HTML).

    Usage:
        from djust.components import AlertComponent

        # In your LiveView:
        def mount(self, request):
            self.alert = AlertComponent(
                message="Operation successful!",
                type="success",
                dismissible=True
            )

        # In template:
        {{ alert.render }}

        # Programmatic control:
        self.alert.show("New message", "warning")
        self.alert.dismiss()
    """

    template_name = None  # Uses inline rendering

    def mount(self, **kwargs):
        """Initialize alert state"""
        self.message = kwargs.get("message", "")
        self.type = kwargs.get("type", "info")  # info, success, warning, danger, error
        self.dismissible = kwargs.get("dismissible", True)
        self.visible = kwargs.get("visible", True)

    def get_context(self) -> Dict[str, Any]:
        """Get alert context"""
        return {
            "message": self.message,
            "type": self.type,
            "dismissible": self.dismissible,
            "visible": self.visible,
        }

    def dismiss(self):
        """Dismiss the alert"""
        self.visible = False
        self.trigger_update()

    def show(self, message: str = None, type: str = "info"):
        """Show the alert with a new message"""
        if message is not None:
            self.message = message
        if type is not None:
            self.type = type
        self.visible = True
        self.trigger_update()

    def set_message(self, message: str):
        """Update alert message"""
        self.message = message
        self.trigger_update()

    def set_type(self, type: str):
        """Update alert type"""
        self.type = type
        self.trigger_update()

    def render(self) -> str:
        """Render alert with inline HTML"""
        if not self.visible:
            return ""

        from ...config import config

        # Get CSS framework
        framework = config.get("css_framework", "bootstrap5")

        if framework == "bootstrap5":
            return mark_safe(self._render_bootstrap())
        elif framework == "tailwind":
            return mark_safe(self._render_tailwind())
        else:
            return mark_safe(self._render_plain())

    def _render_bootstrap(self) -> str:
        """Render Bootstrap 5 alert"""
        type_map = {
            "info": "info",
            "success": "success",
            "warning": "warning",
            "danger": "danger",
            "error": "danger",
        }
        alert_type = type_map.get(self.type, "info")
        dismissible_class = " alert-dismissible fade show" if self.dismissible else ""

        html = f'<div class="alert alert-{alert_type}{dismissible_class}" role="alert" id="{self.component_id}">'
        html += f"{self.message}"

        if self.dismissible:
            html += f'<button type="button" class="btn-close" dj-click="dismiss" data-component-id="{self.component_id}" aria-label="Close"></button>'

        html += "</div>"
        return html

    def _render_tailwind(self) -> str:
        """Render Tailwind CSS alert"""
        type_map = {
            "info": "bg-blue-50 text-blue-800 border-blue-200",
            "success": "bg-green-50 text-green-800 border-green-200",
            "warning": "bg-yellow-50 text-yellow-800 border-yellow-200",
            "danger": "bg-red-50 text-red-800 border-red-200",
            "error": "bg-red-50 text-red-800 border-red-200",
        }
        classes = type_map.get(self.type, "bg-blue-50 text-blue-800 border-blue-200")

        html = f'<div class="rounded-md border p-4 {classes}" id="{self.component_id}">'
        html += '<div class="flex">'
        html += f'<div class="flex-1">{self.message}</div>'

        if self.dismissible:
            html += f'<button type="button" dj-click="dismiss" data-component-id="{self.component_id}" class="ml-3 inline-flex rounded-md p-1.5 hover:bg-opacity-20">'
            html += '<span class="sr-only">Dismiss</span>'
            html += '<svg class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd" /></svg>'
            html += "</button>"

        html += "</div>"
        html += "</div>"
        return html

    def _render_plain(self) -> str:
        """Render plain HTML alert"""
        html = f'<div class="alert alert-{self.type}" id="{self.component_id}">'
        html += f"{self.message}"

        if self.dismissible:
            html += f'<button type="button" dj-click="dismiss" data-component-id="{self.component_id}">×</button>'

        html += "</div>"
        return html
