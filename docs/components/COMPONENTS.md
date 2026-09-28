# djust Components

djust includes Python components for rendering UI and integrating reusable
widgets into LiveViews. Component behavior and styling are component-specific:
some components render framework-specific markup, while others emit the same
markup regardless of `LIVEVIEW_CONFIG['css_framework']`. Check the component's
API page or source before assuming a framework switch changes its output.

The component APIs are Python APIs, not a copy-paste component generator.
Applications can subclass or wrap a component when they need different markup.
The framework itself does not copy component source into an application.

## Philosophy

Components are Python objects provided by the installed djust package. You can
subclass or wrap them when an application needs different behavior. The
framework adapter registry described below applies to Django form rendering;
it does not automatically adapt every UI component.

## Two-Tier Component System

djust provides two component base classes for different use cases:

### Component (Stateless, Presentational)

`Component` is for rendering markup from its current properties. It does not
have the `LiveComponent` mount lifecycle or its own event dispatch:

```python
from djust.components.base import Component
from django.utils.html import format_html

class Badge(Component):
    def __init__(self, text, variant="primary"):
        super().__init__(text=text, variant=variant)

    def _render_custom(self):
        return format_html('<span class="badge bg-{}">{}</span>', self.variant, self.text)

badge = Badge("<script>alert(1)</script>", "danger")
assert "&lt;script&gt;" in badge.render()
```

**Use for**: Presentational widgets such as buttons, badges, and icons.

**Benefits**:
- Does not create a separate LiveComponent lifecycle
- Renders through the base class's available rendering path
- Can be reused wherever a component value can be rendered

### LiveComponent (Stateful, Interactive)

`LiveComponent` supports initialization, parent communication, and rerendering
inside a parent LiveView. The parent LiveView remains responsible for the
connection and page-level rendering:

```python
from djust import LiveComponent, event_handler

class TodoList(LiveComponent):
    template = """
        <ul>
        {% for item in items %}
            <li>
                <input type="checkbox" dj-change="toggle" data-id="{{ item.id }}">
                {{ item.text }}
            </li>
        {% endfor %}
        </ul>
    """

    def mount(self, items=None):
        self.items = items or []

    @event_handler()
    def toggle(self, id: str = "", **kwargs):
        item = next(i for i in self.items if i['id'] == int(id))
        item['completed'] = not item['completed']
        self.send_parent("todo_toggled", {"id": int(id)})
```

**Use for**: Forms, data tables, filters, tabs - anything with state and user interaction.

**Benefits**:
- Component state and lifecycle hooks
- Event handling when methods are authorized with `@event_handler()` under
  strict event security
- Parent communication through `send_parent()`

### Quick Reference: When to Use Which?

| Scenario | Use This | Why |
|----------|----------|-----|
| Display a status badge | `Component` | No state, just renders |
| Show user profile card | `Component` | Just displays data |
| Render a button | `Component` | Simple, stateless |
| Todo list with filters | `LiveComponent` | Has state + interaction |
| Data table with sorting | `LiveComponent` | Complex state management |
| Multi-step form wizard | `LiveComponent` | Multiple states + navigation |
| Tabs that preserve state | `LiveComponent` | State persists across tab switches |

Start with template markup when that is sufficient. Use `Component` for a
reusable presentational unit and `LiveComponent` when a child needs its own
stateful lifecycle or parent communication.

### Rendering Paths

`Component.render()` tries the component's `_rust_impl_class` when present,
then renders its `template` when defined, or calls `_render_custom()` as the
Python fallback. Template rendering tries djust's Rust template engine and
can fall back to Django templates. These are implementation details, not
component-wide performance guarantees. The
compiled extension may not expose every Rust class mentioned in older design
documents; see [Rust Components](RUST_COMPONENTS.md) for current availability.

```python
from djust.components.ui import Button

button = Button("Save", variant="primary")
html = button.render()
```

The supported Python components use these paths differently. Do not infer
that a component has a native Rust implementation from its base class or from
an older benchmark; check the component's availability notes first.

The [unified design](COMPONENT_UNIFIED_DESIGN.md) is a historical proposal,
not the current component contract. Use the API reference and the individual
component pages for supported behavior.

### Documentation Guide

For detailed information:

- **[COMPONENT_UNIFIED_DESIGN.md](COMPONENT_UNIFIED_DESIGN.md)** - Historical architecture proposal; not a current API reference.

- **[LIVECOMPONENT_ARCHITECTURE.md](LIVECOMPONENT_ARCHITECTURE.md)** - Historical internal architecture notes; consult the API reference for current behavior.

- **[API_REFERENCE_COMPONENTS.md](API_REFERENCE_COMPONENTS.md)** - API documentation
  - Complete `Component` API
  - Complete `LiveComponent` API
  - Lifecycle methods and event handling

- **[COMPONENT_BEST_PRACTICES.md](COMPONENT_BEST_PRACTICES.md)** - Best practices guide
  - Decision matrix for choosing component types
  - Common patterns and anti-patterns
  - Performance optimization
  - When to upgrade from simple to complex

- **[COMPONENT_MIGRATION_GUIDE.md](COMPONENT_MIGRATION_GUIDE.md)** - Migration guide
  - How to migrate existing components
  - Step-by-step refactoring patterns
  - Identifying component types

- **[COMPONENT_EXAMPLES.md](COMPONENT_EXAMPLES.md)** - Complete examples
  - Full Todo app with filtering
  - User management dashboard
  - E-commerce product browser
  - Real-world patterns

- **[COMPONENT_PERFORMANCE_OPTIMIZATION.md](COMPONENT_PERFORMANCE_OPTIMIZATION.md)** - Performance guide
  - Historical performance and design notes; figures are not current guarantees.

## Quick Start

### 1. Configure Your CSS Framework

In `settings.py`:

```python
LIVEVIEW_CONFIG = {
    'css_framework': 'bootstrap5',  # 'bootstrap4', 'tailwind', or None for plain form markup
}
```

### 2. Use Components in Views

```python
from djust import LiveView
from djust.components.layout import NavbarComponent, NavItem

class MyView(LiveView):
    def mount(self, request):
        self.navbar = NavbarComponent(
            brand_name="My App",
            brand_logo="/static/images/logo.png",
            items=[
                NavItem("Home", "/", active=True),
                NavItem("About", "/about/"),
                NavItem("Contact", "/contact/"),
            ],
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['navbar'] = self.navbar
        return context
```

### 3. Render in Templates

```html
{{ navbar.render }}
```

`NavbarComponent` selects its Bootstrap, Tailwind, or plain renderer using the
configured CSS framework. For this component, set `css_framework` to the
explicit string `"plain"` to select the plain renderer: its lookup treats a
stored `None` as the default Bootstrap value. This behavior is specific to
this component; it is not a guarantee for every component in the package.

## Available Components

The `djust.components.ui` package exports stateless widgets such as `Button`,
`Badge`, `Radio`, `NavBar`, `Table`, and `Pagination`, along with selected
stateful `*Component` variants. The exact export list is maintained in
[`python/djust/components/ui/__init__.py`](../../python/djust/components/ui/__init__.py).
Layout components such as `NavbarComponent` and `TabsComponent` are exported
from `djust.components.layout`. See the per-component pages for constructor
arguments and behavior.

## Framework Adapters for Django Forms

`djust.frameworks` supplies adapters used by djust's Django form rendering.
The built-in choices include Bootstrap 4, Bootstrap 5, Tailwind, and plain
markup. UI components may use their own framework-specific rendering logic;
they do not all go through these adapters.

### Example Navbar Markup

The following snippets illustrate the kinds of classes each
`NavbarComponent` renderer emits; exact attributes and whitespace can vary with
component options. These are not outputs from the Django form adapters.

#### Bootstrap 5

```html
<nav class="navbar navbar-expand-lg navbar-custom fixed-top">
  <div class="container">
    <a class="navbar-brand" href="/">
      <img src="/static/logo.png" height="16">
      My App
    </a>
    <ul class="navbar-nav ms-auto">
      <li class="nav-item">
        <a class="nav-link active" href="/">Home</a>
      </li>
    </ul>
  </div>
</nav>
```

#### Tailwind

```html
<nav class="bg-white border-b border-gray-200 shadow-sm fixed top-0 left-0 right-0 z-50">
  <div class="container mx-auto px-4">
    <div class="flex items-center justify-between h-16">
      <a href="/" class="flex items-center">
        <img src="/static/logo.png" class="h-4 w-auto mr-2">
        <span class="text-xl font-bold">My App</span>
      </a>
      <div class="flex items-center space-x-1">
        <a href="/" class="px-3 py-2 text-blue-600 font-semibold">Home</a>
      </div>
    </div>
  </div>
</nav>
```

#### Plain

```html
<nav class="navbar navbar-fixed">
  <div class="navbar-container">
    <a href="/" class="navbar-brand">
      <img src="/static/logo.png" class="navbar-logo">
      <span class="navbar-brand-text">My App</span>
    </a>
    <ul class="navbar-nav">
      <li class="nav-item active">
        <a href="/">Home</a>
      </li>
    </ul>
  </div>
</nav>
```

## Creating Custom Components

### Simple Component (Stateless)

For presentational components without state:

```python
from django.utils.html import format_html
from djust.components.base import Component

class StatusBadge(Component):
    """A simple status badge component"""

    def __init__(self, status: str, label: str = None):
        super().__init__(status=status, label=label or status.title())

    def _render_custom(self) -> str:
        """Render markup safely; add framework-specific branches if needed."""
        return format_html(
            '<span class="status-badge status-{}">{}</span>',
            self.status,
            self.label,
        )
```

**Usage**:
```python
# In your view
badge = StatusBadge('success', 'Active')

# In template
{{ badge }}
```

### LiveComponent (Stateful)

For interactive components with state:

```python
from djust import LiveComponent, event_handler

class FilterWidget(LiveComponent):
    """A filterable list component with state"""

    template = """
        <div class="filter-widget">
            <input
                type="text"
                dj-input="on_search"
                value="{{ search_query }}"
                placeholder="Search..."
            />
            <select dj-change="on_category_change">
                <option value="">All Categories</option>
                {% for cat in categories %}
                <option value="{{ cat }}" {% if cat == selected_category %}selected{% endif %}>
                    {{ cat }}
                </option>
                {% endfor %}
            </select>
            <div class="results">
                <p>{{ filtered_count }} results</p>
            </div>
        </div>
    """

    def mount(self, items=None, categories=None):
        """Initialize component state"""
        self.items = items or []
        self.categories = categories or []
        self.search_query = ""
        self.selected_category = ""

    def update(self, items=None, **props):
        """Called when parent updates props"""
        if items is not None:
            self.items = items

    @event_handler()
    def on_search(self, value: str = "", **kwargs):
        """Handle search input"""
        self.search_query = value
        # Notify parent of filter change
        self.send_parent("filter_changed", self._get_filter_state())

    @event_handler()
    def on_category_change(self, value: str = "", **kwargs):
        """Handle category selection"""
        self.selected_category = value
        self.send_parent("filter_changed", self._get_filter_state())

    def get_context_data(self):
        """Return template context"""
        return {
            'search_query': self.search_query,
            'categories': self.categories,
            'selected_category': self.selected_category,
            'filtered_count': self._count_filtered(),
        }

    def _count_filtered(self) -> int:
        """Count items matching current filters"""
        items = self.items
        if self.search_query:
            items = [i for i in items if self.search_query.lower() in i['name'].lower()]
        if self.selected_category:
            items = [i for i in items if i.get('category') == self.selected_category]
        return len(items)

    def _get_filter_state(self) -> dict:
        """Get current filter state for parent"""
        return {
            'search_query': self.search_query,
            'selected_category': self.selected_category,
        }
```

**Usage in Parent View**:
```python
class ProductListView(LiveView):
    def mount(self, request):
        self.products = self._load_products()
        self.filter_widget = FilterWidget(
            items=self.products,
            categories=['Electronics', 'Books', 'Clothing']
        )

    def handle_component_event(self, component_id: str, event: str, data: dict):
        """Handle events from child components"""
        if event == "filter_changed":
            # Update filtered products
            self.filtered_products = self._apply_filters(
                self.products,
                data['search_query'],
                data['selected_category']
            )
```

## Example: Navbar Component

`NavbarComponent` is a stateful layout component with Bootstrap, Tailwind, and
plain HTML/CSS renderers. Its navigation items are ordinary links.

### Basic Usage

```python
from djust.components.layout import NavbarComponent, NavItem

navbar = NavbarComponent(
    brand_name="djust",
    brand_logo="/static/images/djust.png",
    brand_href="/",
    items=[
        NavItem("Home", "/", active=True),
        NavItem("Demos", "/demos/"),
        NavItem("Forms", "/forms/"),
        NavItem("Docs", "/docs/"),
        NavItem("Hosting ↗", "https://djustlive.com", external=True),
    ],
    fixed_top=True,
    logo_height=16,
)
```

### With Icons

```python
items=[
    NavItem("Home", "/", icon="🏠"),
    NavItem("Settings", "/settings/", icon="⚙️"),
    NavItem("Help", "/help/", icon="❓"),
]
```

### Dynamic Updates

```python
from djust import LiveView, event_handler
from djust.components.layout import NavbarComponent, NavItem

class MyView(LiveView):
    def mount(self, request):
        self.navbar = NavbarComponent(...)

    @event_handler()
    def add_menu_item(self):
        """Event handler to add menu items"""
        self.navbar.add_item(NavItem("New Page", "/new/"))

    @event_handler()
    def set_active_page(self, href: str):
        """Event handler to change active page"""
        self.navbar.set_active(href)
```

## Best Practices

### 1. Start Simple, Upgrade When Needed

```python
from djust import LiveView, LiveComponent, event_handler
from djust.components.base import Component
from django.utils.html import format_html
# ✅ Start with inline template
class SimpleView(LiveView):
    template = '<button dj-click="increment">{{ count }}</button>'

    def mount(self, request):
        self.count = 0

    @event_handler()
    def increment(self):
        self.count += 1

# ✅ Upgrade to Component when you need reusability
class CounterButton(Component):
    def __init__(self, count):
        super().__init__(count=count)

    def _render_custom(self):
        return format_html('<button>Count: {}</button>', self.count)

# ✅ Upgrade to LiveComponent when you need state + interactivity
class CounterWidget(LiveComponent):
    template = '<button dj-click="increment">{{ count }}</button>'

    def mount(self, initial_count=0):
        self.count = initial_count

    @event_handler()
    def increment(self):
        self.count += 1
```

### 2. Component Communication: Props Down, Events Up

```python
# Parent view coordinates child components
class DashboardView(LiveView):
    def mount(self, request):
        self.users = User.objects.all()
        self.selected_user = None

        # Create child components
        self.user_list = UserListComponent(users=self.users)
        self.user_detail = UserDetailComponent(user=None)

    def handle_component_event(self, component_id: str, event: str, data: dict):
        """Handle events from child components"""
        if event == "user_selected":
            # Update state
            self.selected_user = User.objects.get(id=data['user_id'])
            # Update child component props
            self.user_detail.update(user=self.selected_user)
```

### 3. Use Simple Components for Presentation

```python
from django.utils.html import format_html
# ✅ Good: Simple component for status display
class StatusBadge(Component):
    def __init__(self, status):
        super().__init__(status=status)

    def _render_custom(self):
        colors = {'active': 'green', 'pending': 'yellow', 'inactive': 'red'}
        color = colors.get(self.status, 'gray')
        return format_html('<span class="badge bg-{}">{}</span>', color, self.status)

# ❌ Bad: LiveComponent for simple presentation
class StatusBadge(LiveComponent):  # Unnecessary overhead!
    def mount(self, status):
        self.status = status
    # ... unnecessary VDOM tree, WebSocket connection
```

### 4. Create a Base View with Common Components

```python
class BaseView(LiveView):
    """Base view with common components"""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Use simple Component for navbar (presentational)
        context['navbar'] = NavbarComponent(
            brand_name="My App",
            items=self.get_nav_items(),
        )

        return context

    def get_nav_items(self):
        """Override in child views to customize navbar"""
        return [
            NavItem("Home", "/", active=self.is_active("/")),
            NavItem("About", "/about/", active=self.is_active("/about/")),
        ]

    def is_active(self, path: str) -> bool:
        return self.request.path.startswith(path)


# Child view inherits navbar automatically
class HomeView(BaseView):
    template_name = 'home.html'
```

### 5. Customize Components for Your Project

For application-specific markup, subclass or wrap the public component API.
Copying package internals creates a fork that you must maintain yourself.

1. Subclass the public component or write an application component.
2. Override or add rendering behavior supported by that component.
3. Keep user-controlled content escaped; prefer Django's `format_html()`.

```python
# my_app/components/navbar.py
from djust.components.layout import NavbarComponent as BaseNavbar

class MyCustomNavbar(BaseNavbar):
    def _render_bootstrap(self):
        """Custom Bootstrap rendering with my styles"""
        # Your custom implementation
        return '...'
```

### 6. Use Configuration for Global Settings

```python
# settings.py
LIVEVIEW_CONFIG = {
    'css_framework': 'tailwind',
    'tailwind': {
        'field_class': 'mt-1 block w-full rounded-md border-gray-300',
        'error_class': 'mt-2 text-sm text-red-600',
    },
}
```

## Comparison with Other Approaches

### Traditional Django (Template-Based)

```html
<!-- navbar.html - tightly coupled to Bootstrap -->
<nav class="navbar navbar-expand-lg">
  <div class="container">
    <a class="navbar-brand" href="/">{{ brand_name }}</a>
    {% for item in nav_items %}
      <li class="nav-item">
        <a class="nav-link" href="{{ item.href }}">{{ item.label }}</a>
      </li>
    {% endfor %}
  </div>
</nav>
```

This is a valid approach when a template is the simplest fit. The example
uses Bootstrap classes, so changing CSS frameworks means changing its markup
or stylesheet.

### shadcn/ui (React)

```tsx
// navbar.tsx - framework-agnostic component
export function Navbar({ items }) {
  return (
    <nav className="flex items-center justify-between">
      {items.map(item => (
        <a href={item.href}>{item.label}</a>
      ))}
    </nav>
  )
}
```

This React comparison is illustrative only; it is not a djust component API.

### djust Components (Python)

Use `Component` for a presentational unit and `LiveComponent` when a child
needs its own stateful lifecycle or parent communication. Components render
on the server; CSS framework support depends on each component's implementation.

## Switching CSS Frameworks

Want to switch from Bootstrap to Tailwind? Just change one setting:

```python
# settings.py
LIVEVIEW_CONFIG = {
    'css_framework': 'tailwind',  # Changed from 'bootstrap5'
}
```

Components with framework-aware renderers read this setting at render time;
support differs by component. Check that component's API page before expecting
the selected framework to change its output. Django form rendering uses the
configured framework adapter. Use `None` for plain form markup; the literal
string `"plain"` is not a documented `css_framework` setting.

## Advanced: Custom Framework Adapters

The adapter registry is specifically for Django form fields. A custom adapter
subclasses `djust.frameworks.FrameworkAdapter`, implements `render_field()`,
`render_errors()`, and `get_field_class()`, then registers an instance with
`register_adapter(name, adapter)`. The configured name selects it through
`get_adapter()`. Its returned HTML is marked safe by form rendering, so the
adapter must escape every dynamic value. See
[`python/djust/frameworks.py`](../../python/djust/frameworks.py) and
[`djust.forms`](../../python/djust/forms.py) for the complete contract.

This registry does not select markup for arbitrary UI components. A custom
component must implement its own rendering behavior.

## Component Library

The examples below are selected components, not an exhaustive API list:

- [NavbarComponent](../../python/djust/components/layout/navbar.py) — navigation bar
- [ButtonComponent](../../python/djust/components/ui/button.py) — stateful button
- [Button](../../python/djust/components/ui/button_simple.py) — stateless button
- [TableComponent](../../python/djust/components/data/table.py) — stateful data table
- [TabsComponent](../../python/djust/components/layout/tabs.py) — stateful tabs

See `examples/demo_project/demo_app/views/kitchen_sink.py` for live examples.

## Contributing Components

Want to add a new component? Follow these steps:

1. Add the component under the appropriate package in `python/djust/components/`.
2. Implement only the rendering paths and framework support the component needs.
3. Add to `__init__.py` exports
4. Create example in `examples/demo_project/`
5. Add documentation

Components should:
- ✅ Document which CSS frameworks, if any, they support
- ✅ Be fully typed with type hints
- ✅ Include comprehensive docstrings
- ✅ Have examples in the demo project
- ✅ Follow the existing component patterns

## Learn More

- [CLAUDE.md](../../CLAUDE.md) - Development guide
- [`examples/demo_project/`](../../examples/demo_project/) — examples in the demo project
- [`python/djust/components/`](../../python/djust/components/) — component source code

---

Components are rendered on the server and can be used with djust's LiveView
integration. Styling and framework support vary by component.
