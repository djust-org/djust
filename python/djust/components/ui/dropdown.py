"""
Dropdown component for djust.

Provides dropdown menus with items and actions.
"""

import re
from djust.components._alias_deprecation import warn_alias
from typing import Dict, Any, Optional
from dataclasses import dataclass
from ..base import LiveComponent
from django.utils.html import conditional_escape
from django.utils.safestring import SafeString, mark_safe

from djust.components.utils import attr_text, data_url_attr


# ``data-*`` attribute names: letters, digits, "-" and "_" only.
_DATA_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


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
        url: Optional link target. Checked and escaped like any URL (a non-navigation
            scheme such as ``javascript:`` becomes ``#``); without one the item links to ``#``.
        disabled: Draw the item disabled (no click handler, ``aria-disabled="true"``)
    """

    text: str = ""
    action: str = ""
    data: Optional[Dict[str, Any]] = None
    icon: str = ""
    variant: str = ""
    divider: bool = False
    url: str = ""
    disabled: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format for backward compatibility"""
        if self.divider:
            return {"divider": True}

        result: Dict[str, Any] = {
            "text": self.text,
            "action": self.action,
        }
        if self.data:
            result["data"] = self.data
        if self.icon:
            result["icon"] = self.icon
        if self.variant:
            result["variant"] = self.variant
        if self.url:
            result["url"] = self.url
        if self.disabled:
            result["disabled"] = True
        return result


def _item_href(item: Dict[str, Any]) -> str:
    """An item's ``href``: its ``url`` checked and escaped (#4002), else ``#``."""
    url = item.get("url")
    return data_url_attr(url) if url else "#"


#: Control characters and spaces, removed before a value's scheme is read: a browser (and a
#: legacy engine) reads ``java<TAB>script:`` as ``javascript:``.
_SCHEME_NOISE = re.compile(r"[\x00-\x20]+")
#: The schemes that run script or load a document: a ``data`` value that starts with one is
#: written as ``#``, whatever its key.
_SCRIPT_SCHEMES = ("javascript:", "vbscript:", "data:")


def _data_value(value: Any) -> str:
    """One ``data`` value as attribute text: escaped, and ``#`` for a script scheme.

    ``dj-value-*`` attributes are event parameters the browser never follows, so any other
    text (``ns:del``, an id, a relative path) is kept as written (#4002 review). The #3898
    sweep still reads a URL-named one (``dj-value-link``) as one a script could follow, and
    the script schemes are all it can do harm with.
    """
    if _SCHEME_NOISE.sub("", str(value)).lower().startswith(_SCRIPT_SCHEMES):
        return "#"
    return attr_text(value)


def _data_attrs(data: Optional[Dict[str, Any]]) -> str:
    """An item's ``data`` as ``dj-value-*`` attributes (#4186): a dict or list value as JSON,
    every value through :func:`_data_value`."""
    if not data:
        return ""
    import json

    out = ""
    for key, value in data.items():
        if not _DATA_KEY_RE.match(str(key)):
            continue
        text = json.dumps(value) if isinstance(value, (dict, list)) else value
        out += f' dj-value-{key}="{_data_value(text)}"'
    return out


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

    def mount(self, **kwargs: Any) -> None:
        """Initialize dropdown state"""
        self.label = kwargs.get("label", "Dropdown")
        self.variant = kwargs.get("variant", "secondary")
        self.size = kwargs.get("size", "md")
        self.split = kwargs.get("split", False)  # Split dropdown button
        self.direction = kwargs.get("direction", "down")  # down, up, start, end

        # Dicts are deprecated to 3.0 (#4207): pass DropdownItem objects
        items = kwargs.get("items", [])
        self.items = []
        for item in items:
            if isinstance(item, DropdownItem):
                self.items.append(item.to_dict())
            else:
                warn_alias("Dropdown", "dict items", "DropdownItem objects")
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

    def render(self) -> SafeString:
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

        html = f'<div class="btn-group {direction_class}" id="{attr_text(self.component_id)}">'

        button_class = f"btn btn-{attr_text(self.variant)} {size_class}".strip()

        if self.split:
            # Split button dropdown
            html += f'<button type="button" class="{button_class}">{conditional_escape(self.label)}</button>'
            html += f'<button type="button" class="{button_class} dropdown-toggle dropdown-toggle-split" data-bs-toggle="dropdown" aria-expanded="false">'
            html += '<span class="visually-hidden">Toggle Dropdown</span></button>'
        else:
            # Regular dropdown
            html += f'<button type="button" class="{button_class} dropdown-toggle" data-bs-toggle="dropdown" aria-expanded="false">{conditional_escape(self.label)}</button>'

        html += '<ul class="dropdown-menu">'

        for item in self.items:
            if item.get("divider"):
                html += '<li><hr class="dropdown-divider"></li>'
            else:
                text = conditional_escape(item.get("text", ""))
                action = attr_text(item.get("action", ""))
                icon = conditional_escape(item.get("icon", ""))
                item_variant = attr_text(item.get("variant", ""))
                data = item.get("data", {})

                disabled = bool(item.get("disabled"))
                click_attr = f' dj-click="{action}"' if action and not disabled else ""
                variant_class = f" text-{item_variant}" if item_variant else ""
                if disabled:
                    variant_class += " disabled"

                # dj-value-* attributes for event parameters (#4186)
                data_attrs = _data_attrs(data)

                content = f"{icon} {text}" if icon else text
                disabled_attr = ' aria-disabled="true"' if disabled else ""
                html += f'<li><a class="dropdown-item{variant_class}" href="{_item_href(item)}"{disabled_attr}{click_attr}{data_attrs}>{content}</a></li>'

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

        html = f'<div class="relative inline-block text-left" id="{attr_text(self.component_id)}" x-data="{{open: false}}">'

        # Button
        button_class = f"{size_class} {variant_class} font-medium rounded-md focus:outline-none focus:ring-2 focus:ring-offset-2"
        html += f'<button type="button" class="{button_class}" dj-click="open = !open">{conditional_escape(self.label)}'
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
                text = conditional_escape(item.get("text", ""))
                action = attr_text(item.get("action", ""))
                icon = conditional_escape(item.get("icon", ""))
                item_variant = conditional_escape(item.get("variant", ""))
                data = item.get("data", {})

                disabled = bool(item.get("disabled"))
                if disabled:
                    click_attr = ""
                elif action:
                    click_attr = f' dj-click="{action}; open = false"'
                else:
                    click_attr = ' dj-click="open = false"'
                variant_class = " text-red-600" if item_variant == "danger" else " text-gray-700"
                state_class = " opacity-50 cursor-not-allowed" if disabled else " hover:bg-gray-100"
                disabled_attr = ' aria-disabled="true"' if disabled else ""

                # dj-value-* attributes for event parameters (#4186)
                data_attrs = _data_attrs(data)

                content = f"{icon} {text}" if icon else text
                html += f'<a href="{_item_href(item)}" class="block px-4 py-2 text-sm{variant_class}{state_class}"{disabled_attr}{click_attr}{data_attrs}>{content}</a>'

        html += "</div></div></div>"
        return html

    def _render_plain(self) -> str:
        """Render plain HTML dropdown"""
        html = f'<div class="dropdown" id="{attr_text(self.component_id)}">'
        html += f'<button type="button" class="dropdown-toggle button button-{attr_text(self.variant)}">{conditional_escape(self.label)} ▼</button>'
        html += '<div class="dropdown-menu">'

        for item in self.items:
            if item.get("divider"):
                html += '<hr class="dropdown-divider">'
            else:
                text = conditional_escape(item.get("text", ""))
                action = attr_text(item.get("action", ""))
                icon = conditional_escape(item.get("icon", ""))
                data = item.get("data", {})

                disabled = bool(item.get("disabled"))
                click_attr = f' dj-click="{action}"' if action and not disabled else ""
                item_class = "dropdown-item disabled" if disabled else "dropdown-item"
                disabled_attr = ' aria-disabled="true"' if disabled else ""

                # dj-value-* attributes for event parameters (#4186)
                data_attrs = _data_attrs(data)

                content = f"{icon} {text}" if icon else text
                html += f'<a href="{_item_href(item)}" class="{item_class}"{disabled_attr}{click_attr}{data_attrs}>{content}</a>'

        html += "</div></div>"
        return html
