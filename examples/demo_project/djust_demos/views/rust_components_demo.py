"""
Components Demo - Showcasing Python Component Architecture

This demo showcases components using the Python component pattern where
components are instantiated in Python code and have state/behavior.
"""

from djust import LiveView
from djust.components.layout import NavbarComponent, NavItem, TabsComponent, TabItem
from djust.components.ui import DropdownComponent, DropdownItem
from djust.decorators import event_handler


class RustComponentsDemo(LiveView):
    """
    Interactive demo showcasing RustDropdown and RustTabs components.

    Features:
    - Multiple dropdown variants and configurations
    - Different tab styles (default, pills, underline)
    - Vertical and horizontal tabs
    - Interactive state management
    - Framework comparison (Bootstrap5, Tailwind, Plain)
    """

    template_name = 'demos/rust_components.html'

    def get_context_data(self, **kwargs):
        """Add navbar to context"""
        context = super().get_context_data(**kwargs)

        # Add navbar component
        context['navbar'] = NavbarComponent(
            brand_name="",
            brand_logo="/static/images/djust.png",
            brand_href="/",
            items=[
                NavItem("Home", "/", active=False),
                NavItem("Demos", "/demos/", active=True),
                NavItem("Components", "/kitchen-sink/", active=False),
                NavItem("Forms", "/forms/", active=False),
                NavItem("Docs", "/docs/", active=False),
                NavItem("Hosting ↗", "https://djustlive.com", external=True),
            ],
            fixed_top=True,
            logo_height=16,
        )

        return context

    def mount(self, request, **kwargs):
        """Initialize component demo state"""

        # Messages state
        self.dropdown_message = ""
        self.counter = 0
        self.selected_option = "opt1"
        self.selected_country = "United States"
        self.variant_selections = {
            'primary': None,
            'success': None,
            'warning': None,
            'danger': None
        }
        self.selected_location = "Canada"

        # === DROPDOWN COMPONENTS ===

        # Basic Dropdown - shows selected value
        dropdown_label = f"Selected: {self.selected_option}" if self.selected_option != "opt1" else "Choose an option"
        self.basic_dropdown = DropdownComponent(
            label=dropdown_label,
            variant="primary",
            items=[
                DropdownItem(text='Option 1', action='select_basic_option', data={'value': 'opt1'}),
                DropdownItem(text='Option 2', action='select_basic_option', data={'value': 'opt2'}),
                DropdownItem(text='Option 3', action='select_basic_option', data={'value': 'opt3'}),
            ]
        )

        # Variant Dropdowns
        primary_label = self.variant_selections['primary'] or "Primary"
        self.dropdown_primary = DropdownComponent(
            label=primary_label,
            variant="primary",
            items=[
                DropdownItem(text='Option 1', action='select_variant', data={'variant': 'primary', 'option': 'Option 1'}),
                DropdownItem(text='Option 2', action='select_variant', data={'variant': 'primary', 'option': 'Option 2'}),
                DropdownItem(text='Option 3', action='select_variant', data={'variant': 'primary', 'option': 'Option 3'}),
            ]
        )

        success_label = self.variant_selections['success'] or "Success"
        self.dropdown_success = DropdownComponent(
            label=success_label,
            variant="success",
            items=[
                DropdownItem(text='Option 1', action='select_variant', data={'variant': 'success', 'option': 'Option 1'}),
                DropdownItem(text='Option 2', action='select_variant', data={'variant': 'success', 'option': 'Option 2'}),
            ]
        )

        warning_label = self.variant_selections['warning'] or "Warning"
        self.dropdown_warning = DropdownComponent(
            label=warning_label,
            variant="warning",
            items=[
                DropdownItem(text='Option 1', action='select_variant', data={'variant': 'warning', 'option': 'Option 1'}),
                DropdownItem(text='Option 2', action='select_variant', data={'variant': 'warning', 'option': 'Option 2'}),
            ]
        )

        danger_label = self.variant_selections['danger'] or "Danger"
        self.dropdown_danger = DropdownComponent(
            label=danger_label,
            variant="danger",
            items=[
                DropdownItem(text='Option 1', action='select_variant', data={'variant': 'danger', 'option': 'Option 1'}),
                DropdownItem(text='Option 2', action='select_variant', data={'variant': 'danger', 'option': 'Option 2'}),
            ]
        )

        # Size Dropdowns - interactive with country selection
        self.dropdown_small = DropdownComponent(
            label=self.selected_country if self.selected_country != "United States" else "Select Country",
            variant="secondary",
            size="sm",
            items=[
                DropdownItem(text='United States', action='select_country', data={'country': 'United States'}),
                DropdownItem(text='United Kingdom', action='select_country', data={'country': 'United Kingdom'}),
                DropdownItem(text='Canada', action='select_country', data={'country': 'Canada'}),
                DropdownItem(text='Australia', action='select_country', data={'country': 'Australia'}),
                DropdownItem(text='Germany', action='select_country', data={'country': 'Germany'}),
            ]
        )

        self.dropdown_medium = DropdownComponent(
            label=self.selected_country if self.selected_country != "United States" else "Select Country",
            variant="secondary",
            items=[
                DropdownItem(text='United States', action='select_country', data={'country': 'United States'}),
                DropdownItem(text='United Kingdom', action='select_country', data={'country': 'United Kingdom'}),
                DropdownItem(text='Canada', action='select_country', data={'country': 'Canada'}),
                DropdownItem(text='Australia', action='select_country', data={'country': 'Australia'}),
                DropdownItem(text='Germany', action='select_country', data={'country': 'Germany'}),
            ]
        )

        self.dropdown_large = DropdownComponent(
            label=self.selected_country if self.selected_country != "United States" else "Select Country",
            variant="secondary",
            size="lg",
            items=[
                DropdownItem(text='United States', action='select_country', data={'country': 'United States'}),
                DropdownItem(text='United Kingdom', action='select_country', data={'country': 'United Kingdom'}),
                DropdownItem(text='Canada', action='select_country', data={'country': 'Canada'}),
                DropdownItem(text='Australia', action='select_country', data={'country': 'Australia'}),
                DropdownItem(text='Germany', action='select_country', data={'country': 'Germany'}),
            ]
        )

        # State Dropdowns
        self.dropdown_selected = DropdownComponent(
            label=self.selected_location,
            variant="info",
            items=[
                DropdownItem(text='United States', action='select_location', data={'location': 'United States'}),
                DropdownItem(text='United Kingdom', action='select_location', data={'location': 'United Kingdom'}),
                DropdownItem(text='Canada', action='select_location', data={'location': 'Canada'}),
            ]
        )

        self.dropdown_disabled = DropdownComponent(
            label="Disabled",
            variant="secondary",
            disabled=True,
            items=[
                DropdownItem(text='Option 1', action='noop'),
                DropdownItem(text='Option 2', action='noop'),
            ]
        )

        # === TABS COMPONENTS ===

        # Basic Tabs
        self.basic_tabs = TabsComponent(
            tabs=[
                TabItem(
                    id='overview',
                    label='Overview',
                    content="<p class='mb-0'>This is the overview tab. Python components provide stateful, reusable UI elements.</p>"
                ),
                TabItem(
                    id='features',
                    label='Features',
                    content="<ul class='mb-0'><li>Component state management</li><li>Event handling</li><li>Python-side logic</li><li>Reusable patterns</li></ul>"
                ),
                TabItem(
                    id='usage',
                    label='Usage',
                    content="<p class='mb-0'>Create component instances in Python and render them in templates.</p>"
                ),
            ],
            active_tab='overview',
            action='switch_basic_tab'
        )

        # Pills Tabs
        self.pills_tabs = TabsComponent(
            tabs=[
                TabItem(
                    id='overview',
                    label='Overview',
                    content="<p class='mb-0'>This is the overview tab with pills style.</p>"
                ),
                TabItem(
                    id='features',
                    label='Features',
                    content="<ul class='mb-0'><li>Multiple variants</li><li>Active tab tracking</li><li>Custom styling</li></ul>"
                ),
                TabItem(
                    id='usage',
                    label='Usage',
                    content="<p class='mb-0'>Set variant='pills' in the component constructor.</p>"
                ),
            ],
            active_tab='features',
            variant='pills',
            action='switch_pills_tab'
        )

        # Underline Tabs
        self.underline_tabs = TabsComponent(
            tabs=[
                TabItem(
                    id='overview',
                    label='Overview',
                    content="<p class='mb-0'>Modern underline style tabs.</p>"
                ),
                TabItem(
                    id='features',
                    label='Features',
                    content="<ul class='mb-0'><li>Clean design</li><li>Modern appearance</li></ul>"
                ),
                TabItem(
                    id='usage',
                    label='Usage',
                    content="<p class='mb-0'>Set variant='underline' for this style.</p>"
                ),
            ],
            active_tab='usage',
            variant='underline',
            action='switch_underline_tab'
        )

        # Vertical Tabs
        self.vertical_tabs = TabsComponent(
            tabs=[
                TabItem(
                    id='general',
                    label='General',
                    content="<p>General settings go here.</p>"
                ),
                TabItem(
                    id='security',
                    label='Security',
                    content="<p>Security settings and preferences.</p>"
                ),
                TabItem(
                    id='notifications',
                    label='Notifications',
                    content="<p>Notification preferences and settings.</p>"
                ),
                TabItem(
                    id='advanced',
                    label='Advanced',
                    content="<p>Advanced configuration options.</p>"
                ),
            ],
            active_tab='general',
            vertical=True,
            action='switch_vertical_tab'
        )

        # Interactive Tabs (updates with counter)
        self.interactive_tabs = TabsComponent(
            tabs=[
                TabItem(
                    id='general',
                    label='General',
                    content=f"<p>General settings. Counter: {self.counter}</p>"
                ),
                TabItem(
                    id='security',
                    label='Security',
                    content="<p>Security settings and preferences.</p>"
                ),
                TabItem(
                    id='notifications',
                    label='Notifications',
                    content="<p>Notification preferences and settings.</p>"
                ),
                TabItem(
                    id='advanced',
                    label='Advanced',
                    content="<p>Advanced configuration options.</p>"
                ),
            ],
            active_tab='general',
            variant='pills',
            action='switch_interactive_tab'
        )

    # === Event Handlers ===

    @event_handler
    def select_basic_option(self, value: str = None, **kwargs):
        """Handle basic dropdown selection"""
        if value:
            self.selected_option = value
            self.dropdown_message = f"✓ You selected: {value}"
            # Re-create the dropdown with updated label
            dropdown_label = f"Selected: {self.selected_option}"
            self.basic_dropdown = DropdownComponent(
                label=dropdown_label,
                variant="success",  # Change to success color
                items=[
                    DropdownItem(text='Option 1', action='select_basic_option', data={'value': 'opt1'}),
                    DropdownItem(text='Option 2', action='select_basic_option', data={'value': 'opt2'}),
                    DropdownItem(text='Option 3', action='select_basic_option', data={'value': 'opt3'}),
                ]
            )

    @event_handler
    def select_country(self, country: str = None, **kwargs):
        """Handle country selection - updates all size dropdowns"""
        if country:
            self.selected_country = country
            self.dropdown_message = f"✓ Country changed to: {country}"

            # Update all size dropdowns with new selection
            self.dropdown_small = DropdownComponent(
                label=self.selected_country,
                variant="info",
                size="sm",
                items=[
                    DropdownItem(text='United States', action='select_country', data={'country': 'United States'}),
                    DropdownItem(text='United Kingdom', action='select_country', data={'country': 'United Kingdom'}),
                    DropdownItem(text='Canada', action='select_country', data={'country': 'Canada'}),
                    DropdownItem(text='Australia', action='select_country', data={'country': 'Australia'}),
                    DropdownItem(text='Germany', action='select_country', data={'country': 'Germany'}),
                ]
            )

            self.dropdown_medium = DropdownComponent(
                label=self.selected_country,
                variant="info",
                items=[
                    DropdownItem(text='United States', action='select_country', data={'country': 'United States'}),
                    DropdownItem(text='United Kingdom', action='select_country', data={'country': 'United Kingdom'}),
                    DropdownItem(text='Canada', action='select_country', data={'country': 'Canada'}),
                    DropdownItem(text='Australia', action='select_country', data={'country': 'Australia'}),
                    DropdownItem(text='Germany', action='select_country', data={'country': 'Germany'}),
                ]
            )

            self.dropdown_large = DropdownComponent(
                label=self.selected_country,
                variant="info",
                size="lg",
                items=[
                    DropdownItem(text='United States', action='select_country', data={'country': 'United States'}),
                    DropdownItem(text='United Kingdom', action='select_country', data={'country': 'United Kingdom'}),
                    DropdownItem(text='Canada', action='select_country', data={'country': 'Canada'}),
                    DropdownItem(text='Australia', action='select_country', data={'country': 'Australia'}),
                    DropdownItem(text='Germany', action='select_country', data={'country': 'Germany'}),
                ]
            )

    @event_handler
    def select_variant(self, variant: str = None, option: str = None, **kwargs):
        """Handle variant dropdown selection"""
        if variant and option:
            self.variant_selections[variant] = option
            self.dropdown_message = f"✓ {variant.capitalize()} dropdown: {option} selected"

            # Recreate the dropdown with updated label
            label = self.variant_selections[variant]
            items = []

            if variant == 'primary':
                items = [
                    DropdownItem(text='Option 1', action='select_variant', data={'variant': 'primary', 'option': 'Option 1'}),
                    DropdownItem(text='Option 2', action='select_variant', data={'variant': 'primary', 'option': 'Option 2'}),
                    DropdownItem(text='Option 3', action='select_variant', data={'variant': 'primary', 'option': 'Option 3'}),
                ]
                self.dropdown_primary = DropdownComponent(label=label, variant=variant, items=items)
            elif variant == 'success':
                items = [
                    DropdownItem(text='Option 1', action='select_variant', data={'variant': 'success', 'option': 'Option 1'}),
                    DropdownItem(text='Option 2', action='select_variant', data={'variant': 'success', 'option': 'Option 2'}),
                ]
                self.dropdown_success = DropdownComponent(label=label, variant=variant, items=items)
            elif variant == 'warning':
                items = [
                    DropdownItem(text='Option 1', action='select_variant', data={'variant': 'warning', 'option': 'Option 1'}),
                    DropdownItem(text='Option 2', action='select_variant', data={'variant': 'warning', 'option': 'Option 2'}),
                ]
                self.dropdown_warning = DropdownComponent(label=label, variant=variant, items=items)
            elif variant == 'danger':
                items = [
                    DropdownItem(text='Option 1', action='select_variant', data={'variant': 'danger', 'option': 'Option 1'}),
                    DropdownItem(text='Option 2', action='select_variant', data={'variant': 'danger', 'option': 'Option 2'}),
                ]
                self.dropdown_danger = DropdownComponent(label=label, variant=variant, items=items)

    @event_handler
    def select_location(self, location: str = None, **kwargs):
        """Handle location dropdown selection"""
        if location:
            self.selected_location = location
            self.dropdown_message = f"✓ Location changed to: {location}"

            # Recreate the dropdown with updated label
            self.dropdown_selected = DropdownComponent(
                label=self.selected_location,
                variant="info",
                items=[
                    DropdownItem(text='United States', action='select_location', data={'location': 'United States'}),
                    DropdownItem(text='United Kingdom', action='select_location', data={'location': 'United Kingdom'}),
                    DropdownItem(text='Canada', action='select_location', data={'location': 'Canada'}),
                ]
            )

    @event_handler
    def increment_counter(self):
        """Increment counter and update interactive tabs"""
        self.counter += 1
        # Update the interactive tabs component with new counter value
        self.interactive_tabs = TabsComponent(
            tabs=[
                TabItem(
                    id='general',
                    label='General',
                    content=f"<p>General settings. Counter: {self.counter}</p>"
                ),
                TabItem(
                    id='security',
                    label='Security',
                    content="<p>Security settings and preferences.</p>"
                ),
                TabItem(
                    id='notifications',
                    label='Notifications',
                    content="<p>Notification preferences and settings.</p>"
                ),
                TabItem(
                    id='advanced',
                    label='Advanced',
                    content="<p>Advanced configuration options.</p>"
                ),
            ],
            active_tab='general',
            variant='pills',
            action='switch_interactive_tab'
        )

    @event_handler
    def reset_counter(self):
        """Reset counter"""
        self.counter = 0
        self.interactive_tabs = TabsComponent(
            tabs=[
                TabItem(
                    id='general',
                    label='General',
                    content=f"<p>General settings. Counter: {self.counter}</p>"
                ),
                TabItem(
                    id='security',
                    label='Security',
                    content="<p>Security settings and preferences.</p>"
                ),
                TabItem(
                    id='notifications',
                    label='Notifications',
                    content="<p>Notification preferences and settings.</p>"
                ),
                TabItem(
                    id='advanced',
                    label='Advanced',
                    content="<p>Advanced configuration options.</p>"
                ),
            ],
            active_tab='general',
            variant='pills',
            action='switch_interactive_tab'
        )

    @event_handler
    def clear_messages(self):
        """Clear all messages"""
        self.dropdown_message = ""

    # === TABS EVENT HANDLERS ===

    @event_handler
    def switch_basic_tab(self, tab: str = None, **kwargs):
        """Switch active tab in basic tabs"""
        if tab:
            self.basic_tabs.activate_tab(tab)

    @event_handler
    def switch_pills_tab(self, tab: str = None, **kwargs):
        """Switch active tab in pills tabs"""
        if tab:
            self.pills_tabs.activate_tab(tab)

    @event_handler
    def switch_underline_tab(self, tab: str = None, **kwargs):
        """Switch active tab in underline tabs"""
        if tab:
            self.underline_tabs.activate_tab(tab)

    @event_handler
    def switch_vertical_tab(self, tab: str = None, **kwargs):
        """Switch active tab in vertical tabs"""
        if tab:
            self.vertical_tabs.activate_tab(tab)

    @event_handler
    def switch_interactive_tab(self, tab: str = None, **kwargs):
        """Switch active tab in interactive tabs"""
        if tab:
            self.interactive_tabs.activate_tab(tab)
