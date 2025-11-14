"""
Simple ButtonGroup component for djust - stateless, high-performance.

Groups multiple buttons together with Bootstrap 5 button group styling.
This is a stateless Component optimized for performance.
"""

from typing import List, Dict
from ..base import Component


# Try to import Rust implementation (will be added later)
try:
    from djust._rust import RustButtonGroup

    _RUST_AVAILABLE = True
except (ImportError, AttributeError):
    _RUST_AVAILABLE = False
    RustButtonGroup = None


class ButtonGroup(Component):
    """
    Simple, stateless button group component with automatic Rust optimization.

    This component automatically uses pure Rust implementation if available,
    otherwise falls back to hybrid rendering with Rust template engine.

    Performance:
        - Pure Rust (if available): ~1μs per render
        - Hybrid template: ~5-10μs per render
        - Pure Python fallback: ~50-100μs per render

    Use Cases:
        - Display-only button groups (rendered but @click handled by parent)
        - Toggle button groups
        - Radio button groups
        - Toolbar with multiple button groups
        - High-frequency rendering

    Note: This is a stateless component for rendering. Event handlers are
    attached in the template using @click directives on individual buttons.

    Args:
        buttons: List of button dicts with 'label', 'variant', 'active', 'disabled' keys
        size: Button size (sm, md, lg)
        vertical: Whether to stack buttons vertically
        role: ARIA role (group for button group, toolbar for toolbar)

    Examples:
        # Simple usage
        buttons = [
            {'label': 'Left', 'variant': 'primary'},
            {'label': 'Middle', 'variant': 'primary'},
            {'label': 'Right', 'variant': 'primary', 'active': True},
        ]
        group = ButtonGroup(buttons=buttons)
        html = group.render()

        # In template with event handlers
        {{ button_group.render|safe }}

        # Vertical group
        group = ButtonGroup(buttons=buttons, vertical=True)

        # Small size
        group = ButtonGroup(buttons=buttons, size='sm')

        # Large size
        group = ButtonGroup(buttons=buttons, size='lg')

        # Toolbar (multiple button groups)
        group1 = [
            {'label': 'Bold', 'variant': 'outline-primary'},
            {'label': 'Italic', 'variant': 'outline-primary'},
        ]
        group2 = [
            {'label': 'Left', 'variant': 'outline-secondary'},
            {'label': 'Center', 'variant': 'outline-secondary'},
            {'label': 'Right', 'variant': 'outline-secondary'},
        ]
        toolbar = ButtonGroup(buttons=group1, role='toolbar')

        # Disabled button in group
        buttons = [
            {'label': 'Active', 'variant': 'primary'},
            {'label': 'Disabled', 'variant': 'primary', 'disabled': True},
        ]
        group = ButtonGroup(buttons=buttons)
    """

    # Link to Rust implementation if available
    _rust_impl_class = RustButtonGroup if _RUST_AVAILABLE else None

    # Fallback: Hybrid rendering with template
    # Note: Avoiding elif due to Rust template engine bug - using separate if blocks instead
    template = """<div class="btn-group{% if vertical %}-vertical{% endif %}{% if size == "sm" %} btn-group-sm{% endif %}{% if size == "lg" %} btn-group-lg{% endif %}" role="{{ role }}" aria-label="Button group">
{% for button in buttons %}<button type="button" class="btn btn-{{ button.variant }}{% if button.active %} active{% endif %}"{% if button.disabled %} disabled{% endif %}>{{ button.label }}</button>
{% endfor %}</div>"""

    def __init__(
        self, buttons: List[Dict], size: str = "md", vertical: bool = False, role: str = "group"
    ):
        """
        Initialize button group component.

        Args:
            buttons: List of button dicts with 'label', 'variant', 'active', 'disabled' keys
            size: Button size (sm, md, lg)
            vertical: Whether to stack buttons vertically
            role: ARIA role (group for button group, toolbar for toolbar)
        """
        super().__init__(buttons=buttons, size=size, vertical=vertical, role=role)

        # Store for hybrid rendering
        self.buttons = buttons
        self.size = size
        self.vertical = vertical
        self.role = role

    def get_context_data(self):
        """Context for hybrid rendering"""
        return {
            "buttons": self.buttons,
            "size": self.size,
            "vertical": self.vertical,
            "role": self.role,
        }

    def _render_custom(self) -> str:
        """Custom Python rendering (fallback)"""
        from djust.config import config

        framework = config.get("css_framework", "bootstrap5")

        if framework == "bootstrap5":
            return self._render_bootstrap()
        elif framework == "tailwind":
            return self._render_tailwind()
        else:
            return self._render_plain()

    def _render_bootstrap(self) -> str:
        """Render Bootstrap 5 button group"""
        # Size classes
        size_map = {
            "sm": " btn-group-sm",
            "lg": " btn-group-lg",
            "md": "",
        }
        size_class = size_map.get(self.size, "")

        # Vertical or horizontal
        group_class = "btn-group-vertical" if self.vertical else "btn-group"

        # Build button HTML
        button_html = []
        for button in self.buttons:
            variant = button.get("variant", "primary")
            label = button.get("label", "")
            active = button.get("active", False)
            disabled = button.get("disabled", False)

            active_class = " active" if active else ""
            disabled_attr = " disabled" if disabled else ""

            button_html.append(
                f'<button type="button" class="btn btn-{variant}{active_class}"{disabled_attr}>{label}</button>'
            )

        buttons_str = "\n".join(button_html)

        return f'<div class="{group_class}{size_class}" role="{self.role}" aria-label="Button group">\n{buttons_str}\n</div>'

    def _render_tailwind(self) -> str:
        """Render Tailwind CSS button group"""
        # Size mapping
        size_map = {
            "sm": "px-3 py-1.5 text-sm",
            "md": "px-4 py-2 text-base",
            "lg": "px-6 py-3 text-lg",
        }
        size_classes = size_map.get(self.size, size_map["md"])

        # Variant mapping
        variant_map = {
            "primary": "bg-blue-600 hover:bg-blue-700 text-white",
            "secondary": "bg-gray-600 hover:bg-gray-700 text-white",
            "success": "bg-green-600 hover:bg-green-700 text-white",
            "danger": "bg-red-600 hover:bg-red-700 text-white",
            "warning": "bg-yellow-500 hover:bg-yellow-600 text-white",
            "info": "bg-cyan-500 hover:bg-cyan-600 text-white",
            "light": "bg-gray-100 hover:bg-gray-200 text-gray-800",
            "dark": "bg-gray-800 hover:bg-gray-900 text-white",
            "outline-primary": "border border-blue-600 text-blue-600 hover:bg-blue-50",
            "outline-secondary": "border border-gray-600 text-gray-600 hover:bg-gray-50",
        }

        # Flex direction
        flex_dir = "flex-col" if self.vertical else "flex-row"

        # Build button HTML
        button_html = []
        for i, button in enumerate(self.buttons):
            variant = button.get("variant", "primary")
            label = button.get("label", "")
            active = button.get("active", False)
            disabled = button.get("disabled", False)

            variant_classes = variant_map.get(variant, variant_map["primary"])
            active_class = " ring-2 ring-offset-2 ring-blue-500" if active else ""
            disabled_classes = " opacity-50 cursor-not-allowed" if disabled else ""
            disabled_attr = " disabled" if disabled else ""

            # Rounded corners only on first and last buttons
            if self.vertical:
                if i == 0:
                    rounded = "rounded-t-lg"
                elif i == len(self.buttons) - 1:
                    rounded = "rounded-b-lg"
                else:
                    rounded = "rounded-none"
            else:
                if i == 0:
                    rounded = "rounded-l-lg"
                elif i == len(self.buttons) - 1:
                    rounded = "rounded-r-lg"
                else:
                    rounded = "rounded-none"

            button_html.append(
                f'<button type="button" class="font-medium {size_classes} {rounded} {variant_classes}{active_class}{disabled_classes}"{disabled_attr}>{label}</button>'
            )

        buttons_str = "\n".join(button_html)

        return f'<div class="inline-flex {flex_dir}" role="{self.role}" aria-label="Button group">\n{buttons_str}\n</div>'

    def _render_plain(self) -> str:
        """Render plain HTML button group"""
        # Size class
        size_class = f" btn-group-{self.size}" if self.size != "md" else ""

        # Vertical or horizontal
        group_class = "btn-group-vertical" if self.vertical else "btn-group"

        # Build button HTML
        button_html = []
        for button in self.buttons:
            variant = button.get("variant", "primary")
            label = button.get("label", "")
            active = button.get("active", False)
            disabled = button.get("disabled", False)

            active_class = " active" if active else ""
            disabled_attr = " disabled" if disabled else ""

            button_html.append(
                f'<button type="button" class="button button-{variant}{active_class}"{disabled_attr}>{label}</button>'
            )

        buttons_str = "\n".join(button_html)

        return f'<div class="{group_class}{size_class}" role="{self.role}" aria-label="Button group">\n{buttons_str}\n</div>'
