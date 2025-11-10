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

You can create your own components following the same pattern:

```python
from djust.components.base import LiveComponent
from djust.config import config

class MyCustomComponent(LiveComponent):
    """A custom component that adapts to your CSS framework"""

    def mount(self, **kwargs):
        """Initialize component state"""
        self.title = kwargs.get('title', 'Default Title')
        self.content = kwargs.get('content', '')

    def get_context(self):
        """Return context for rendering"""
        return {
            'title': self.title,
            'content': self.content,
        }

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
        """Bootstrap 5 rendering"""
        return f'''
        <div class="card">
            <div class="card-header">
                <h3>{self.title}</h3>
            </div>
            <div class="card-body">
                <p>{self.content}</p>
            </div>
        </div>
        '''

    def _render_tailwind(self) -> str:
        """Tailwind CSS rendering"""
        return f'''
        <div class="rounded-lg border border-gray-200 bg-white">
            <div class="border-b border-gray-200 px-4 py-3">
                <h3 class="text-lg font-semibold">{self.title}</h3>
            </div>
            <div class="px-4 py-3">
                <p class="text-gray-700">{self.content}</p>
            </div>
        </div>
        '''

    def _render_plain(self) -> str:
        """Plain HTML rendering"""
        return f'''
        <div class="custom-card">
            <div class="custom-card-header">
                <h3>{self.title}</h3>
            </div>
            <div class="custom-card-body">
                <p>{self.content}</p>
            </div>
        </div>
        '''
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

### 1. Create a Base View with Common Components

```python
class BaseView(LiveView):
    """Base view with common components"""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Navbar available to all child views
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

### 2. Customize Components for Your Project

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

### 3. Use Configuration for Global Settings

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
# navbar.py - framework-agnostic, server-side
class NavbarComponent(LiveComponent):
    def render(self):
        framework = config.get('css_framework')
        if framework == 'bootstrap5':
            return self._render_bootstrap()
        elif framework == 'tailwind':
            return self._render_tailwind()
```

✅ Framework-agnostic
✅ Own the code
✅ Server-side (no build step)
✅ Full Python/Django integration
✅ Reactive with LiveView

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
