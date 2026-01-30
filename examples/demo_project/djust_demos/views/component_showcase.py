"""
Comprehensive Component Showcase - All 26 djust components in one interactive page.
"""

from djust import LiveView
from djust.decorators import event_handler
from djust.components.ui import (
    # Display Components
    Alert, Avatar, Badge, Button, Card, Spinner, Progress, Tooltip, Toast, Divider, Icon,
    # Form Components
    Input, Select, Checkbox, TextArea, Radio, Switch, Range,
    # Container Components
    Modal, Tabs, Accordion, Offcanvas,
    # Navigation Components
    Breadcrumb, Pagination, Dropdown, NavBar,
    # Layout Components
    ButtonGroup, ListGroup,
    # Data Components
    Table,
)


class ComponentShowcaseView(LiveView):
    """Interactive showcase of all djust components."""

    template_name = 'demos/component_showcase.html'

    def mount(self, request):
        """Initialize all components for the showcase."""
        # Current active category (for filtering)
        self.active_category = request.GET.get('category', 'all')

        # Search query
        self.search_query = ""

        # Interactive states (must be set before initializing components)
        # Only initialize if not already set (i.e., not restored from session in HTTP mode)
        if not hasattr(self, 'counter'):
            self.counter = 0
        if not hasattr(self, 'switch_enabled'):
            self.switch_enabled = True
        if not hasattr(self, 'slider_value'):
            self.slider_value = 50
        if not hasattr(self, 'selected_radio'):
            self.selected_radio = "medium"

        # Initialize all components
        self._init_display_components()
        self._init_form_components()
        self._init_container_components()
        self._init_navigation_components()
        self._init_layout_components()
        self._init_data_components()

    def _init_display_components(self):
        """Initialize display components."""
        self.alert_success = Alert("Operation successful!", variant="success", dismissable=True)
        self.alert_warning = Alert("Warning: Please review your input", variant="warning")
        self.alert_danger = Alert("Error: Something went wrong", variant="danger")

        self.avatar_image = Avatar(
            src="https://ui-avatars.com/api/?name=John+Doe&size=128",
            alt="John Doe",
            size="lg",
            status="online"
        )
        self.avatar_initials = Avatar(initials="JD", size="md", shape="circle")

        self.badge_primary = Badge("New", variant="primary")
        self.badge_success = Badge("Success", variant="success")
        self.badge_danger = Badge("5", variant="danger")

        self.button_primary = Button("Primary", variant="primary")
        self.button_outline = Button("Outline", variant="outline-secondary")
        self.button_lg = Button("Large", variant="success", size="lg")

        self.card_simple = Card(
            header="Simple Card",
            body="This is a basic card component with header and body content.",
            footer="Card Footer"
        )

        self.spinner_border = Spinner(variant="primary", size="md")
        self.spinner_grow = Spinner(variant="success", size="sm", animation="grow")

        self.progress_75 = Progress(value=75, variant="success", show_label=True)
        self.progress_animated = Progress(
            value=40,
            variant="info",
            striped=True,
            animated=True,
            show_label=True
        )

        self.tooltip_example = Tooltip("Hover me", text="This is a helpful tooltip!")

        self.toast_success = Toast(
            title="Success",
            message="Your changes have been saved!",
            variant="success",
            show_icon=True
        )

        self.divider_simple = Divider()
        self.divider_text = Divider(text="OR", margin="lg")

        self.icon_star = Icon(name="star-fill", library="bootstrap", size="lg", color="warning")
        self.icon_check = Icon(name="check-circle", library="bootstrap", color="success")

    def _init_form_components(self):
        """Initialize form components."""
        self.input_text = Input(
            name="username",
            label="Username",
            placeholder="Enter your username",
            required=True
        )

        self.input_email = Input(
            name="email",
            label="Email Address",
            type="email",
            placeholder="you@example.com",
            help_text="We'll never share your email"
        )

        self.select_country = Select(
            name="country",
            label="Country",
            options=[
                {'value': 'us', 'label': 'United States'},
                {'value': 'uk', 'label': 'United Kingdom'},
                {'value': 'ca', 'label': 'Canada'},
            ],
            value="us"
        )

        self.checkbox_terms = Checkbox(
            name="terms",
            label="I agree to the terms and conditions"
        )

        self.checkbox_newsletter = Checkbox(
            name="newsletter",
            label="Subscribe to newsletter",
            checked=True
        )

        self.textarea_bio = TextArea(
            name="bio",
            label="Biography",
            placeholder="Tell us about yourself...",
            rows=4
        )

        self.radio_size = Radio(
            name="size",
            label="Select Size",
            options=[
                {'value': 'small', 'label': 'Small'},
                {'value': 'medium', 'label': 'Medium'},
                {'value': 'large', 'label': 'Large'},
            ],
            value=self.selected_radio
        )

        self.switch_notifications = Switch(
            name="notifications",
            label="Enable notifications",
            checked=self.switch_enabled
        )

        self.range_volume = Range(
            name="volume",
            label="Volume",
            value=self.slider_value,
            min_value=0,
            max_value=100,
            show_value=True
        )

    def _init_container_components(self):
        """Initialize container components."""
        self.modal_example = Modal(
            body="Are you sure you want to continue?",
            id="exampleModal",
            title="Confirm Action",
            footer='<button class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button><button class="btn btn-primary">Confirm</button>'
        )

        self.tabs_example = Tabs(tabs=[
            {'title': 'Home', 'content': '<p>Welcome to the home tab!</p>'},
            {'title': 'Profile', 'content': '<p>Your profile information.</p>'},
            {'title': 'Contact', 'content': '<p>Contact us here.</p>'},
        ])

        self.accordion_example = Accordion(items=[
            {'title': 'Section 1', 'content': '<p>Content for section 1</p>'},
            {'title': 'Section 2', 'content': '<p>Content for section 2</p>'},
            {'title': 'Section 3', 'content': '<p>Content for section 3</p>'},
        ])

        self.offcanvas_example = Offcanvas(
            title="Offcanvas Menu",
            body="<p>This is the offcanvas content.</p>",
            id="offcanvasExample",
            placement="start"
        )

    def _init_navigation_components(self):
        """Initialize navigation components."""
        self.breadcrumb_example = Breadcrumb(items=[
            {'label': 'Home', 'url': '/'},
            {'label': 'Components', 'url': '/components'},
            {'label': 'Showcase', 'url': None},  # Current page
        ])

        self.pagination_example = Pagination(
            current_page=5,
            total_pages=10,
            max_visible_pages=5
        )

        self.dropdown_example = Dropdown(
            label="Actions",
            items=[
                {'label': 'Edit', 'url': '#edit'},
                {'label': 'Delete', 'url': '#delete'},
                {'divider': True},
                {'label': 'Archive', 'url': '#archive'},
            ],
            variant="primary"
        )

        self.navbar_example = NavBar(
            brand={'text': 'djust', 'url': '/'},
            items=[
                {'label': 'Home', 'url': '/'},
                {'label': 'Demos', 'url': '/demos/'},
                {'label': 'Components', 'url': '/demos/component-showcase/', 'active': True},
                {'label': 'Docs', 'url': '/docs/'},
            ],
            variant="dark"
        )

    def _init_layout_components(self):
        """Initialize layout components."""
        self.button_group_example = ButtonGroup(
            buttons=[
                {'label': 'Left', 'variant': 'outline-primary'},
                {'label': 'Middle', 'variant': 'outline-primary', 'active': True},
                {'label': 'Right', 'variant': 'outline-primary'},
            ]
        )

        self.list_group_example = ListGroup(items=[
            {'label': 'Dashboard', 'url': '#dashboard', 'active': True},
            {'label': 'Profile', 'url': '#profile'},
            {'label': 'Settings', 'url': '#settings'},
        ])

    def _init_data_components(self):
        """Initialize data components."""
        self.table_example = Table(
            columns=[
                {'key': 'name', 'label': 'Name'},
                {'key': 'email', 'label': 'Email'},
                {'key': 'status', 'label': 'Status'},
            ],
            data=[
                {'name': 'John Doe', 'email': 'john@example.com', 'status': 'Active'},
                {'name': 'Jane Smith', 'email': 'jane@example.com', 'status': 'Active'},
                {'name': 'Bob Wilson', 'email': 'bob@example.com', 'status': 'Inactive'},
            ],
            striped=True,
            hover=True
        )

    @event_handler
    def increment(self):
        """Increment the counter (for interactive demo)."""
        self.counter += 1

    @event_handler
    def decrement(self):
        """Decrement the counter."""
        self.counter -= 1

    @event_handler
    def toggle_switch(self):
        """Toggle the switch state."""
        import sys
        print(f"\n[toggle_switch] BEFORE: switch_enabled={self.switch_enabled}", file=sys.stderr)
        print(f"[toggle_switch] Switch HTML BEFORE toggle:", file=sys.stderr)
        print(f"{self.switch_notifications.render()}", file=sys.stderr)

        self.switch_enabled = not self.switch_enabled
        # Update component in-place (proper pattern)
        self.switch_notifications.update(checked=self.switch_enabled)

        print(f"\n[toggle_switch] AFTER: switch_enabled={self.switch_enabled}", file=sys.stderr)
        print(f"[toggle_switch] Switch HTML AFTER toggle:", file=sys.stderr)
        print(f"{self.switch_notifications.render()}", file=sys.stderr)

    @event_handler
    def update_slider(self, value: str = "50", **kwargs):
        """Update slider value."""
        self.slider_value = float(value)
        # Update component in-place
        self.range_volume.update(value=self.slider_value)

    @event_handler
    def update_radio(self, size: str = "medium", **kwargs):
        """Update radio selection."""
        self.selected_radio = size
        # Update component in-place
        self.radio_size.update(value=self.selected_radio)

    @event_handler
    def search_components(self, query: str = "", **kwargs):
        """Filter components by search query."""
        self.search_query = query.lower()

    @event_handler
    def filter_category(self, category: str = "all", **kwargs):
        """Filter components by category."""
        self.active_category = category

    def get_context_data(self, **kwargs):
        """Return context data for template."""
        return {
            # Categories
            'active_category': self.active_category,
            'search_query': self.search_query,

            # Display Components
            'alert_success': self.alert_success,
            'alert_warning': self.alert_warning,
            'alert_danger': self.alert_danger,
            'avatar_image': self.avatar_image,
            'avatar_initials': self.avatar_initials,
            'badge_primary': self.badge_primary,
            'badge_success': self.badge_success,
            'badge_danger': self.badge_danger,
            'button_primary': self.button_primary,
            'button_outline': self.button_outline,
            'button_lg': self.button_lg,
            'card_simple': self.card_simple,
            'spinner_border': self.spinner_border,
            'spinner_grow': self.spinner_grow,
            'progress_75': self.progress_75,
            'progress_animated': self.progress_animated,
            'tooltip_example': self.tooltip_example,
            'toast_success': self.toast_success,
            'divider_simple': self.divider_simple,
            'divider_text': self.divider_text,
            'icon_star': self.icon_star,
            'icon_check': self.icon_check,

            # Form Components
            'input_text': self.input_text,
            'input_email': self.input_email,
            'select_country': self.select_country,
            'checkbox_terms': self.checkbox_terms,
            'checkbox_newsletter': self.checkbox_newsletter,
            'textarea_bio': self.textarea_bio,
            'radio_size': self.radio_size,
            'switch_notifications': self.switch_notifications,
            'range_volume': self.range_volume,

            # Container Components
            'modal_example': self.modal_example,
            'tabs_example': self.tabs_example,
            'accordion_example': self.accordion_example,
            'offcanvas_example': self.offcanvas_example,

            # Navigation Components
            'breadcrumb_example': self.breadcrumb_example,
            'pagination_example': self.pagination_example,
            'dropdown_example': self.dropdown_example,
            'navbar_example': self.navbar_example,

            # Layout Components
            'button_group_example': self.button_group_example,
            'list_group_example': self.list_group_example,

            # Data Components
            'table_example': self.table_example,

            # Interactive states
            'counter': self.counter,
            'switch_enabled': self.switch_enabled,
            'slider_value': self.slider_value,
            'selected_radio': self.selected_radio,
        }
