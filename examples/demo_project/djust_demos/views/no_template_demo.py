"""
No-Template Demo - Building UIs with ZERO Template Files

This demo showcases building a complete, functional page using ONLY Python.
No template file exists - everything is generated from template_string and components.
"""

from djust import LiveView
from djust.components.layout import NavbarComponent, NavItem, TabsComponent, TabItem
from djust.decorators import event_handler
from djust.components.ui import (
    AlertComponent,
    BadgeComponent,
    ButtonComponent,
    CardComponent,
    DropdownComponent,
    DropdownItem,
    ProgressComponent,
)


class NoTemplateDemo(LiveView):
    """
    A complete interactive dashboard with ZERO template files.

    Everything you see is generated from Python:
    - Navigation bar
    - Layout structure
    - All UI components
    - Interactive functionality

    The template_string is static for efficient VDOM patching.
    """

    # No template file! Everything is generated from Python
    template_name = None

    # Static template string for efficient VDOM patching
    template = """
    {{ navbar.render }}

    <div class="container" style="margin-top: 80px;">
        <!-- Header -->
        <div class="row mb-4">
            <div class="col-12">
                <h1>No Template Demo 🎨</h1>
                <p class="lead">This entire page was built without any template file - pure Python!</p>
            </div>
        </div>

        <!-- Alerts -->
        {% if show_success %}
        <div class="alert alert-success alert-dismissible fade show" role="alert">
            Action completed successfully!
            <button type="button" class="btn-close" @click="dismiss_alert"></button>
        </div>
        {% endif %}

        <div class="alert alert-info" role="alert">
            This entire page was generated from Python - no template file!
        </div>

        <!-- Main Grid -->
        <div class="row g-4 mb-4">
            <div class="col-md-6">
                <div class="card">
                    <div class="card-body">
                        <h5 class="card-title">Interactive Counter</h5>
                        <div class="text-center py-4">
                            <h2 class="display-1 mb-4">
                                <span class="badge bg-{% if counter > 0 %}primary{% else %}secondary{% endif %}">{{ counter }}</span>
                            </h2>
                            <div class="d-flex gap-2 justify-content-center flex-wrap">
                                <button class="btn btn-danger btn-lg" @click="decrement">-</button>
                                <button class="btn btn-secondary btn-lg" @click="reset_counter">Reset</button>
                                <button class="btn btn-success btn-lg" @click="increment">+</button>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            <div class="col-md-6">
                <div class="card">
                    <div class="card-body">
                        <h5 class="card-title">Quick Todo List</h5>
                        <div class="mb-3">
                            {% if items %}
                                {% for item in items %}
                                <div class="d-flex justify-content-between align-items-center p-2 border-bottom">
                                    <span>{{ item }}</span>
                                    <button class="btn btn-sm btn-danger" @click="remove_item" data-index="{{ forloop.counter0 }}">×</button>
                                </div>
                                {% endfor %}
                            {% else %}
                                <p class="text-muted">No items</p>
                            {% endif %}
                        </div>
                        <div class="input-group">
                            <input type="text" class="form-control" placeholder="Add item..." id="new-item">
                            <button class="btn btn-primary" @click="add_item">Add</button>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <div class="row g-4 mb-4">
            <div class="col-md-6">
                <div class="card">
                    <div class="card-body">
                        <h5 class="card-title">Statistics</h5>
                        <div class="row text-center">
                            <div class="col-4">
                                <h3>{{ counter }}</h3>
                                <small class="text-muted">Clicks</small>
                            </div>
                            <div class="col-4">
                                <h3>{{ items|length }}</h3>
                                <small class="text-muted">Items</small>
                            </div>
                            <div class="col-4">
                                <h3><span class="badge bg-warning">{{ notification_count }}</span></h3>
                                <small class="text-muted">Alerts</small>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            <div class="col-md-6">
                <div class="card">
                    <div class="card-body">
                        <h5 class="card-title">Progress Tracker</h5>
                        <div class="mb-3">
                            <p class="mb-2">Current Progress: {{ progress }}%</p>
                            <div class="progress" style="height: 30px;">
                                <div class="progress-bar progress-bar-striped progress-bar-animated bg-success"
                                     role="progressbar"
                                     style="width: {{ progress }}%"
                                     aria-valuenow="{{ progress }}"
                                     aria-valuemin="0"
                                     aria-valuemax="100">
                                    {{ progress }}%
                                </div>
                            </div>
                        </div>
                        <div class="d-flex gap-2">
                            <button class="btn btn-danger btn-sm" @click="decrease_progress">- 10%</button>
                            <button class="btn btn-success btn-sm" @click="increase_progress">+ 10%</button>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Actions -->
        <div class="row mb-4">
            <div class="col-12">
                <div class="card">
                    <div class="card-body">
                        <h5 class="card-title mb-3">Quick Actions</h5>
                        <div class="d-flex gap-2 flex-wrap">
                            {{ theme_dropdown.render }}
                            {{ actions_dropdown.render }}
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Documentation -->
        <div class="row mb-4">
            <div class="col-12">
                <div class="card">
                    <div class="card-body">
                        <h5 class="card-title mb-3">Documentation</h5>
                        {{ docs_tabs.render }}
                    </div>
                </div>
            </div>
        </div>

        <!-- Source Code -->
        <div class="row mb-5">
            <div class="col-12">
                <div class="card border-success">
                    <div class="card-body">
                        <h5 class="card-title text-success">✨ No Template File!</h5>
                        <p>This page demonstrates the ultimate component-based architecture:</p>
                        <ul>
                            <li>Zero <code>.html</code> template files</li>
                            <li>All UI generated from Python</li>
                            <li>Static template for efficient VDOM patching</li>
                            <li>Full interactivity maintained</li>
                        </ul>
                        <p class="mb-0">
                            <strong>File:</strong> <code>demo_app/views/no_template_demo.py</code><br>
                            <strong>Template:</strong> <code>None - using template_string!</code>
                        </p>
                    </div>
                </div>
            </div>
        </div>
    </div>
    """

    def mount(self, request, **kwargs):
        """Initialize state and components (created once, not rebuilt)"""

        # State variables
        self.counter = 0
        self.items = ["Buy groceries", "Walk the dog", "Write code"]
        self.show_success = False
        self.notification_count = 3
        self.progress = 45
        self.selected_theme = "Default"

        # Create components once - they don't change structure
        self._build_components()

    def _build_components(self):
        """Build components that have stable structure"""

        # Navigation - created once
        self.navbar = NavbarComponent(
            brand_name="No Template Demo",
            brand_logo="/static/images/djust.png",
            brand_href="/",
            items=[
                NavItem("Home", "/", active=False),
                NavItem("Demos", "/demos/", active=True),
                NavItem("Back to Demos", "/demos/", active=False),
            ],
            fixed_top=True,
            logo_height=16,
        )

        # Theme Dropdown - need to rebuild when theme changes
        self.theme_dropdown = DropdownComponent(
            label=f"Theme: {self.selected_theme}",
            variant="info",
            items=[
                DropdownItem(text="Default", action="change_theme", data={"theme": "Default"}),
                DropdownItem(text="Dark", action="change_theme", data={"theme": "Dark"}),
                DropdownItem(text="Light", action="change_theme", data={"theme": "Light"}),
                DropdownItem(divider=True),
                DropdownItem(text="Blue", action="change_theme", data={"theme": "Blue"}),
                DropdownItem(text="Green", action="change_theme", data={"theme": "Green"}),
            ]
        )

        # Actions Dropdown - static
        self.actions_dropdown = DropdownComponent(
            label="Actions",
            variant="primary",
            items=[
                DropdownItem(text="Reset Everything", action="reset_all", icon="🔄"),
                DropdownItem(text="Add Random Items", action="add_random_items", icon="➕"),
                DropdownItem(divider=True),
                DropdownItem(text="Show Success", action="show_success_alert", icon="✓"),
            ]
        )

        # Documentation Tabs
        self.docs_tabs = TabsComponent(
            tabs=[
                TabItem(
                    id="about",
                    label="About",
                    content="""
                        <div class="p-3">
                            <h5>Zero Template Files</h5>
                            <p>This entire page was built without a template file!</p>
                            <ul>
                                <li>No <code>.html</code> file exists for this view</li>
                                <li>Static <code>template_string</code> for efficient VDOM patching</li>
                                <li>Template syntax for dynamic content</li>
                                <li>Components for complex widgets</li>
                                <li>Fully interactive and reactive</li>
                            </ul>
                            <p class="mb-0">
                                Check the source code at
                                <code>demo_app/views/no_template_demo.py</code>
                            </p>
                        </div>
                    """
                ),
                TabItem(
                    id="how",
                    label="How It Works",
                    content="""
                        <div class="p-3">
                            <h5>Implementation</h5>
                            <pre><code class="language-python">class NoTemplateDemo(LiveView):
    # Static template for efficient VDOM patching
    template = '''
        &lt;div&gt;
            {{ navbar.render }}
            &lt;h1&gt;Counter: {{ counter }}&lt;/h1&gt;
            &lt;button @click="increment"&gt;+&lt;/button&gt;
        &lt;/div&gt;
    '''

    def mount(self, request, **kwargs):
        self.counter = 0
        self.navbar = NavbarComponent(...)

    @event_handler
    def increment(self):
        self.counter += 1  # Just update state!
        # VDOM automatically diffs and patches
</code></pre>
                            <p>Static template + dynamic state = efficient VDOM patching!</p>
                        </div>
                    """
                ),
                TabItem(
                    id="benefits",
                    label="Benefits",
                    content="""
                        <div class="p-3">
                            <h5>Why This Matters</h5>
                            <ul>
                                <li><strong>Single File:</strong> Entire UI in one Python file</li>
                                <li><strong>Type Safety:</strong> Full IDE support and autocomplete</li>
                                <li><strong>Efficient Updates:</strong> VDOM patches, not full HTML</li>
                                <li><strong>Testability:</strong> Easy to unit test</li>
                                <li><strong>Maintainability:</strong> All logic in one place</li>
                                <li><strong>No Context Switching:</strong> Stay in Python!</li>
                            </ul>
                            <p class="text-success mb-0">
                                <strong>Perfect for:</strong> Dashboards, admin panels, internal tools,
                                APIs with UI, microservices with frontends
                            </p>
                        </div>
                    """
                ),
            ],
            active_tab="about",
            variant="pills",
            action="switch_tab"
        )

    def render_full_template(self, request=None):
        """Override to wrap template_string content with full HTML document"""
        # Get the inner content (just the template_string rendered, no wrapper)
        inner_content = super().render_full_template(request)

        # Get view path for WebSocket mounting
        view_path = f"{self.__class__.__module__}.{self.__class__.__name__}"

        # Wrap with data-djust-root container
        liveview_content = f'<div data-djust-root data-djust-view="{view_path}">\n{inner_content}\n</div>'

        # Wrap with full HTML document structure
        wrapped_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>No Template Demo - djust</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="/static/css/theme.css">
</head>
<body>
{liveview_content}
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>"""
        return wrapped_html

    # === Event Handlers ===
    # Note: Just update state variables - VDOM will automatically patch!

    @event_handler
    def increment(self):
        """Increment counter - VDOM patches the badge and stats automatically"""
        self.counter += 1

    @event_handler
    def decrement(self):
        """Decrement counter - VDOM patches the badge and stats automatically"""
        self.counter -= 1

    @event_handler
    def reset_counter(self):
        """Reset counter - VDOM patches the badge and stats automatically"""
        self.counter = 0

    @event_handler
    def add_item(self, **kwargs):
        """Add item to todo list - VDOM patches the list automatically"""
        import random
        items = ["Read a book", "Exercise", "Learn Python", "Build a project", "Drink water"]
        self.items.append(random.choice(items))

    @event_handler
    def remove_item(self, index: str = None, **kwargs):
        """Remove item from todo list - VDOM patches the list automatically"""
        if index is not None:
            idx = int(index)
            if 0 <= idx < len(self.items):
                self.items.pop(idx)

    @event_handler
    def increase_progress(self):
        """Increase progress by 10% - VDOM patches the progress bar automatically"""
        self.progress = min(100, self.progress + 10)

    @event_handler
    def decrease_progress(self):
        """Decrease progress by 10% - VDOM patches the progress bar automatically"""
        self.progress = max(0, self.progress - 10)

    @event_handler
    def change_theme(self, theme: str = None, **kwargs):
        """Change theme - need to rebuild dropdown to update label"""
        if theme:
            self.selected_theme = theme
            # Rebuild theme dropdown to update label
            self.theme_dropdown = DropdownComponent(
                label=f"Theme: {self.selected_theme}",
                variant="info",
                items=[
                    DropdownItem(text="Default", action="change_theme", data={"theme": "Default"}),
                    DropdownItem(text="Dark", action="change_theme", data={"theme": "Dark"}),
                    DropdownItem(text="Light", action="change_theme", data={"theme": "Light"}),
                    DropdownItem(divider=True),
                    DropdownItem(text="Blue", action="change_theme", data={"theme": "Blue"}),
                    DropdownItem(text="Green", action="change_theme", data={"theme": "Green"}),
                ]
            )

    @event_handler
    def reset_all(self):
        """Reset everything - VDOM patches all changed elements automatically"""
        self.counter = 0
        self.items = ["Buy groceries", "Walk the dog", "Write code"]
        self.progress = 45
        self.notification_count = 3
        self.show_success = True

    @event_handler
    def add_random_items(self):
        """Add random items - VDOM patches the list automatically"""
        import random
        items = ["Debug code", "Review PR", "Update docs", "Fix tests", "Deploy app"]
        for _ in range(3):
            self.items.append(random.choice(items))

    @event_handler
    def show_success_alert(self):
        """Show success alert - VDOM adds the alert automatically"""
        self.show_success = True

    @event_handler
    def dismiss_alert(self):
        """Dismiss alert - VDOM removes the alert automatically"""
        self.show_success = False

    @event_handler
    def switch_tab(self, tab: str = None, **kwargs):
        """Switch active tab - component handles state internally"""
        if tab:
            self.docs_tabs.activate_tab(tab)
