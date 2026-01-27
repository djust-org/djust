"""
Badge component for djust.

Provides small labels/badges for counts, statuses, and categories.
"""

from typing import Dict, Any
from ..base import LiveComponent
from django.utils.safestring import mark_safe


class BadgeComponent(LiveComponent):
    """
    Badge component for displaying small labels and counts.

    Supports multiple variants (primary, secondary, success, etc.),
    pill style, and dismissible badges.

    Usage:
        from djust.components import BadgeComponent

        # In your LiveView:
        def mount(self, request):
            self.status_badge = BadgeComponent(
                text="Active",
                variant="success"
            )

            self.count_badge = BadgeComponent(
                text="99+",
                variant="danger",
                pill=True
            )

            self.tag_badge = BadgeComponent(
                text="Python",
                variant="info",
                dismissible=True,
                on_dismiss="remove_tag"
            )

        # In template:
        {{ status_badge.render }}
        {{ count_badge.render }}
    """

    template_name = None  # Uses inline rendering

    def mount(self, **kwargs):
        """Initialize badge state"""
        self.text = kwargs.get("text", "")
        self.variant = kwargs.get(
            "variant", "primary"
        )  # primary, secondary, success, danger, warning, info, light, dark
        self.pill = kwargs.get("pill", False)  # Rounded pill style
        self.dismissible = kwargs.get("dismissible", False)
        self.on_dismiss = kwargs.get("on_dismiss", None)
        self.visible = True

    def get_context(self) -> Dict[str, Any]:
        """Get badge context"""
        return {
            "text": self.text,
            "variant": self.variant,
            "pill": self.pill,
            "dismissible": self.dismissible,
            "visible": self.visible,
        }

    def dismiss(self):
        """Dismiss the badge"""
        self.visible = False
        self.trigger_update()

    def set_text(self, text: str):
        """Update badge text"""
        self.text = text
        self.trigger_update()

    def render(self) -> str:
        """Render badge with inline HTML"""
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
        """Render Bootstrap 5 badge"""
        variant_map = {
            "primary": "primary",
            "secondary": "secondary",
            "success": "success",
            "danger": "danger",
            "warning": "warning",
            "info": "info",
            "light": "light",
            "dark": "dark",
        }
        variant = variant_map.get(self.variant, "primary")

        classes = f"badge bg-{variant}"
        if self.pill:
            classes += " rounded-pill"

        html = f'<span class="{classes}" id="{self.component_id}">{self.text}'

        if self.dismissible:
            dismiss_attr = (
                f'dj-click="{self.on_dismiss}"' if self.on_dismiss else 'dj-click="dismiss"'
            )
            html += f' <button type="button" class="btn-close btn-close-white" {dismiss_attr} aria-label="Close" style="font-size: 0.65em; padding: 0.1em 0.25em;"></button>'

        html += "</span>"
        return html

    def _render_tailwind(self) -> str:
        """Render Tailwind CSS badge"""
        variant_map = {
            "primary": "bg-blue-100 text-blue-800",
            "secondary": "bg-gray-100 text-gray-800",
            "success": "bg-green-100 text-green-800",
            "danger": "bg-red-100 text-red-800",
            "warning": "bg-yellow-100 text-yellow-800",
            "info": "bg-cyan-100 text-cyan-800",
            "light": "bg-gray-50 text-gray-600",
            "dark": "bg-gray-800 text-white",
        }
        variant_classes = variant_map.get(self.variant, variant_map["primary"])

        base_classes = "inline-flex items-center px-2.5 py-0.5 text-xs font-medium"
        pill_classes = " rounded-full" if self.pill else " rounded"

        classes = f"{base_classes}{pill_classes} {variant_classes}"

        html = f'<span class="{classes}" id="{self.component_id}">{self.text}'

        if self.dismissible:
            dismiss_attr = (
                f'dj-click="{self.on_dismiss}"' if self.on_dismiss else 'dj-click="dismiss"'
            )
            html += f"""<button type="button" {dismiss_attr} class="ml-1 inline-flex flex-shrink-0 rounded-full p-0.5 hover:bg-opacity-20">
                <svg class="h-3 w-3" fill="currentColor" viewBox="0 0 20 20">
                    <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd"/>
                </svg>
            </button>"""

        html += "</span>"
        return html

    def _render_plain(self) -> str:
        """Render plain HTML badge"""
        classes = f"badge badge-{self.variant}"
        if self.pill:
            classes += " badge-pill"

        html = f'<span class="{classes}" id="{self.component_id}">{self.text}'

        if self.dismissible:
            dismiss_attr = (
                f'dj-click="{self.on_dismiss}"' if self.on_dismiss else 'dj-click="dismiss"'
            )
            html += f' <button type="button" {dismiss_attr}>×</button>'

        html += "</span>"
        return html
