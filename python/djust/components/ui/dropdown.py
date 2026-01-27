"""
Dropdown component for djust.

Provides dropdown menus with items and actions.
"""

from typing import Dict, Any, Optional
from dataclasses import dataclass
from ..base import LiveComponent
from django.utils.safestring import mark_safe


@dataclass
class DropdownItem:
    """
    Represents a single dropdown menu item.

    Args:
        text: Display text for the item
        action: Event handler name to call when clicked
        data: Optional dictionary of data to pass to the handler
        icon: Optional icon/emoji to display before text
        variant: Optional color variant (danger, warning, etc.)
        divider: If True, renders as a divider line instead
    """

    text: str = ""
    action: str = ""
    data: Optional[Dict[str, Any]] = None
    icon: str = ""
    variant: str = ""
    divider: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format for backward compatibility"""
        if self.divider:
            return {"divider": True}

        result = {
            "text": self.text,
            "action": self.action,
        }
        if self.data:
            result["data"] = self.data
        if self.icon:
            result["icon"] = self.icon
        if self.variant:
            result["variant"] = self.variant
        return result


class DropdownComponent(LiveComponent):
    """
    Dropdown menu component.

    Displays a button that opens a menu with clickable items.

    Usage:
        from djust.components import DropdownComponent

        # In your LiveView:
        def mount(self, request):
            self.actions_dropdown = DropdownComponent(
                label="Actions",
                variant="primary",
                items=[
                    {'text': 'Edit', 'action': 'edit_item', 'icon': '✏️'},
                    {'text': 'Delete', 'action': 'delete_item', 'icon': '🗑️', 'variant': 'danger'},
                    {'divider': True},
                    {'text': 'Archive', 'action': 'archive_item'},
                ]
            )

        # In template:
        {{ actions_dropdown.render }}
    """

    template_name = None  # Uses inline rendering

    def mount(self, **kwargs):
        """Initialize dropdown state"""
        self.label = kwargs.get("label", "Dropdown")
        self.variant = kwargs.get("variant", "secondary")
        self.size = kwargs.get("size", "md")
        self.split = kwargs.get("split", False)  # Split dropdown button
        self.direction = kwargs.get("direction", "down")  # down, up, start, end

        # Accept both DropdownItem objects and dicts for backward compatibility
        items = kwargs.get("items", [])
        self.items = []
        for item in items:
            if isinstance(item, DropdownItem):
                self.items.append(item.to_dict())
            else:
                self.items.append(item)

    def get_context(self) -> Dict[str, Any]:
        """Get dropdown context"""
        return {
            "label": self.label,
            "variant": self.variant,
            "size": self.size,
            "items": self.items,
            "split": self.split,
            "direction": self.direction,
        }

    def render(self) -> str:
        """Render dropdown with inline HTML"""
        from ...config import config

        framework = config.get("css_framework", "bootstrap5")

        if framework == "bootstrap5":
            return mark_safe(self._render_bootstrap())
        elif framework == "tailwind":
            return mark_safe(self._render_tailwind())
        else:
            return mark_safe(self._render_plain())

    def _render_bootstrap(self) -> str:
        """Render Bootstrap 5 dropdown"""
        size_map = {"sm": "btn-sm", "md": "", "lg": "btn-lg"}
        size_class = size_map.get(self.size, "")

        direction_map = {
            "down": "dropdown",
            "up": "dropup",
            "start": "dropstart",
            "end": "dropend",
        }
        direction_class = direction_map.get(self.direction, "dropdown")

        html = f'<div class="btn-group {direction_class}" id="{self.component_id}">'

        button_class = f"btn btn-{self.variant} {size_class}".strip()

        if self.split:
            # Split button dropdown
            html += f'<button type="button" class="{button_class}">{self.label}</button>'
            html += f'<button type="button" class="{button_class} dropdown-toggle dropdown-toggle-split" data-bs-toggle="dropdown" aria-expanded="false">'
            html += '<span class="visually-hidden">Toggle Dropdown</span></button>'
        else:
            # Regular dropdown
            html += f'<button type="button" class="{button_class} dropdown-toggle" data-bs-toggle="dropdown" aria-expanded="false">{self.label}</button>'

        html += '<ul class="dropdown-menu">'

        for item in self.items:
            if item.get("divider"):
                html += '<li><hr class="dropdown-divider"></li>'
            else:
                text = item.get("text", "")
                action = item.get("action", "")
                icon = item.get("icon", "")
                item_variant = item.get("variant", "")
                data = item.get("data", {})

                click_attr = f' dj-click="{action}"' if action else ""
                variant_class = f" text-{item_variant}" if item_variant else ""

                # Add data-* attributes for event parameters
                data_attrs = ""
                if data:
                    import json

                    for key, value in data.items():
                        # Convert value to JSON string for complex types
                        if isinstance(value, (dict, list)):
                            value_str = json.dumps(value).replace('"', "&quot;")
                        else:
                            value_str = str(value)
                        data_attrs += f' data-{key}="{value_str}"'

                content = f"{icon} {text}" if icon else text
                html += f'<li><a class="dropdown-item{variant_class}" href="#"{click_attr}{data_attrs}>{content}</a></li>'

        html += "</ul></div>"
        return html

    def _render_tailwind(self) -> str:
        """Render Tailwind CSS dropdown"""
        variant_map = {
            "primary": "bg-blue-600 hover:bg-blue-700 text-white",
            "secondary": "bg-gray-600 hover:bg-gray-700 text-white",
            "success": "bg-green-600 hover:bg-green-700 text-white",
            "danger": "bg-red-600 hover:bg-red-700 text-white",
        }
        variant_class = variant_map.get(self.variant, variant_map["secondary"])

        size_map = {
            "sm": "px-3 py-1.5 text-sm",
            "md": "px-4 py-2 text-base",
            "lg": "px-6 py-3 text-lg",
        }
        size_class = size_map.get(self.size, size_map["md"])

        html = f'<div class="relative inline-block text-left" id="{self.component_id}" x-data="{{open: false}}">'

        # Button
        button_class = f"{size_class} {variant_class} font-medium rounded-md focus:outline-none focus:ring-2 focus:ring-offset-2"
        html += f'<button type="button" class="{button_class}" dj-click="open = !open">{self.label}'
        html += """<svg class="-mr-1 ml-2 h-5 w-5 inline" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
            <path fill-rule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clip-rule="evenodd"/>
        </svg></button>"""

        # Menu
        html += """<div x-show="open" @click.away="open = false" class="absolute right-0 z-10 mt-2 w-56 origin-top-right rounded-md bg-white shadow-lg ring-1 ring-black ring-opacity-5">
            <div class="py-1">"""

        for item in self.items:
            if item.get("divider"):
                html += '<div class="border-t border-gray-100"></div>'
            else:
                text = item.get("text", "")
                action = item.get("action", "")
                icon = item.get("icon", "")
                item_variant = item.get("variant", "")
                data = item.get("data", {})

                click_attr = (
                    f' dj-click="{action}; open = false"' if action else ' dj-click="open = false"'
                )
                variant_class = " text-red-600" if item_variant == "danger" else " text-gray-700"

                # Add data-* attributes for event parameters
                data_attrs = ""
                if data:
                    import json

                    for key, value in data.items():
                        if isinstance(value, (dict, list)):
                            value_str = json.dumps(value).replace('"', "&quot;")
                        else:
                            value_str = str(value)
                        data_attrs += f' data-{key}="{value_str}"'

                content = f"{icon} {text}" if icon else text
                html += f'<a href="#" class="block px-4 py-2 text-sm{variant_class} hover:bg-gray-100"{click_attr}{data_attrs}>{content}</a>'

        html += "</div></div></div>"
        return html

    def _render_plain(self) -> str:
        """Render plain HTML dropdown"""
        html = f'<div class="dropdown" id="{self.component_id}">'
        html += f'<button type="button" class="dropdown-toggle button button-{self.variant}">{self.label} ▼</button>'
        html += '<div class="dropdown-menu">'

        for item in self.items:
            if item.get("divider"):
                html += '<hr class="dropdown-divider">'
            else:
                text = item.get("text", "")
                action = item.get("action", "")
                icon = item.get("icon", "")
                data = item.get("data", {})

                click_attr = f' dj-click="{action}"' if action else ""

                # Add data-* attributes for event parameters
                data_attrs = ""
                if data:
                    import json

                    for key, value in data.items():
                        if isinstance(value, (dict, list)):
                            value_str = json.dumps(value).replace('"', "&quot;")
                        else:
                            value_str = str(value)
                        data_attrs += f' data-{key}="{value_str}"'

                content = f"{icon} {text}" if icon else text
                html += f'<a href="#" class="dropdown-item"{click_attr}{data_attrs}>{content}</a>'

        html += "</div></div>"
        return html
