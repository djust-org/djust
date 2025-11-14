# djust Components - shadcn/ui Style Architecture

djust follows a **component-as-code** approach similar to shadcn/ui, where components are:

1. **Style-Independent**: Work with Bootstrap, Tailwind, or plain CSS
2. **Copy-Paste Ready**: Own the code, customize as needed
3. **Framework-Agnostic**: Adapt to your CSS framework automatically
4. **Type-Safe**: Full Python type hints and IDE support
5. **Server-Side**: No JavaScript framework required

## Philosophy

Unlike npm packages that you install and configure, djust components are **code you own**. Similar to shadcn/ui's approach:

> "Copy the component code into your project and customize it to your needs. You own the code."

Components automatically adapt to your configured CSS framework through the **Framework Adapter** pattern.

## Two-Tier Component System

djust provides two types of components, optimized for different use cases:

### Component (Stateless, Presentational)

**Simple components** for rendering UI without state or interactivity:

```python
from djust.components.base import Component

class Badge(Component):
    def __init__(self, text, variant="primary"):
        self.text = text
        self.variant = variant

    def render(self):
        return f'<span class="badge bg-{self.variant}">{self.text}</span>'
```

**Use for**: Buttons, badges, icons, cards - anything that just displays data.

**Benefits**:
- Zero overhead - just a render() method
- No WebSocket connection or VDOM tree
- Perfect for simple, reusable UI elements
- Fast and lightweight

### LiveComponent (Stateful, Interactive)

**Smart components** with their own state, lifecycle, and reactivity:

```python
from djust import LiveComponent

class TodoList(LiveComponent):
    template_string = """
        <ul>
        {% for item in items %}
            <li>
                <input type="checkbox" @change="toggle" data-id="{{ item.id }}">
                {{ item.text }}
            </li>
        {% endfor %}
        </ul>
    """

    def mount(self, items=None):
        self.items = items or []

    def toggle(self, id: str = None):
        item = next(i for i in self.items if i['id'] == int(id))
        item['completed'] = not item['completed']
        self.send_parent("todo_toggled", {"id": int(id)})
```

**Use for**: Forms, data tables, filters, tabs - anything with state and user interaction.

**Benefits**:
- Own VDOM tree with efficient updates
- Event handlers with automatic routing
- Props/events communication with parent
- Lifecycle methods (mount/update/unmount)

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

**Golden Rule**: Start with the simplest pattern (inline template or `Component`), upgrade to `LiveComponent` only when you need state and interactivity.

### Performance Optimization

Components feature **automatic performance optimization** - the framework automatically chooses the fastest available implementation:

**Unified Component Design:**

```python
from djust.components.ui import Badge

# Same simple code, regardless of implementation
badge = Badge("New", variant="primary")
html = badge.render()

# Behind the scenes, Component base class chooses:
# 1. Pure Rust (if available) → ~1μs per render (fastest)
# 2. Hybrid template_string → ~5μs per render (fast)
# 3. Python render() → ~50μs per render (flexible)
```

**Core components in Rust** (automatically optimized):
- Badge, Button, Icon, Spinner - used frequently, implemented in Rust
- 50-100x faster than pure Python
- Transparent to developers - same API

**Custom components use hybrid** (when Rust not available):
- `template_string` for Rust template rendering (10x faster than Python)
- `_render_custom()` for full Python control (maximum flexibility)

**Example with automatic optimization:**

```python
class StatusBadge(Component):
    # Links to Rust implementation if available
    _rust_impl_class = RustBadge  # Automatically used if Rust built

    # Fallback: hybrid rendering
    template_string = '<span class="badge bg-{{ variant }}">{{ text }}</span>'

    def get_context_data(self):
        return {'text': self.text, 'variant': self.variant}
```

See **[COMPONENT_UNIFIED_DESIGN.md](COMPONENT_UNIFIED_DESIGN.md)** for the complete unified design with automatic Rust optimization.

### Documentation Guide

For detailed information:

- **[COMPONENT_UNIFIED_DESIGN.md](COMPONENT_UNIFIED_DESIGN.md)** - ⭐ **Start here!**
  - Unified Component design with automatic Rust optimization
  - Performance waterfall (Rust → Hybrid → Python)
  - Core Rust component library (Badge, Button, Icon, etc.)
  - Single API with transparent optimization

- **[LIVECOMPONENT_ARCHITECTURE.md](LIVECOMPONENT_ARCHITECTURE.md)** - Complete architecture guide
  - How the two-tier system works
  - VDOM patching with separate trees
  - Component lifecycle
  - Performance characteristics

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
  - Three-tier performance spectrum (Python → Hybrid → Rust)
  - Optional `template_string` for Rust rendering
  - Pure Rust components via PyO3
  - Benchmarks and migration paths

## Quick Start

### 1. Configure Your CSS Framework

In `settings.py`:

```python
DJUST = {
    'css_framework': 'bootstrap5',  # or 'tailwind', 'plain'
}
```

### 2. Use Components in Views

```python
from djust import LiveView
from djust.components.layout import NavbarComponent, NavItem

class MyView(LiveView):
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Create navbar - automatically adapts to your framework
        context['navbar'] = NavbarComponent(
            brand_name="My App",
            brand_logo="/static/images/logo.png",
            items=[
                NavItem("Home", "/", active=True),
                NavItem("About", "/about/"),
                NavItem("Contact", "/contact/"),
            ],
        )

        return context
```

### 3. Render in Templates

```html
{{ navbar.render }}
```

That's it! The navbar will automatically render with Bootstrap, Tailwind, or plain CSS based on your configuration.

## Available Components

### UI Components
- **Button** - Buttons with variants (primary, secondary, danger, etc.)
- **Card** - Content cards with header, body, footer
- **Badge** - Labels and tags
- **Alert** - Dismissible alerts with variants
- **Modal** - Dialog/modal windows
- **Dropdown** - Dropdown menus
- **Progress** - Progress bars
- **Spinner** - Loading indicators

### Layout Components
- **Navbar** - Navigation bar (NEW!)
- **Tabs** - Tab navigation

### Data Components
- **Table** - Data tables with sorting/filtering
- **Pagination** - Pagination controls

### Form Components
- Integrated with Django Forms
- Auto-rendered with your CSS framework

## How It Works: Framework Adapters

Each component uses the **Framework Adapter** pattern to render framework-specific HTML:

```python
class NavbarComponent(LiveComponent):
    def render(self) -> str:
        framework = config.get('css_framework', 'bootstrap5')

        if framework == 'bootstrap5':
            return self._render_bootstrap()
        elif framework == 'tailwind':
            return self._render_tailwind()
        else:
            return self._render_plain()
```

### Bootstrap 5 Output

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

### Tailwind Output (Same Component!)

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

### Plain HTML Output (Same Component!)

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
from djust.components.base import Component
from djust.config import config

class StatusBadge(Component):
    """A simple status badge component"""

    def __init__(self, status: str, label: str = None):
        self.status = status
        self.label = label or status.title()

    def render(self) -> str:
        """Render with framework-specific styling"""
        from django.utils.safestring import mark_safe

        framework = config.get('css_framework', 'bootstrap5')

        if framework == 'bootstrap5':
            return mark_safe(self._render_bootstrap())
        elif framework == 'tailwind':
            return mark_safe(self._render_tailwind())
        else:
            return mark_safe(self._render_plain())

    def _render_bootstrap(self) -> str:
        variants = {
            'success': 'success',
            'error': 'danger',
            'warning': 'warning',
            'info': 'info',
        }
        variant = variants.get(self.status, 'secondary')
        return f'<span class="badge bg-{variant}">{self.label}</span>'

    def _render_tailwind(self) -> str:
        colors = {
            'success': 'bg-green-100 text-green-800',
            'error': 'bg-red-100 text-red-800',
            'warning': 'bg-yellow-100 text-yellow-800',
            'info': 'bg-blue-100 text-blue-800',
        }
        classes = colors.get(self.status, 'bg-gray-100 text-gray-800')
        return f'<span class="px-2 py-1 rounded text-xs font-semibold {classes}">{self.label}</span>'

    def _render_plain(self) -> str:
        return f'<span class="badge badge-{self.status}">{self.label}</span>'
```

**Usage**:
```python
# In your view
badge = StatusBadge('success', 'Active')

# In template
{{ badge.render }}
```

### LiveComponent (Stateful)

For interactive components with state:

```python
from djust import LiveComponent
from djust.config import config

class FilterWidget(LiveComponent):
    """A filterable list component with state"""

    template_string = """
        <div class="filter-widget">
            <input
                type="text"
                @input="on_search"
                value="{{ search_query }}"
                placeholder="Search..."
            />
            <select @change="on_category_change">
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

    def on_search(self, value: str = "", **kwargs):
        """Handle search input"""
        self.search_query = value
        # Notify parent of filter change
        self.send_parent("filter_changed", self._get_filter_state())

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

The `NavbarComponent` is a perfect example of the shadcn approach. Here's how to use it:

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
        NavItem("Components", "/kitchen-sink/"),
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
class MyView(LiveView):
    def mount(self, request):
        self.navbar = NavbarComponent(...)

    def add_menu_item(self):
        """Event handler to add menu items"""
        self.navbar.add_item(NavItem("New Page", "/new/"))

    def set_active_page(self, href: str):
        """Event handler to change active page"""
        self.navbar.set_active(href)
```

## Best Practices

### 1. Start Simple, Upgrade When Needed

```python
# ✅ Start with inline template
class SimpleView(LiveView):
    template_string = '<button @click="increment">{{ count }}</button>'

    def mount(self, request):
        self.count = 0

    def increment(self):
        self.count += 1

# ✅ Upgrade to Component when you need reusability
class CounterButton(Component):
    def __init__(self, count):
        self.count = count

    def render(self):
        return f'<button>Count: {self.count}</button>'

# ✅ Upgrade to LiveComponent when you need state + interactivity
class CounterWidget(LiveComponent):
    template_string = '<button @click="increment">{{ count }}</button>'

    def mount(self, initial_count=0):
        self.count = initial_count

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
# ✅ Good: Simple component for status display
class StatusBadge(Component):
    def __init__(self, status):
        self.status = status

    def render(self):
        colors = {'active': 'green', 'pending': 'yellow', 'inactive': 'red'}
        color = colors.get(self.status, 'gray')
        return f'<span class="badge bg-{color}">{self.status}</span>'

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

Don't like how a component renders? **Copy the code and modify it!** That's the whole point:

1. Copy `python/djust/components/layout/navbar.py` to your project
2. Modify the rendering methods
3. Use your custom version

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
DJUST = {
    'css_framework': 'tailwind',

    # Framework-specific class mappings
    'framework_classes': {
        'tailwind': {
            'field_class': 'mt-1 block w-full rounded-md border-gray-300',
            'error_class': 'mt-2 text-sm text-red-600',
            # ... more classes
        }
    }
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

❌ Tied to one CSS framework
❌ Hard to switch frameworks
❌ Logic scattered across templates and views

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

✅ Framework-agnostic
✅ Own the code
❌ Client-side only (React)
❌ Requires build step

### djust Components (Python)

```python
# Simple Component (stateless, for presentation)
class StatusBadge(Component):
    def __init__(self, status):
        self.status = status

    def render(self):
        framework = config.get('css_framework')
        if framework == 'bootstrap5':
            return self._render_bootstrap()
        # ... other frameworks

# LiveComponent (stateful, for interactivity)
class FilterWidget(LiveComponent):
    template_string = """
        <input @input="on_search" value="{{ query }}" />
        <p>{{ results_count }} results</p>
    """

    def mount(self, items):
        self.items = items
        self.query = ""

    def on_search(self, value=""):
        self.query = value
        self.send_parent("filter_changed", {"query": value})
```

✅ Framework-agnostic
✅ Own the code
✅ Server-side (no build step)
✅ Full Python/Django integration
✅ Reactive with LiveView
✅ Two-tier system: simple for presentation, powerful for interactivity

## Switching CSS Frameworks

Want to switch from Bootstrap to Tailwind? Just change one setting:

```python
# settings.py
DJUST = {
    'css_framework': 'tailwind',  # Changed from 'bootstrap5'
}
```

All components automatically adapt! No template changes needed.

## Advanced: Custom Framework Adapters

You can even create adapters for other CSS frameworks:

```python
from djust.frameworks import FrameworkAdapter, register_adapter

class BulmaAdapter(FrameworkAdapter):
    """Bulma CSS framework adapter"""

    def render_field(self, field, field_name, value, errors, **kwargs):
        # Bulma-specific rendering
        return f'<div class="field">...</div>'

    def render_errors(self, errors, **kwargs):
        return f'<p class="help is-danger">{errors[0]}</p>'

# Register your adapter
register_adapter('bulma', BulmaAdapter())
```

Then use it:

```python
DJUST = {
    'css_framework': 'bulma',
}
```

## Component Library

All available components:

- [NavbarComponent](python/djust/components/layout/navbar.py) - Navigation bar
- [ButtonComponent](python/djust/components/ui/button.py) - Buttons
- [CardComponent](python/djust/components/ui/card.py) - Content cards
- [AlertComponent](python/djust/components/ui/alert.py) - Alerts
- [ModalComponent](python/djust/components/ui/modal.py) - Modals
- [TableComponent](python/djust/components/data/table.py) - Data tables
- [TabsComponent](python/djust/components/layout/tabs.py) - Tab navigation

See `examples/demo_project/demo_app/views/kitchen_sink.py` for live examples.

## Contributing Components

Want to add a new component? Follow these steps:

1. Create the component file: `python/djust/components/<category>/<name>.py`
2. Implement `_render_bootstrap()`, `_render_tailwind()`, `_render_plain()`
3. Add to `__init__.py` exports
4. Create example in `examples/demo_project/`
5. Add documentation

Components should:
- ✅ Work with all three frameworks (Bootstrap, Tailwind, plain)
- ✅ Be fully typed with type hints
- ✅ Include comprehensive docstrings
- ✅ Have examples in the demo project
- ✅ Follow the existing component patterns

## Learn More

- [CLAUDE.md](CLAUDE.md) - Development guide
- [examples/demo_project/](examples/demo_project/) - Live examples
- [python/djust/components/](python/djust/components/) - Component source code

---

**The shadcn approach applied to server-side Python/Django**: Own your components, adapt to any CSS framework, no build step required.
