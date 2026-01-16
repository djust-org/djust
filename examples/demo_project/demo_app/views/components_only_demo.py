"""
Components-Only Demo - Building UIs with Zero HTML

This demo showcases building a complete, functional page using ONLY Python components.
No HTML in the template - everything is composed from reusable components.
"""

from djust import LiveView
from djust.components.layout import NavbarComponent, NavItem, TabsComponent, TabItem
from djust.components.ui import (
    AlertComponent,
    BadgeComponent,
    ButtonComponent,
    ButtonGroup,
    CardComponent,
    DropdownComponent,
    DropdownItem,
    ListGroup,
    ModalComponent,
    ProgressComponent,
    SpinnerComponent,
)


class ComponentsOnlyDemo(LiveView):
    """
    A complete dashboard built entirely from Python components.

    Features:
    - Navigation bar
    - Alert messages
    - Stats cards with badges
    - Interactive buttons
    - Dropdowns for actions
    - Progress indicators
    - Modal dialogs
    - All without writing HTML!
    """

    template_name = 'demos/components_only.html'

    def mount(self, request, **kwargs):
        """Initialize component-based dashboard"""

        # State
        self.show_alert = True
        self.alert_message = "Welcome to the Components-Only demo! Everything you see is built with Python components."
        self.alert_variant = "info"

        self.task_progress = 65
        self.tasks_completed = 13
        self.tasks_total = 20

        self.user_status = "active"
        self.selected_action = None

        self.show_modal = False
        self.modal_title = "Confirm Action"
        self.modal_message = "Are you sure you want to proceed?"

        # Build components
        self._build_navbar()
        self._build_header()
        self._build_stats_cards()
        self._build_actions_section()
        self._build_button_groups()
        self._build_progress_section()
        self._build_tabs_section()
        self._build_modal()

    def _build_navbar(self):
        """Build navigation bar component"""
        self.navbar = NavbarComponent(
            brand_name="Components Only",
            brand_logo="/static/images/djust.png",
            brand_href="/",
            items=[
                NavItem("Home", "/", active=False),
                NavItem("Demos", "/demos/", active=True),
                NavItem("Components", "/kitchen-sink/", active=False),
                NavItem("Forms", "/forms/", active=False),
            ],
            fixed_top=True,
            logo_height=16,
        )

    def _build_header(self):
        """Build header section with alert"""
        if self.show_alert:
            self.header_alert = AlertComponent(
                message=self.alert_message,
                variant=self.alert_variant,
                dismissible=True,
                on_dismiss='dismiss_alert'
            )
        else:
            self.header_alert = None

    def _build_stats_cards(self):
        """Build statistics cards"""
        # Tasks Card
        tasks_badge = BadgeComponent(
            text=f"{self.tasks_completed}/{self.tasks_total}",
            variant="primary"
        )

        self.tasks_card = CardComponent(
            title="Tasks Progress",
            content=f"""
                <div class="d-flex justify-content-between align-items-center">
                    <div>
                        <h2 class="mb-0">{self.tasks_completed}</h2>
                        <p class="text-muted mb-0">Completed Tasks</p>
                    </div>
                    <div>{tasks_badge.render()}</div>
                </div>
            """,
            variant="default"
        )

        # Status Card
        status_badge = BadgeComponent(
            text=self.user_status.upper(),
            variant="success" if self.user_status == "active" else "warning"
        )

        self.status_card = CardComponent(
            title="System Status",
            content=f"""
                <div class="d-flex justify-content-between align-items-center">
                    <div>
                        <p class="mb-2">Current Status</p>
                        {status_badge.render()}
                    </div>
                    <div class="text-end">
                        <small class="text-muted">Last updated: Just now</small>
                    </div>
                </div>
            """,
            variant="default"
        )

    def _build_actions_section(self):
        """Build action buttons and dropdowns"""
        self.primary_button = ButtonComponent(
            text="Complete Task",
            variant="primary",
            size="md",
            action="complete_task"
        )

        self.secondary_button = ButtonComponent(
            text="Show Modal",
            variant="secondary",
            size="md",
            action="open_modal"
        )

        self.actions_dropdown = DropdownComponent(
            label="More Actions",
            variant="info",
            items=[
                DropdownItem(text="Reset Progress", action="reset_progress", icon="🔄"),
                DropdownItem(text="Export Data", action="export_data", icon="📥"),
                DropdownItem(divider=True),
                DropdownItem(text="Settings", action="show_settings", icon="⚙️"),
            ]
        )

    def _build_button_groups(self):
        """Build button group components"""
        # Basic button group
        self.alignment_group = ButtonGroup(
            buttons=[
                {'label': 'Left', 'variant': 'outline-primary'},
                {'label': 'Center', 'variant': 'outline-primary', 'active': True},
                {'label': 'Right', 'variant': 'outline-primary'},
            ],
            size='md'
        )

        # Toolbar-style button group
        self.formatting_group = ButtonGroup(
            buttons=[
                {'label': 'Bold', 'variant': 'outline-secondary'},
                {'label': 'Italic', 'variant': 'outline-secondary'},
                {'label': 'Underline', 'variant': 'outline-secondary'},
            ],
            size='sm',
            role='toolbar'
        )

        # Vertical button group
        self.view_mode_group = ButtonGroup(
            buttons=[
                {'label': 'List View', 'variant': 'primary', 'active': True},
                {'label': 'Grid View', 'variant': 'primary'},
                {'label': 'Card View', 'variant': 'primary'},
            ],
            vertical=True
        )

    def _build_progress_section(self):
        """Build progress indicators"""
        self.task_progress_bar = ProgressComponent(
            value=self.task_progress,
            variant="success",
            striped=True,
            animated=True,
            show_label=True
        )

        self.loading_spinner = SpinnerComponent(
            variant="primary",
            size="sm",
            label="Loading..."
        )

    def _build_list_groups(self):
        """Build list group components"""
        # Basic navigation list
        self.nav_list = ListGroup(items=[
            {'label': 'Dashboard', 'url': '/demos/components', 'active': True},
            {'label': 'Users', 'url': '#', 'badge': {'text': '12', 'variant': 'primary'}},
            {'label': 'Products', 'url': '#', 'badge': {'text': '48', 'variant': 'info'}},
            {'label': 'Settings', 'url': '#'},
        ])

        # Status list with variants
        self.status_list = ListGroup(items=[
            {'label': 'All systems operational', 'variant': 'success'},
            {'label': 'Scheduled maintenance tonight', 'variant': 'warning'},
            {'label': 'Database backup in progress', 'variant': 'info'},
        ])

        # Numbered task list
        self.task_list = ListGroup(
            items=[
                {'label': 'Review pull requests', 'url': '#'},
                {'label': 'Update documentation', 'url': '#'},
                {'label': 'Deploy to production', 'url': '#', 'disabled': True},
            ],
            numbered=True
        )

        # Flush list (for sidebars/cards)
        self.menu_list = ListGroup(
            items=[
                {'label': 'Profile Settings', 'url': '#'},
                {'label': 'Security', 'url': '#'},
                {'label': 'Notifications', 'url': '#'},
                {'label': 'Privacy', 'url': '#'},
            ],
            flush=True
        )

    def _build_tabs_section(self):
        """Build tabbed content"""
        self.content_tabs = TabsComponent(
            tabs=[
                TabItem(
                    id='overview',
                    label='Overview',
                    content=f"""
                        <div class="p-3">
                            <h5>Component-Based Architecture</h5>
                            <p>This entire page is built using Python components:</p>
                            <ul>
                                <li><code>NavbarComponent</code> for navigation</li>
                                <li><code>AlertComponent</code> for messages</li>
                                <li><code>CardComponent</code> for content sections</li>
                                <li><code>ButtonComponent</code> for actions</li>
                                <li><code>ButtonGroup</code> for grouped buttons</li>
                                <li><code>DropdownComponent</code> for menus</li>
                                <li><code>ProgressComponent</code> for indicators</li>
                                <li><code>TabsComponent</code> for this tabbed interface</li>
                                <li><code>BadgeComponent</code> for status labels</li>
                                <li><code>ModalComponent</code> for dialogs</li>
                            </ul>
                            <p>No HTML was written in the template - it's all Python!</p>
                        </div>
                    """
                ),
                TabItem(
                    id='features',
                    label='Features',
                    content="""
                        <div class="p-3">
                            <h5>Key Benefits</h5>
                            <ul>
                                <li><strong>Type Safety:</strong> Full IDE support and type checking</li>
                                <li><strong>Reusability:</strong> Components can be shared across views</li>
                                <li><strong>Maintainability:</strong> Update component logic in one place</li>
                                <li><strong>Reactivity:</strong> Components update automatically</li>
                                <li><strong>Framework Agnostic:</strong> Works with Bootstrap, Tailwind, or plain CSS</li>
                            </ul>
                        </div>
                    """
                ),
                TabItem(
                    id='code',
                    label='Code Example',
                    content="""
                        <div class="p-3">
                            <h5>Building Components in Python</h5>
                            <pre><code class="language-python">
# In your LiveView
def mount(self, request):
    self.alert = AlertComponent(
        message="Welcome!",
        variant="info",
        dismissible=True
    )

    self.card = CardComponent(
        title="Stats",
        content="<p>Your content here</p>"
    )

    self.dropdown = DropdownComponent(
        label="Actions",
        items=[
            DropdownItem(text="Edit", action="edit"),
            DropdownItem(text="Delete", action="delete"),
        ]
    )

# In your template
{{ alert.render }}
{{ card.render }}
{{ dropdown.render }}
                            </code></pre>
                        </div>
                    """
                ),
            ],
            active_tab='overview',
            variant='pills',
            action='switch_tab'
        )

    def _build_modal(self):
        """Build modal dialog"""
        if self.show_modal:
            self.modal = ModalComponent(
                title=self.modal_title,
                content=self.modal_message,
                show=True,
                on_close='close_modal'
            )
        else:
            self.modal = None

    # === Event Handlers ===

    def dismiss_alert(self):
        """Dismiss the alert message"""
        self.show_alert = False
        self._build_header()

    def complete_task(self):
        """Complete a task and update progress"""
        if self.tasks_completed < self.tasks_total:
            self.tasks_completed += 1
            self.task_progress = int((self.tasks_completed / self.tasks_total) * 100)

            # Rebuild components
            self._build_stats_cards()
            self._build_progress_section()

            # Show success alert
            self.show_alert = True
            self.alert_message = f"Task completed! {self.tasks_completed}/{self.tasks_total} done."
            self.alert_variant = "success"
            self._build_header()

    def reset_progress(self):
        """Reset all progress"""
        self.tasks_completed = 0
        self.task_progress = 0

        # Rebuild components
        self._build_stats_cards()
        self._build_progress_section()

        # Show info alert
        self.show_alert = True
        self.alert_message = "Progress has been reset."
        self.alert_variant = "warning"
        self._build_header()

    def export_data(self):
        """Simulate data export"""
        self.show_alert = True
        self.alert_message = "Data exported successfully!"
        self.alert_variant = "success"
        self._build_header()

    def show_settings(self):
        """Show settings modal"""
        self.show_modal = True
        self.modal_title = "Settings"
        self.modal_message = """
            <div class="mb-3">
                <label class="form-label">User Status</label>
                <select class="form-select" @change="change_status">
                    <option value="active">Active</option>
                    <option value="inactive">Inactive</option>
                </select>
            </div>
        """
        self._build_modal()

    def open_modal(self):
        """Open the confirmation modal"""
        self.show_modal = True
        self.modal_title = "Component Modal"
        self.modal_message = "This modal was created using the ModalComponent. No HTML needed!"
        self._build_modal()

    def close_modal(self):
        """Close the modal"""
        self.show_modal = False
        self._build_modal()

    def change_status(self, value: str = None, **kwargs):
        """Change user status"""
        if value:
            self.user_status = value
            self._build_stats_cards()
            self.close_modal()

    def switch_tab(self, tab: str = None, **kwargs):
        """Switch active tab"""
        if tab:
            self.content_tabs.activate_tab(tab)

