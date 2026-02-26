---
title: "Components"
slug: components
section: guides
order: 4
level: beginner
description: "Build reusable UI with stateless Components and interactive LiveComponents"
---

# Components

djust provides a two-tier component system: **Component** for fast, stateless rendering and **LiveComponent** for interactive widgets with state and event handlers. Both render server-side with no JavaScript build step.

For **styling**, djust follows manifesto principle #7: *"Strong opinions on security, state, transport. Zero opinions on CSS, markup, or design."* Components output semantic HTML -- you bring your own styling via CSS custom properties, Tailwind, Bootstrap, or plain CSS. The optional [`djust-components`](#djust-components-template-tag-library) and [`djust-theming`](#djust-theming) packages provide a style-agnostic component library and design system powered by CSS custom properties.

## Two Types of Components

|               | Component                               | LiveComponent                            |
| ------------- | --------------------------------------- | ---------------------------------------- |
| **State**     | None                                    | Full lifecycle (mount/update/unmount)    |
| **Events**    | None                                    | `dj-click`, `dj-submit`, etc.            |
| **Rendering** | Rust-accelerated (~1-10us)              | Template-based (~50-100us)               |
| **Use for**   | Badges, icons, cards, status indicators | Tables with sorting, modals, tabs, forms |

Pick `Component` when you just need HTML output. Pick `LiveComponent` when the component needs to react to user interaction.

## Stateless Components

### Your First Component

```python
from djust.components.base import Component

class StatusDot(Component):
    template = '<span class="dot dot-{{ color }}"></span>'

    def __init__(self, color: str = "green"):
        super().__init__(color=color)
        self.color = color

    def get_context_data(self):
        return {"color": self.color}
```

Use it in a LiveView:

```python
class DashboardView(LiveView):
    template_name = "dashboard.html"

    def mount(self, request, **kwargs):
        self.status = StatusDot("green")

    def get_context_data(self, **kwargs):
        return {"status": self.status}
```

In the template, `{{ status }}` calls `__str__()` which calls `render()`:

```html
<div dj-root dj-view="myapp.views.DashboardView">
    <h1>Server Status: {{ status }}</h1>
</div>
```

### Performance Waterfall

Every `Component` tries three rendering strategies in order:

1. **Pure Rust** (~1us) -- If `_rust_impl_class` is set and the Rust extension is built
2. **Hybrid template** (~5-10us) -- If `template` is set, rendered by the Rust template engine with Django fallback
3. **Python `_render_custom()`** (~50-100us) -- Full flexibility, you write the HTML string

You don't pick one -- you define as many as you want and the fastest available wins:

```python
from djust.components.base import Component

try:
    from djust._rust import RustBadge
except ImportError:
    RustBadge = None

class Badge(Component):
    # Tier 1: Rust (if extension is built)
    _rust_impl_class = RustBadge

    # Tier 2: Hybrid template (Rust template engine + Django fallback)
    template = '<span class="badge bg-{{ variant }}">{{ text }}</span>'

    def __init__(self, text: str, variant: str = "primary"):
        super().__init__(text=text, variant=variant)
        self.text = text
        self.variant = variant

    def get_context_data(self):
        return {"text": self.text, "variant": self.variant}

    # Tier 3: Custom Python (if template engine fails)
    def _render_custom(self):
        return f'<span class="badge bg-{self.variant}">{self.text}</span>'
```

Most components only need a `template`. Add `_render_custom()` when you need framework-specific HTML (Bootstrap vs Tailwind). Add `_rust_impl_class` for hot-path components rendered hundreds of times per page.

### Updating a Component

Call `.update()` to change properties without recreating the instance:

```python
def toggle_status(self):
    new_color = "red" if self.status.color == "green" else "green"
    self.status.update(color=new_color)
```

### Component IDs

Components get a stable ID automatically:

```python
class MyView(LiveView):
    def mount(self, request, **kwargs):
        self.user_badge = Badge("Admin")
        # user_badge.id => "badge-user_badge"

        Badge("Admin", id="custom-id")
        # .id => "custom-id"
```

The waterfall: explicit `id=` parameter > auto-generated from attribute name > class name.

### Styling Components

Components are style-agnostic by default. Use CSS custom properties for themeable components:

```python
class Alert(Component):
    template = """
        <div class="dj-alert dj-alert--{{ type }}" role="alert">
            {{ message }}
        </div>
    """

    def __init__(self, message: str, type: str = "info"):
        super().__init__(message=message, type=type)
        self.message = message
        self.type = type

    def get_context_data(self):
        return {"message": self.message, "type": self.type}
```

Then style with CSS custom properties that adapt to any theme:

```css
.dj-alert {
    padding: var(--dj-spacing-3, 0.75rem);
    border-radius: var(--dj-radius, 8px);
    border: 1px solid var(--dj-border);
}
.dj-alert--success { background: var(--dj-success); color: white; }
.dj-alert--danger { background: var(--dj-danger); color: white; }
.dj-alert--info { background: var(--dj-info); color: white; }
```

This approach works with any styling system -- `djust-theming` presets, Tailwind, Bootstrap, or your own CSS. See [Styling & Theming](#djust-theming) below for the full design system.

> **Note:** The built-in components in `djust.components.ui` currently use `_render_custom()` with per-framework render methods (Bootstrap, Tailwind, Plain). This approach is being migrated toward CSS custom properties for true style independence. For new projects, prefer the [`djust-components`](#djust-components-template-tag-library) template tag library which is already style-agnostic.

## LiveComponents (Stateful)

### Your First LiveComponent

```python
from djust.components.base import LiveComponent

class CounterWidget(LiveComponent):
    template = """
        <div>
            <button dj-click="decrement" data-component-id="{{ component_id }}">-</button>
            <span>{{ count }}</span>
            <button dj-click="increment" data-component-id="{{ component_id }}">+</button>
        </div>
    """

    def mount(self, **kwargs):
        self.count = kwargs.get("initial", 0)

    @event_handler()
    def increment(self, **kwargs):
        self.count += 1
        self.trigger_update()

    @event_handler()
    def decrement(self, **kwargs):
        self.count -= 1
        self.trigger_update()

    def get_context_data(self):
        return {"count": self.count}
```

Key differences from `Component`:

- **`mount()`** is required -- set up initial state here
- **`get_context_data()`** is required -- return template variables
- **Event handlers** must be decorated with `@event_handler()` (same as LiveView). Wire them with `dj-click`, `dj-submit`, etc.
- **`data-component-id="{{ component_id }}"`** routes events to the right component instance
- **`trigger_update()`** tells the parent LiveView to re-render

### Using in a LiveView

```python
class DashboardView(LiveView):
    template_name = "dashboard.html"

    def mount(self, request, **kwargs):
        self.counter = CounterWidget(initial=10)

    def get_context_data(self, **kwargs):
        return {"counter": self.counter}
```

```html
<div dj-root dj-view="myapp.views.DashboardView">
    <h2>Counter</h2>
    {{ counter.render }}
</div>
```

The `component_id` is automatically set to the attribute name (`"counter"`) by the framework. No manual ID management needed.

### Lifecycle

```
__init__(**kwargs)
    └─> mount(**kwargs)      # Set initial state
        └─> _mounted = True

render()                     # Called on each parent re-render
    └─> get_context_data()   # Provide template vars

unmount()                    # Cleanup when removed
    └─> _mounted = False
```

### Parent-Child Communication

**Props down**: Pass data to components via constructor kwargs or `.update()`.

**Events up**: Components send events to their parent LiveView with `send_parent()`.

```python
class TodoItem(LiveComponent):
    template = """
        <div dj-click="toggle" data-component-id="{{ component_id }}">
            <input type="checkbox" {% if completed %}checked{% endif %}>
            {{ text }}
        </div>
    """

    def mount(self, **kwargs):
        self.text = kwargs.get("text", "")
        self.completed = kwargs.get("completed", False)
        self.todo_id = kwargs.get("todo_id")

    @event_handler()
    def toggle(self, **kwargs):
        self.completed = not self.completed
        # Notify parent
        self.send_parent("todo_toggled", {
            "id": self.todo_id,
            "completed": self.completed,
        })

    def get_context_data(self):
        return {"text": self.text, "completed": self.completed}
```

The parent LiveView handles it:

```python
class TodoView(LiveView):
    def mount(self, request, **kwargs):
        self.todos = [
            {"id": 1, "text": "Buy milk", "completed": False},
            {"id": 2, "text": "Write docs", "completed": True},
        ]

    def handle_component_event(self, component_id, event, data):
        if event == "todo_toggled":
            for todo in self.todos:
                if todo["id"] == data["id"]:
                    todo["completed"] = data["completed"]
```

### File-Based Templates

For complex HTML, use `template_name` instead of inline `template`:

```python
class UserCard(LiveComponent):
    template_name = "components/user_card.html"

    def mount(self, **kwargs):
        self.user = kwargs.get("user")
        self.show_actions = kwargs.get("show_actions", True)

    def get_context_data(self):
        return {
            "user": self.user,
            "show_actions": self.show_actions,
            "component_id": self.component_id,
        }
```

```html
<!-- templates/components/user_card.html -->
<div class="card" data-component-id="{{ component_id }}">
    <div class="card-body">
        <h5>{{ user.name }}</h5>
        <p>{{ user.email }}</p>
        {% if show_actions %}
        <button dj-click="edit_user"
                data-component-id="{{ component_id }}">Edit</button>
        {% endif %}
    </div>
</div>
```

Note: When using `template_name`, the component ID wrapper `<div data-component-id="...">` is **not** added automatically. Include `data-component-id="{{ component_id }}"` in your template root element.

## Built-in Components

djust ships with a library of ready-to-use components. All adapt to your CSS framework automatically.

### UI Components

| Component           | Type          | Description                                                                                                                                                                                                                         |
| ------------------- | ------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AlertComponent`    | LiveComponent | Dismissible alerts with `.show()` / `.dismiss()`                                                                                                                                                                                    |
| `BadgeComponent`    | LiveComponent | Interactive badges                                                                                                                                                                                                                  |
| `Badge`             | Component     | Stateless badges (faster)                                                                                                                                                                                                           |
| `ButtonComponent`   | LiveComponent | Buttons with disable/enable                                                                                                                                                                                                         |
| `Button`            | Component     | Stateless buttons                                                                                                                                                                                                                   |
| `CardComponent`     | LiveComponent | Cards with dynamic content                                                                                                                                                                                                          |
| `Card`              | Component     | Stateless cards                                                                                                                                                                                                                     |
| `DropdownComponent` | LiveComponent | Dropdown menus                                                                                                                                                                                                                      |
| `ModalComponent`    | LiveComponent | Show/hide modals programmatically                                                                                                                                                                                                   |
| `ProgressComponent` | LiveComponent | Progress bars with `.set_value()` / `.increment()`                                                                                                                                                                                  |
| `SpinnerComponent`  | LiveComponent | Loading indicators with `.show()` / `.hide()`                                                                                                                                                                                       |
| More stateless:     | Component     | `Accordion`, `Avatar`, `Breadcrumb`, `ButtonGroup`, `Checkbox`, `Divider`, `Icon`, `Input`, `ListGroup`, `NavBar`, `Offcanvas`, `Pagination`, `Radio`, `Range`, `Select`, `Switch`, `Table`, `Tabs`, `TextArea`, `Toast`, `Tooltip` |

### Layout Components

| Component         | Type          | Description                              |
| ----------------- | ------------- | ---------------------------------------- |
| `TabsComponent`   | LiveComponent | Tabbed navigation with `.activate_tab()` |
| `NavbarComponent` | LiveComponent | Navigation bars with `.set_active()`     |

### Data Components

| Component             | Type          | Description                            |
| --------------------- | ------------- | -------------------------------------- |
| `TableComponent`      | LiveComponent | Sortable data tables with `.sort_by()` |
| `PaginationComponent` | LiveComponent | Page navigation                        |

### Form Components

| Component          | Type          | Description                                      |
| ------------------ | ------------- | ------------------------------------------------ |
| `ForeignKeySelect` | LiveComponent | Django ForeignKey field with search/autocomplete |
| `ManyToManySelect` | LiveComponent | Django M2M field with checkboxes or multi-select |

### Usage Examples

**Alert:**

```python
def mount(self, request, **kwargs):
    self.alert = AlertComponent(
        message="Changes saved!",
        type="success",
        dismissible=True,
    )

@event_handler()
def save(self, **kwargs):
    # ... save logic ...
    self.alert.show("Changes saved!", "success")

@event_handler()
def on_error(self, **kwargs):
    self.alert.show("Something went wrong", "danger")
```

**Modal:**

```python
def mount(self, request, **kwargs):
    self.confirm_modal = ModalComponent(
        title="Confirm Delete",
        body="This action cannot be undone.",
        show=False,
        size="md",  # sm, md, lg, xl
    )

@event_handler()
def delete_clicked(self, **kwargs):
    self.confirm_modal.show()

@event_handler()
def dismiss(self, **kwargs):
    self.confirm_modal.hide()
```

**Tabs:**

```python
from djust.components.layout import TabsComponent

def mount(self, request, **kwargs):
    self.tabs = TabsComponent(
        tabs=[
            {"id": "overview", "label": "Overview", "content": "..."},
            {"id": "settings", "label": "Settings", "content": "..."},
            {"id": "logs", "label": "Logs", "content": "...", "badge": "3"},
        ],
        active="overview",
        variant="tabs",  # "tabs" or "pills"
    )
```

**Data Table:**

```python
from djust.components import TableComponent

def mount(self, request, **kwargs):
    self.users_table = TableComponent(
        columns=[
            {"key": "name", "label": "Name", "sortable": True},
            {"key": "email", "label": "Email"},
            {"key": "role", "label": "Role", "sortable": True},
        ],
        rows=list(User.objects.values("name", "email", "role")),
        striped=True,
        hoverable=True,
    )
```

**ForeignKey Select:**

```python
from djust.components.forms import ForeignKeySelect

def mount(self, request, **kwargs):
    self.category = ForeignKeySelect(
        name="category",
        queryset=Category.objects.all(),
        label_field="name",
        label="Category",
        required=True,
        searchable=True,
        search_fields=["name"],
    )
```

## Component Registry

Components can be registered by name for dynamic lookup:

```python
from djust.components import register_component, get_component, list_components

# Register a custom component
register_component("status_dot", StatusDotComponent)

# Look up by name
cls = get_component("status_dot")
dot = cls(color="green")

# List all registered components
for name, cls in list_components().items():
    print(f"{name}: {cls.__name__}")
```

All built-in LiveComponents are auto-registered: `alert`, `badge`, `button`, `card`, `dropdown`, `modal`, `progress`, `spinner`, `tabs`, `table`, `pagination`.

## Writing Custom Components

### Stateless: Extend `Component`

Use when you need fast rendering with no interaction:

```python
from djust.components.base import Component

class PriceBadge(Component):
    template = """
        <span class="price {% if on_sale %}price--sale{% endif %}">
            {% if on_sale %}<s>{{ original }}</s> {% endif %}
            {{ current }}
        </span>
    """

    def __init__(self, current: str, original: str = "", on_sale: bool = False):
        super().__init__(current=current, original=original, on_sale=on_sale)
        self.current = current
        self.original = original
        self.on_sale = on_sale

    def get_context_data(self):
        return {
            "current": self.current,
            "original": self.original,
            "on_sale": self.on_sale,
        }
```

### Stateful: Extend `LiveComponent`

Use when the component needs to handle events or manage state:

```python
from djust.components.base import LiveComponent

class SearchBox(LiveComponent):
    template = """
        <div data-component-id="{{ component_id }}">
            <input type="text" dj-input="on_search"
                   data-component-id="{{ component_id }}"
                   value="{{ query }}" placeholder="Search...">
            {% if loading %}
                <span class="spinner-border spinner-border-sm"></span>
            {% endif %}
            <ul>
            {% for result in results %}
                <li dj-click="select_result"
                    data-component-id="{{ component_id }}"
                    data-id="{{ result.id }}">
                    {{ result.name }}
                </li>
            {% endfor %}
            </ul>
        </div>
    """

    def mount(self, **kwargs):
        self.query = ""
        self.results = []
        self.loading = False
        self.search_fn = kwargs.get("search_fn")  # Callable for searching

    @event_handler()
    def on_search(self, value="", **kwargs):
        self.query = value
        if len(value) >= 2 and self.search_fn:
            self.loading = True
            self.results = self.search_fn(value)
            self.loading = False
        else:
            self.results = []
        self.trigger_update()

    @event_handler()
    def select_result(self, id=None, **kwargs):
        self.send_parent("result_selected", {"id": id})

    def get_context_data(self):
        return {
            "query": self.query,
            "results": self.results,
            "loading": self.loading,
        }
```

### Checklist

When building a custom component:

- [ ] `@event_handler()` on every method wired to a `dj-*` event
- [ ] `**kwargs` in every event handler signature
- [ ] `data-component-id="{{ component_id }}"` on every element with `dj-*` events
- [ ] `trigger_update()` after state changes that should re-render
- [ ] `send_parent()` for events the parent needs to know about
- [ ] `get_context_data()` returns all variables used in the template
- [ ] `mount()` initializes all state -- don't rely on class-level defaults for mutable objects

## Template Tips

**Inline vs file-based templates:**

- Use inline `template = "..."` for small components (< 20 lines of HTML)
- Use `template_name = "components/my_widget.html"` for complex HTML
- Inline templates get Rust-accelerated rendering. File-based templates use Django's engine.

**Avoid `{% elif %}` in inline templates** -- the Rust template engine has a known limitation. Use separate `{% if %}` blocks:

```python
# Don't:
template = '{% if size == "lg" %}big{% elif size == "sm" %}small{% endif %}'

# Do:
template = '{% if size == "lg" %}big{% endif %}{% if size == "sm" %}small{% endif %}'
```

**Components in templates render via `{{ component }}`** -- the `__str__` method calls `render()` automatically. For LiveComponents, use `{{ component.render }}` to ensure the wrapper div is included.

## djust-components (Template Tag Library)

[`djust-components`](https://github.com/djust-org/djust-components) is a separate package that provides 12 style-agnostic UI components as Django template tags. Unlike the core `djust.components` Python classes, these use CSS custom properties for all styling -- no hardcoded Bootstrap or Tailwind classes.

### Installation

```bash
pip install djust-components
```

```python
# settings.py
INSTALLED_APPS = [
    "djust_components",
    # ...
]
```

```html
<!-- base template -->
<link rel="stylesheet" href="{% static 'djust_components/components.css' %}">
```

### Usage

```html
{% load djust_components %}

{% modal id="confirm" title="Are you sure?" open=modal_open %}
  <p>This action cannot be undone.</p>
  <button dj-click="confirm_delete">Delete</button>
  <button dj-click="close_modal">Cancel</button>
{% endmodal %}

{% tabs id="settings" active=active_tab event="set_tab" %}
  {% tab id="general" label="General" %}
    General settings content
  {% endtab %}
  {% tab id="security" label="Security" icon="🔒" %}
    Security settings content
  {% endtab %}
{% endtabs %}

{% card title="Dashboard" variant="elevated" %}
  Card content here
{% endcard %}

{% badge label="Online" status="online" pulse=True %}

{% progress value=75 label="Upload" color="success" %}

{% data_table rows=rows columns=columns sort_by=sort_by sort_desc=sort_desc %}

{% toast_container toasts dismiss_event="dismiss_toast" %}
```

### Available Components

| Tag                                        | Description                          |
| ------------------------------------------ | ------------------------------------ |
| `{% modal %}`                              | Overlay dialog with backdrop blur    |
| `{% tabs %}` / `{% tab %}`                 | Content switching with active state  |
| `{% accordion %}` / `{% accordion_item %}` | Expandable sections                  |
| `{% dropdown %}`                           | Toggle menu                          |
| `{% toast_container %}`                    | Server-push notifications            |
| `{% tooltip %}`                            | Hover tooltip                        |
| `{% progress %}`                           | Animated progress bar                |
| `{% badge %}`                              | Status indicator with optional pulse |
| `{% card %}`                               | Content container                    |
| `{% data_table %}`                         | Sortable table with pagination       |
| `{% pagination %}`                         | Page navigation                      |
| `{% avatar %}`                             | User avatar with initials fallback   |

### Customization

All components use CSS custom properties. Override them to match any theme:

```css
:root {
  --dj-primary: #6366f1;
  --dj-success: #22c55e;
  --dj-warning: #eab308;
  --dj-danger: #ef4444;
  --dj-text: #e2e8f0;
  --dj-bg: #0f172a;
  --dj-bg-subtle: #1e293b;
  --dj-border: rgba(99, 102, 241, 0.15);
  --dj-radius: 8px;
}
```

This is what makes them style-agnostic: change the variables, and every component adapts. Works standalone or with `djust-theming` for full design system support.

## djust-theming

[`djust-theming`](https://github.com/djust-org/djust-theming) is a production-ready theming system inspired by shadcn/ui. It provides CSS custom properties-based theming with light/dark mode, 132 built-in theme combinations, and reactive theme switching via djust LiveViews.

### Installation

```bash
pip install djust-theming
```

```python
# settings.py
INSTALLED_APPS = [
    "djust_theming",
    # ...
]

TEMPLATES = [{
    "OPTIONS": {
        "context_processors": [
            "djust_theming.context_processors.theme_context",
        ],
    },
}]

# Choose a design system + color preset
LIVEVIEW_CONFIG = {
    "theme": {
        "theme": "material",       # Design system (11 options)
        "preset": "blue",          # Color preset (12 options)
        "default_mode": "system",  # light, dark, or system
    }
}
```

### Design Systems + Color Presets

Mix any design system with any color preset (11 x 12 = 132 combinations):

**Design systems** control typography, spacing, radius, shadows, and animations:
Material, iOS, Fluent, Minimalist, Playful, Corporate, Retro, Elegant, Neo-Brutalist, Organic, Dense

**Color presets** control the palette:
Default, Shadcn, Blue, Green, Purple, Orange, Rose, Cyberpunk, Sunset, Forest, Ocean, Metallic

```python
# Material Design + Cyberpunk colors
LIVEVIEW_CONFIG = {"theme": {"theme": "material", "preset": "cyberpunk"}}

# iOS + Forest green palette
LIVEVIEW_CONFIG = {"theme": {"theme": "ios", "preset": "forest"}}
```

### Template Integration

```html
{% load theme_tags static %}
<!DOCTYPE html>
<html>
<head>
    {% theme_head link_css=True %}
    <link href="{% static 'djust_theming/css/base.css' %}" rel="stylesheet">
</head>
<body>
    {% theme_switcher %}          {# Full theme switcher UI #}
    {% theme_mode_toggle %}       {# Light/dark toggle #}
    {% theme_preset_selector %}   {# Preset picker #}

    <div class="card">
        <div class="card-header">Dashboard</div>
        <div class="card-body">
            <button class="btn btn-primary">Get Started</button>
        </div>
    </div>
</body>
</html>
```

### Reactive Theme Switching with LiveView

```python
from djust import LiveView
from djust_theming import ThemeMixin

class DashboardView(ThemeMixin, LiveView):
    template_name = "dashboard.html"

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        # theme_head, theme_switcher, theme_preset, theme_mode
        # are automatically available in the template context
```

Theme changes happen instantly over WebSocket -- no page reload.

### shadcn/ui Compatibility

Import themes from [themes.shadcn.com](https://themes.shadcn.com) or export djust presets to share:

```bash
python manage.py djust_theme shadcn-import my-theme.json
python manage.py djust_theme shadcn-export --preset blue --output blue-theme.json
```

### Tailwind Integration

```bash
python manage.py djust_theme tailwind-config --preset blue --output tailwind.config.js
```

Then use theme colors in Tailwind classes:

```html
<button class="bg-primary text-primary-foreground hover:bg-primary/90 px-4 py-2 rounded-md">
  Primary Button
</button>
```

## Choosing a Styling Approach

| Approach                                 | When to Use                                                                                                                           |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| **`djust-components` + `djust-theming`** | New projects. Style-agnostic, CSS custom properties, 132 theme combos, shadcn/ui compatible. **Recommended.**                         |
| **`djust-theming` alone**                | You want the design system and theme switching but prefer to write your own component HTML.                                           |
| **`djust-components` alone**             | You want pre-built template tags but will define your own `--dj-*` CSS variables.                                                     |
| **Core `djust.components`**              | You need Rust-accelerated rendering for high-frequency components (100+ per page), or need programmatic component creation in Python. |
| **Plain HTML**                           | You want full control. Use `dj-click`, `dj-submit` etc. directly on your own markup. djust has zero opinions on your HTML structure.  |
