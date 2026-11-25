"""
Simple ButtonGroup component for djust - stateless, high-performance.

Groups multiple buttons together with Bootstrap 5 button group styling.
This is a stateless Component optimized for performance.
"""

from typing import Any, Dict, List
from ..base import Component


class ButtonGroup(Component):
    """
    Simple, stateless button group component.

    There is no native Rust implementation; it renders through its template.

    Performance:
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

    def get_context_data(self) -> dict[str, Any]:
        """Context for hybrid rendering"""
        return {
            "buttons": self.buttons,
            "size": self.size,
            "vertical": self.vertical,
            "role": self.role,
        }
