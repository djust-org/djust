"""
Dropdown Component Demo - Stateless vs Stateful

Demonstrates both:
- Dropdown (stateless, high-performance)
- DropdownComponent (stateful, with lifecycle)
"""

from djust import LiveView
from djust.components.ui import (
from djust.decorators import event
    Dropdown,  # Stateless
    DropdownComponent,  # Stateful
    Alert,
    Card,
)


class DropdownDemo(LiveView):
    """
    Demo page showcasing both stateless and stateful dropdown components.
    """

    template_name = 'demos/dropdown_demo.html'

    def mount(self, request, **kwargs):
        """Initialize dropdown demo"""

        # State for tracking actions
        self.last_action = "No action yet"
        self.action_count = 0

        # ===== STATELESS DROPDOWNS (for display) =====

        # Basic dropdown
        self.basic_dropdown = Dropdown(
            label="Basic Menu",
            items=[
                {'label': 'Action', 'url': '#action'},
                {'label': 'Another action', 'url': '#another'},
                {'label': 'Something else', 'url': '#something'},
                {'divider': True},
                {'label': 'Separated link', 'url': '#separated'},
            ],
            variant="primary"
        )

        # Split button dropdown
        self.split_dropdown = Dropdown(
            label="Split Button",
            items=[
                {'label': 'Edit', 'url': '#edit'},
                {'label': 'Delete', 'url': '#delete'},
                {'label': 'Archive', 'url': '#archive'},
            ],
            variant="success",
            split=True
        )

        # All variants
        self.dropdown_variants = [
            Dropdown("Primary", [
                {'label': 'Item 1', 'url': '#1'},
                {'label': 'Item 2', 'url': '#2'},
            ], variant="primary", size="sm"),

            Dropdown("Secondary", [
                {'label': 'Item 1', 'url': '#1'},
                {'label': 'Item 2', 'url': '#2'},
            ], variant="secondary", size="sm"),

            Dropdown("Success", [
                {'label': 'Item 1', 'url': '#1'},
                {'label': 'Item 2', 'url': '#2'},
            ], variant="success", size="sm"),

            Dropdown("Danger", [
                {'label': 'Item 1', 'url': '#1'},
                {'label': 'Item 2', 'url': '#2'},
            ], variant="danger", size="sm"),

            Dropdown("Warning", [
                {'label': 'Item 1', 'url': '#1'},
                {'label': 'Item 2', 'url': '#2'},
            ], variant="warning", size="sm"),

            Dropdown("Info", [
                {'label': 'Item 1', 'url': '#1'},
                {'label': 'Item 2', 'url': '#2'},
            ], variant="info", size="sm"),

            Dropdown("Light", [
                {'label': 'Item 1', 'url': '#1'},
                {'label': 'Item 2', 'url': '#2'},
            ], variant="light", size="sm"),

            Dropdown("Dark", [
                {'label': 'Item 1', 'url': '#1'},
                {'label': 'Item 2', 'url': '#2'},
            ], variant="dark", size="sm"),
        ]

        # Different sizes
        self.size_small = Dropdown("Small", [
            {'label': 'Item 1', 'url': '#1'},
            {'label': 'Item 2', 'url': '#2'},
        ], size="sm")

        self.size_medium = Dropdown("Medium", [
            {'label': 'Item 1', 'url': '#1'},
            {'label': 'Item 2', 'url': '#2'},
        ], size="md")

        self.size_large = Dropdown("Large", [
            {'label': 'Item 1', 'url': '#1'},
            {'label': 'Item 2', 'url': '#2'},
        ], size="lg")

        # Different directions
        self.direction_down = Dropdown("Down", [
            {'label': 'Item 1', 'url': '#1'},
            {'label': 'Item 2', 'url': '#2'},
        ], direction="down")

        self.direction_up = Dropdown("Up", [
            {'label': 'Item 1', 'url': '#1'},
            {'label': 'Item 2', 'url': '#2'},
        ], direction="up")

        self.direction_start = Dropdown("Start", [
            {'label': 'Item 1', 'url': '#1'},
            {'label': 'Item 2', 'url': '#2'},
        ], direction="start")

        self.direction_end = Dropdown("End", [
            {'label': 'Item 1', 'url': '#1'},
            {'label': 'Item 2', 'url': '#2'},
        ], direction="end")

        # Disabled items
        self.disabled_dropdown = Dropdown(
            label="Menu with Disabled",
            items=[
                {'label': 'Active item', 'url': '#active'},
                {'label': 'Disabled item', 'url': '#disabled', 'disabled': True},
                {'divider': True},
                {'label': 'Another active', 'url': '#active2'},
                {'label': 'Another disabled', 'url': '#disabled2', 'disabled': True},
            ],
            variant="secondary"
        )

        # ===== STATEFUL DROPDOWN (interactive) =====

        # Interactive dropdown with event handlers
        self.actions_dropdown = DropdownComponent(
            label="Actions",
            variant="primary",
            items=[
                {'text': 'Edit', 'action': 'handle_edit', 'icon': '✏️'},
                {'text': 'Duplicate', 'action': 'handle_duplicate', 'icon': '📋'},
                {'text': 'Share', 'action': 'handle_share', 'icon': '🔗'},
                {'divider': True},
                {'text': 'Delete', 'action': 'handle_delete', 'icon': '🗑️', 'variant': 'danger'},
            ]
        )

        # Alert for showing component (using stateless Alert)
        self.status_alert = Alert(
            text=self.last_action,
            variant="info"
        )

    @event
    def handle_edit(self):
        """Handle edit action"""
        self.action_count += 1
        self.last_action = f"Edit clicked! (Action #{self.action_count})"
        self.status_alert = Alert(text=self.last_action, variant="info")

    @event
    def handle_duplicate(self):
        """Handle duplicate action"""
        self.action_count += 1
        self.last_action = f"Duplicate clicked! (Action #{self.action_count})"
        self.status_alert = Alert(text=self.last_action, variant="success")

    @event
    def handle_share(self):
        """Handle share action"""
        self.action_count += 1
        self.last_action = f"Share clicked! (Action #{self.action_count})"
        self.status_alert = Alert(text=self.last_action, variant="primary")

    @event
    def handle_delete(self):
        """Handle delete action"""
        self.action_count += 1
        self.last_action = f"Delete clicked! (Action #{self.action_count})"
        self.status_alert = Alert(text=self.last_action, variant="danger")

    def get_context_data(self, **kwargs):
        """Pass dropdown components to template"""
        context = super().get_context_data(**kwargs)
        context.update({
            'basic_dropdown': self.basic_dropdown,
            'split_dropdown': self.split_dropdown,
            'dropdown_variants': self.dropdown_variants,
            'size_small': self.size_small,
            'size_medium': self.size_medium,
            'size_large': self.size_large,
            'direction_down': self.direction_down,
            'direction_up': self.direction_up,
            'direction_start': self.direction_start,
            'direction_end': self.direction_end,
            'disabled_dropdown': self.disabled_dropdown,
            'actions_dropdown': self.actions_dropdown,
            'status_alert': self.status_alert,
            'last_action': self.last_action,
            'action_count': self.action_count,
        })
        return context
