"""
Button component for djust.

Provides buttons with multiple variants, sizes, and states.
"""

from typing import Dict, Any
from ..base import LiveComponent
from django.utils.safestring import mark_safe


class ButtonComponent(LiveComponent):
    """
    Versatile button component with framework-aware styling.

    Supports multiple variants (primary, secondary, success, danger, etc.),
    sizes (sm, md, lg), disabled state, and custom click handlers.

    Usage:
        from djust.components import ButtonComponent

        # In your LiveView:
        def mount(self, request):
            self.submit_btn = ButtonComponent(
                label="Submit Form",
                variant="primary",
                size="md",
                on_click="submit_form"
            )

            self.delete_btn = ButtonComponent(
                label="Delete",
                variant="danger",
                size="sm",
                disabled=False,
                on_click="delete_item"
            )

        # In template:
        {{ submit_btn.render }}
        {{ delete_btn.render }}
    """

    template_name = None  # Uses inline rendering

    def mount(self, **kwargs):
        """Initialize button state"""
        self.label = kwargs.get("label", "Button")
        self.variant = kwargs.get(
            "variant", "primary"
        )  # primary, secondary, success, danger, warning, info, light, dark
        self.size = kwargs.get("size", "md")  # sm, md, lg
        self.disabled = kwargs.get("disabled", False)
        self.on_click = kwargs.get("on_click", None)
        self.button_type = kwargs.get("type", "button")  # button, submit, reset
        self.outline = kwargs.get("outline", False)  # Outline variant for Bootstrap/Tailwind
        self.icon = kwargs.get("icon", None)  # Optional icon HTML/text
        self.icon_position = kwargs.get("icon_position", "left")  # left, right

    def get_context(self) -> Dict[str, Any]:
        """Get button context"""
        return {
            "label": self.label,
            "variant": self.variant,
            "size": self.size,
            "disabled": self.disabled,
            "on_click": self.on_click,
            "button_type": self.button_type,
            "outline": self.outline,
            "icon": self.icon,
            "icon_position": self.icon_position,
        }

    def set_disabled(self, disabled: bool):
        """Enable or disable the button"""
        self.disabled = disabled
        self.trigger_update()

    def set_label(self, label: str):
        """Update button label"""
        self.label = label
        self.trigger_update()

    def render(self) -> str:
        """Render button with inline HTML"""
        from ...config import config

        framework = config.get("css_framework", "bootstrap5")

        if framework == "bootstrap5":
            return mark_safe(self._render_bootstrap())
        elif framework == "tailwind":
            return mark_safe(self._render_tailwind())
        else:
            return mark_safe(self._render_plain())

    def _render_bootstrap(self) -> str:
        """Render Bootstrap 5 button"""
        # Variant mapping
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

        # Size mapping
        size_map = {"sm": "btn-sm", "md": "", "lg": "btn-lg"}
        size_class = size_map.get(self.size, "")

        # Outline variant
        outline_prefix = "outline-" if self.outline else ""

        # Build classes
        classes = f"btn btn-{outline_prefix}{variant}"
        if size_class:
            classes += f" {size_class}"

        # Disabled attribute
        disabled_attr = " disabled" if self.disabled else ""

        # Click handler
        click_attr = f' dj-click="{self.on_click}"' if self.on_click else ""

        # Icon and label
        if self.icon:
            if self.icon_position == "left":
                content = f"{self.icon} {self.label}"
            else:
                content = f"{self.label} {self.icon}"
        else:
            content = self.label

        return f'<button type="{self.button_type}" class="{classes}" id="{self.component_id}"{disabled_attr}{click_attr}>{content}</button>'

    def _render_tailwind(self) -> str:
        """Render Tailwind CSS button"""
        # Variant mapping
        variant_map = {
            "primary": "bg-blue-600 hover:bg-blue-700 text-white",
            "secondary": "bg-gray-600 hover:bg-gray-700 text-white",
            "success": "bg-green-600 hover:bg-green-700 text-white",
            "danger": "bg-red-600 hover:bg-red-700 text-white",
            "warning": "bg-yellow-500 hover:bg-yellow-600 text-white",
            "info": "bg-cyan-600 hover:bg-cyan-700 text-white",
            "light": "bg-gray-100 hover:bg-gray-200 text-gray-800",
            "dark": "bg-gray-800 hover:bg-gray-900 text-white",
        }

        # Outline variants
        outline_variant_map = {
            "primary": "border-blue-600 text-blue-600 hover:bg-blue-50",
            "secondary": "border-gray-600 text-gray-600 hover:bg-gray-50",
            "success": "border-green-600 text-green-600 hover:bg-green-50",
            "danger": "border-red-600 text-red-600 hover:bg-red-50",
            "warning": "border-yellow-500 text-yellow-600 hover:bg-yellow-50",
            "info": "border-cyan-600 text-cyan-600 hover:bg-cyan-50",
            "light": "border-gray-300 text-gray-700 hover:bg-gray-50",
            "dark": "border-gray-800 text-gray-800 hover:bg-gray-50",
        }

        if self.outline:
            variant_classes = outline_variant_map.get(self.variant, outline_variant_map["primary"])
            variant_classes = f"border-2 {variant_classes}"
        else:
            variant_classes = variant_map.get(self.variant, variant_map["primary"])

        # Size mapping
        size_map = {
            "sm": "px-3 py-1.5 text-sm",
            "md": "px-4 py-2 text-base",
            "lg": "px-6 py-3 text-lg",
        }
        size_classes = size_map.get(self.size, size_map["md"])

        # Base classes
        base_classes = "font-medium rounded-md focus:outline-none focus:ring-2 focus:ring-offset-2"

        # Disabled classes
        disabled_classes = " opacity-50 cursor-not-allowed" if self.disabled else ""

        # Build full class string
        classes = f"{base_classes} {size_classes} {variant_classes}{disabled_classes}"

        # Disabled attribute
        disabled_attr = " disabled" if self.disabled else ""

        # Click handler
        click_attr = f' dj-click="{self.on_click}"' if self.on_click else ""

        # Icon and label
        if self.icon:
            icon_spacing = "space-x-2" if self.icon else ""
            if self.icon_position == "left":
                content = f'<span class="inline-flex items-center {icon_spacing}">{self.icon}<span>{self.label}</span></span>'
            else:
                content = f'<span class="inline-flex items-center {icon_spacing}"><span>{self.label}</span>{self.icon}</span>'
        else:
            content = self.label

        return f'<button type="{self.button_type}" class="{classes}" id="{self.component_id}"{disabled_attr}{click_attr}>{content}</button>'

    def _render_plain(self) -> str:
        """Render plain HTML button"""
        # Disabled attribute
        disabled_attr = " disabled" if self.disabled else ""

        # Click handler
        click_attr = f' dj-click="{self.on_click}"' if self.on_click else ""

        # Classes
        classes = f"button button-{self.variant} button-{self.size}"

        # Icon and label
        if self.icon:
            if self.icon_position == "left":
                content = f"{self.icon} {self.label}"
            else:
                content = f"{self.label} {self.icon}"
        else:
            content = self.label

        return f'<button type="{self.button_type}" class="{classes}" id="{self.component_id}"{disabled_attr}{click_attr}>{content}</button>'
