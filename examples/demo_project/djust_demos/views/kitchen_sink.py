"""
Kitchen Sink Demo View

Showcases all available Django Rust Live components with interactive examples.
"""

from django.http import HttpRequest
from demo_app.views.navbar_example import BaseViewWithNavbar
from djust.decorators import event
from djust.components import (
    # UI Components
    AlertComponent,
    BadgeComponent,
    ButtonComponent,
    CardComponent,
    DropdownComponent,
    ModalComponent,
    ProgressComponent,
    SpinnerComponent,
    # Layout Components
    TabsComponent,
    # Data Components
    TableComponent,
    PaginationComponent,
)


class KitchenSinkView(BaseViewWithNavbar):
    """
    Comprehensive component showcase with interactive playground.

    Displays all available components organized in categories:
    - UI Components (Button, Badge, Dropdown, Alert, Card, Modal, Progress, Spinner)
    - Layout Components (Tabs)
    - Data Components (Table, Pagination)

    Uses modern template with shared theme system:
    - kitchen_sink.html with theme.css, components.css, utilities.css
    - Dark/light mode support
    - NavbarComponent for navigation
    """

    template_name = "kitchen_sink.html"

    def mount(self, request: HttpRequest):
        """Initialize all components with example configurations"""

        # === UI COMPONENTS ===

        # Buttons
        self.button_primary = ButtonComponent(
            label="Primary Button",
            variant="primary",
            size="md",
            on_click="handle_button_click"
        )

        self.button_secondary = ButtonComponent(
            label="Secondary",
            variant="secondary",
            size="sm"
        )

        self.button_success = ButtonComponent(
            label="Success Button",
            variant="success",
            size="lg",
            icon="✓"
        )

        self.button_danger = ButtonComponent(
            label="Danger",
            variant="danger",
            outline=True
        )

        # Badges
        self.badge_primary = BadgeComponent(text="Primary", variant="primary")
        self.badge_success = BadgeComponent(text="Active", variant="success", pill=True)
        self.badge_warning = BadgeComponent(text="99+", variant="warning", pill=True)
        self.badge_dismissible = BadgeComponent(
            text="Python",
            variant="info",
            dismissible=True,
            on_dismiss="dismiss_tag"
        )

        # Dropdowns
        self.dropdown_actions = DropdownComponent(
            label="Actions",
            variant="primary",
            items=[
                {'text': 'Edit', 'action': 'edit_item', 'icon': '✏️'},
                {'text': 'Delete', 'action': 'delete_item', 'icon': '🗑️', 'variant': 'danger'},
                {'divider': True},
                {'text': 'Archive', 'action': 'archive_item'},
            ]
        )

        self.dropdown_split = DropdownComponent(
            label="Split Dropdown",
            variant="success",
            split=True,
            items=[
                {'text': 'Action 1', 'action': 'action1'},
                {'text': 'Action 2', 'action': 'action2'},
            ]
        )

        # Alerts
        self.alert_success = AlertComponent(
            message="Operation completed successfully!",
            type="success",
            dismissible=True
        )

        self.alert_info = AlertComponent(
            message="Here's some helpful information.",
            type="info",
            icon=True
        )

        self.alert_warning = AlertComponent(
            message="Warning: Please review your changes.",
            type="warning",
            dismissible=True
        )

        # Cards
        self.card_basic = CardComponent(
            title="Basic Card",
            body="This is a simple card component with a title and body text.",
            variant="default"
        )

        self.card_with_footer = CardComponent(
            title="Featured Card",
            body="This card has a footer with additional information.",
            footer="Last updated 3 mins ago",
            variant="primary"
        )

        # Modal
        self.modal_example = ModalComponent(
            title="Example Modal",
            body="This is a modal dialog with customizable content.",
            show=False,
            size="lg"
        )

        # Progress
        self.progress_simple = ProgressComponent(
            value=45,
            variant="primary",
            show_label=True
        )

        self.progress_striped = ProgressComponent(
            value=65,
            variant="success",
            striped=True,
            animated=True,
            show_label=True
        )

        self.progress_custom = ProgressComponent(
            value=30,
            variant="warning",
            custom_label="Processing...",
            striped=True
        )

        # Spinner
        self.spinner_border = SpinnerComponent(
            variant="primary",
            size="md",
            label="Loading..."
        )

        self.spinner_grow = SpinnerComponent(
            variant="success",
            size="sm",
            type="grow"
        )

        # === LAYOUT COMPONENTS ===

        # Tabs
        self.tabs_example = TabsComponent(
            tabs=[
                {
                    'id': 'home',
                    'label': 'Home',
                    'content': '<p>Welcome to the Home tab!</p><p>This content is loaded immediately.</p>'
                },
                {
                    'id': 'profile',
                    'label': 'Profile',
                    'content': '<p>User profile information goes here.</p>'
                },
                {
                    'id': 'messages',
                    'label': 'Messages',
                    'content': '<p>Your messages appear here.</p>',
                    'badge': '5'
                },
                {
                    'id': 'settings',
                    'label': 'Settings',
                    'content': '<p>Configure your settings.</p>',
                    'disabled': True
                },
            ],
            active='home',
            variant='tabs'
        )

        # === DATA COMPONENTS ===

        # Table with sample data
        self.table_users = TableComponent(
            columns=[
                {'key': 'id', 'label': 'ID', 'sortable': True},
                {'key': 'name', 'label': 'Name', 'sortable': True},
                {'key': 'email', 'label': 'Email', 'sortable': True},
                {'key': 'role', 'label': 'Role', 'badge': True},
                {'key': 'status', 'label': 'Status', 'badge': True},
            ],
            rows=[
                {'id': 1, 'name': 'Alice Johnson', 'email': 'alice@example.com', 'role': 'Admin', 'status': 'active'},
                {'id': 2, 'name': 'Bob Smith', 'email': 'bob@example.com', 'role': 'User', 'status': 'active'},
                {'id': 3, 'name': 'Charlie Brown', 'email': 'charlie@example.com', 'role': 'User', 'status': 'inactive'},
                {'id': 4, 'name': 'Diana Prince', 'email': 'diana@example.com', 'role': 'Moderator', 'status': 'active'},
                {'id': 5, 'name': 'Eve Wilson', 'email': 'eve@example.com', 'role': 'User', 'status': 'active'},
            ],
            striped=True,
            hoverable=True,
            bordered=True
        )

        # Pagination
        self.pagination_basic = PaginationComponent(
            current_page=3,
            total_pages=10,
            show_page_info=True,
            alignment='center'
        )

        self.pagination_large = PaginationComponent(
            current_page=1,
            total_items=250,
            items_per_page=25,
            size='lg',
            on_page_change='handle_page_change'
        )

        # Component interaction state
        self.button_click_count = 0
        self.modal_open_count = 0
        self.current_progress = 45

    @event
    def handle_button_click(self):
        """Handle primary button click"""
        self.button_click_count += 1
        self.alert_success.set_message(f"Button clicked {self.button_click_count} time(s)!")
        self.alert_success.show()

    @event
    def edit_item(self):
        """Handle edit action"""
        self.alert_info.set_message("Edit action triggered!")
        self.alert_info.show()

    @event
    def delete_item(self):
        """Handle delete action"""
        self.alert_warning.set_message("Delete action triggered!")
        self.alert_warning.show()

    @event
    def archive_item(self):
        """Handle archive action"""
        self.alert_info.set_message("Archive action triggered!")
        self.alert_info.show()

    @event
    def dismiss_tag(self):
        """Handle tag dismissal"""
        self.badge_dismissible.dismiss()

    @event
    def show_modal(self):
        """Show the example modal"""
        self.modal_open_count += 1
        self.modal_example.set_body(f"This modal has been opened {self.modal_open_count} time(s).")
        self.modal_example.show()

    @event
    def close_modal(self):
        """Close the example modal"""
        self.modal_example.hide()

    @event
    def increment_progress(self):
        """Increment progress bar"""
        if self.current_progress < 100:
            self.current_progress += 10
            self.progress_simple.set_value(self.current_progress)
            self.progress_striped.set_value(min(self.current_progress + 20, 100))

    @event
    def reset_progress(self):
        """Reset progress bars"""
        self.current_progress = 0
        self.progress_simple.reset()
        self.progress_striped.reset()

    @event
    def handle_page_change(self, page: int):
        """Handle pagination page change"""
        self.alert_info.set_message(f"Navigated to page {page}")
        self.alert_info.show()

    @event
    def dismiss(self, component_id: str = None, **kwargs):
        """Handle component dismiss/hide - works for alerts, modals, etc."""
        # Get the component by its attribute name (which is now used as component_id)
        if component_id and hasattr(self, component_id):
            component = getattr(self, component_id)
            # Try dismiss() first (for alerts), then hide() (for modals)
            if hasattr(component, 'dismiss'):
                component.dismiss()
            elif hasattr(component, 'hide'):
                component.hide()

    @event
    def activate_tab(self, tab: str = None):
        """Handle tab activation"""
        if tab:
            self.tabs_example.activate_tab(tab)
