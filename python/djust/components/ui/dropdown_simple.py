"""
Dropdown component for djust - stateless, high-performance.

Provides button-triggered dropdown menus for display purposes.
This is a stateless Component optimized for performance.
For interactive dropdowns with event handlers, use them in LiveView event handlers.
"""

from typing import List, Dict, Optional, Union
from ..base import Component


# Try to import Rust implementation (will be added later)
try:
    from djust._rust import RustDropdown

    _RUST_AVAILABLE = True
except (ImportError, AttributeError):
    _RUST_AVAILABLE = False
    RustDropdown = None


class Dropdown(Component):
    """
    Simple, stateless dropdown component with automatic Rust optimization.

    This component automatically uses pure Rust implementation if available,
    otherwise falls back to hybrid rendering with Rust template engine.

    Performance:
        - Pure Rust (if available): ~1μs per render
        - Python fallback: ~50-100μs per render (uses loops)

    Use Cases:
        - Display-only dropdown menus (rendered but @click handled by parent)
        - Static menu collections
        - Navigation dropdowns
        - High-frequency rendering

    Note: This is a stateless component for rendering. Event handlers are
    attached in the template using @click directives.

    Args:
        label: Button text
        items: List of menu item dicts with 'label', 'url', 'divider', 'disabled' keys
        variant: Color variant (primary, secondary, success, danger, warning, info, light, dark)
        size: Button size (sm, md, lg)
        split: Use split button style
        direction: Dropdown direction (down, up, start, end)
        id: Dropdown ID for JavaScript control (auto-generated if not provided)

    Examples:
        # Simple dropdown
        items = [
            {'label': 'Action', 'url': '#action'},
            {'label': 'Another action', 'url': '#another'},
            {'divider': True},
            {'label': 'Disabled', 'disabled': True},
        ]
        dropdown = Dropdown(label="Menu", items=items)
        html = dropdown.render()

        # Split button dropdown
        dropdown = Dropdown(
            label="Primary",
            items=items,
            variant="primary",
            split=True
        )

        # Dropup
        dropdown = Dropdown(
            label="Dropup",
            items=items,
            direction="up"
        )

        # Different sizes
        small = Dropdown("Small Menu", items=items, size="sm")
        large = Dropdown("Large Menu", items=items, size="lg")

        # All variants
        primary = Dropdown("Primary", items=items, variant="primary")
        secondary = Dropdown("Secondary", items=items, variant="secondary")
        success = Dropdown("Success", items=items, variant="success")
        danger = Dropdown("Danger", items=items, variant="danger")
    """

    # Link to Rust implementation if available
    _rust_impl_class = RustDropdown if _RUST_AVAILABLE else None

    # Note: Not using template because loops are needed for items
    # Using Python _render_custom() which is still fast (~50-100μs)

    def __init__(
        self,
        label: str,
        items: List[Dict[str, Union[str, bool]]],
        variant: str = "primary",
        size: str = "md",
        split: bool = False,
        direction: str = "down",
        id: Optional[str] = None,
    ):
        """
        Initialize dropdown component.

        Args:
            label: Button text
            items: List of menu item dicts
            variant: Color variant
            size: Button size (sm, md, lg)
            split: Use split button style
            direction: Dropdown direction (down, up, start, end)
            id: Dropdown ID (auto-generated if not provided)
        """
        super().__init__(
            label=label,
            items=items,
            variant=variant,
            size=size,
            split=split,
            direction=direction,
            id=id,
        )

        # Store for Python rendering
        self.label = label
        self.items = items
        self.variant = variant
        self.size = size
        self.split = split
        self.direction = direction

    def get_context_data(self):
        """Context for hybrid rendering (if Rust template engine supports loops in future)"""
        return {
            "label": self.label,
            "items": self.items,
            "variant": self.variant,
            "size": self.size,
            "split": self.split,
            "direction": self.direction,
            "id": self.id,
        }

    def _render_custom(self) -> str:
        """Custom Python rendering (fallback)"""
        from ...config import config

        framework = config.get("css_framework", "bootstrap5")

        if framework == "bootstrap5":
            return self._render_bootstrap()
        elif framework == "tailwind":
            return self._render_tailwind()
        else:
            return self._render_plain()

    def _render_bootstrap(self) -> str:
        """Render Bootstrap 5 dropdown"""
        size_map = {
            "sm": " btn-sm",
            "lg": " btn-lg",
            "md": "",
        }
        size_class = size_map.get(self.size, "")

        direction_map = {
            "down": "dropdown",
            "up": "dropup",
            "start": "dropstart",
            "end": "dropend",
        }
        direction_class = direction_map.get(self.direction, "dropdown")

        parts = [f'<div class="btn-group {direction_class}" id="{self.id}">']

        button_class = f"btn btn-{self.variant}{size_class}"

        if self.split:
            # Split button dropdown
            parts.append(f'    <button type="button" class="{button_class}">{self.label}</button>')
            parts.append(
                f'    <button type="button" class="{button_class} dropdown-toggle dropdown-toggle-split" data-bs-toggle="dropdown" aria-expanded="false">'
            )
            parts.append('        <span class="visually-hidden">Toggle Dropdown</span>')
            parts.append("    </button>")
        else:
            # Regular dropdown
            parts.append(
                f'    <button type="button" class="{button_class} dropdown-toggle" data-bs-toggle="dropdown" aria-expanded="false">'
            )
            parts.append(f"        {self.label}")
            parts.append("    </button>")

        parts.append('    <ul class="dropdown-menu">')

        # Render menu items
        for item in self.items:
            if item.get("divider"):
                parts.append('        <li><hr class="dropdown-divider"></li>')
            else:
                label = item.get("label", "")
                url = item.get("url", "#")
                disabled = item.get("disabled", False)

                disabled_class = " disabled" if disabled else ""
                disabled_attr = ' aria-disabled="true"' if disabled else ""

                parts.append(
                    f'        <li><a class="dropdown-item{disabled_class}" href="{url}"{disabled_attr}>{label}</a></li>'
                )

        parts.append("    </ul>")
        parts.append("</div>")

        return "\n".join(parts)

    def _render_tailwind(self) -> str:
        """Render Tailwind CSS dropdown"""
        variant_map = {
            "primary": "bg-blue-600 hover:bg-blue-700 text-white",
            "secondary": "bg-gray-600 hover:bg-gray-700 text-white",
            "success": "bg-green-600 hover:bg-green-700 text-white",
            "danger": "bg-red-600 hover:bg-red-700 text-white",
            "warning": "bg-yellow-500 hover:bg-yellow-600 text-white",
            "info": "bg-cyan-500 hover:bg-cyan-600 text-white",
            "light": "bg-gray-100 hover:bg-gray-200 text-gray-800",
            "dark": "bg-gray-800 hover:bg-gray-900 text-white",
        }
        variant_class = variant_map.get(self.variant, variant_map["primary"])

        size_map = {
            "sm": "px-3 py-1.5 text-sm",
            "md": "px-4 py-2 text-base",
            "lg": "px-6 py-3 text-lg",
        }
        size_class = size_map.get(self.size, size_map["md"])

        parts = [
            f'<div class="relative inline-block text-left" id="{self.id}" x-data="{{open: false}}">'
        ]

        # Button
        button_class = f"{size_class} {variant_class} font-medium rounded-md focus:outline-none focus:ring-2 focus:ring-offset-2"

        if self.split:
            # Split button
            parts.append('    <div class="inline-flex rounded-md shadow-sm">')
            parts.append(
                f'        <button type="button" class="{button_class} rounded-l-md">{self.label}</button>'
            )
            parts.append(
                f'        <button type="button" class="{button_class} rounded-r-md border-l border-white border-opacity-25" @click="open = !open">'
            )
            parts.append(
                '            <svg class="h-5 w-5" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">'
            )
            parts.append(
                '                <path fill-rule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clip-rule="evenodd"/>'
            )
            parts.append("            </svg>")
            parts.append("        </button>")
            parts.append("    </div>")
        else:
            # Regular button
            parts.append(f'    <button type="button" class="{button_class}" @click="open = !open">')
            parts.append(f"        {self.label}")
            parts.append(
                '        <svg class="-mr-1 ml-2 h-5 w-5 inline" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">'
            )
            parts.append(
                '            <path fill-rule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clip-rule="evenodd"/>'
            )
            parts.append("        </svg>")
            parts.append("    </button>")

        # Menu
        parts.append(
            '    <div x-show="open" @click.away="open = false" class="absolute right-0 z-10 mt-2 w-56 origin-top-right rounded-md bg-white shadow-lg ring-1 ring-black ring-opacity-5">'
        )
        parts.append('        <div class="py-1">')

        for item in self.items:
            if item.get("divider"):
                parts.append('            <div class="border-t border-gray-100"></div>')
            else:
                label = item.get("label", "")
                url = item.get("url", "#")
                disabled = item.get("disabled", False)

                disabled_class = (
                    " opacity-50 cursor-not-allowed" if disabled else " hover:bg-gray-100"
                )
                click_attr = ' @click="open = false"' if not disabled else ""

                parts.append(
                    f'            <a href="{url}" class="block px-4 py-2 text-sm text-gray-700{disabled_class}"{click_attr}>{label}</a>'
                )

        parts.append("        </div>")
        parts.append("    </div>")
        parts.append("</div>")

        return "\n".join(parts)

    def _render_plain(self) -> str:
        """Render plain HTML dropdown"""
        size_class = f" button-{self.size}" if self.size != "md" else ""

        parts = [f'<div class="dropdown" id="{self.id}">']

        if self.split:
            parts.append(
                f'    <button type="button" class="button button-{self.variant}{size_class}">{self.label}</button>'
            )
            parts.append(
                f'    <button type="button" class="dropdown-toggle button button-{self.variant}{size_class}">▼</button>'
            )
        else:
            parts.append(
                f'    <button type="button" class="dropdown-toggle button button-{self.variant}{size_class}">{self.label} ▼</button>'
            )

        parts.append('    <div class="dropdown-menu">')

        for item in self.items:
            if item.get("divider"):
                parts.append('        <hr class="dropdown-divider">')
            else:
                label = item.get("label", "")
                url = item.get("url", "#")
                disabled = item.get("disabled", False)

                disabled_class = " disabled" if disabled else ""

                parts.append(
                    f'        <a href="{url}" class="dropdown-item{disabled_class}">{label}</a>'
                )

        parts.append("    </div>")
        parts.append("</div>")

        return "\n".join(parts)
