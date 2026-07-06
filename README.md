<p align="center">
  <img src="branding/logo/djust-wordmark-dark.png" alt="djust" width="300" />
</p>

<p align="center"><strong>Reactive server-side rendering for Django, powered by Rust</strong></p>

djust brings Phoenix LiveView-style reactive components to Django. You write
server-side Python; the client updates automatically over a WebSocket. There is
no JavaScript to write, no bundler, and no build step in your project.

**[djust.org](https://djust.org)** · **[Documentation](https://docs.djust.org)** · **[Quick Start](https://docs.djust.org/getting-started/)** · **[Examples](https://djust.org/examples/)**

[![PyPI version](https://img.shields.io/pypi/v/djust.svg)](https://pypi.org/project/djust/)
[![CI](https://github.com/djust-org/djust/actions/workflows/test.yml/badge.svg)](https://github.com/djust-org/djust/actions/workflows/test.yml)
[![MIT License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Django 4.2+](https://img.shields.io/badge/django-4.2+-green.svg)](https://www.djangoproject.com/)
[![PyPI Downloads](https://img.shields.io/pypi/dm/djust.svg)](https://pypi.org/project/djust/)

## Features

- **Fast** — Rust-powered template engine and virtual DOM diffing (10–100x faster than plain Django rendering; see [Performance](#performance))
- **Reactive components** — Phoenix LiveView-style server-side reactivity
- **Django compatible** — works with existing Django templates and components
- **No build step** — ~55 KB gzipped client JavaScript, no bundling required
- **WebSocket updates** — real-time DOM patches over WebSocket, with HTTP fallback
- **Minimal payloads** — diffing sends only what changed
- **Rust core** — performance-critical paths (templates, VDOM, parsing) are written in Rust
- **Debug panel** — interactive debugging with event history and VDOM inspection
- **Lazy hydration** — defer WebSocket connections for below-the-fold content to reduce memory
- **TurboNav compatible** — works with Turbo-style client-side navigation
- **PWA support** — offline-first Progressive Web Apps with automatic sync
- **Multi-tenant** — tenant isolation for SaaS architectures
- **Auth** — view-level and handler-level authorization via Django permissions

## Quick Example

```python
from djust import LiveView, event_handler

class CounterView(LiveView):
    template_string = """
    <div>
        <h1>Count: {{ count }}</h1>
        <button dj-click="increment">+</button>
        <button dj-click="decrement">-</button>
    </div>
    """

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler
    def increment(self):
        self.count += 1  # Automatically updates client

    @event_handler
    def decrement(self):
        self.count -= 1
```

No JavaScript needed. State changes trigger minimal DOM updates automatically.

## How Reactivity Works

djust uses a Rust-powered virtual DOM (VDOM) to diff server-rendered HTML and
send only the changed patches over WebSocket. A few core attributes make
everything click.

### Template Anatomy

```html
{% load live_tags %}
<!DOCTYPE html>
<html>
<head>
    {% djust_client_config %}        {# Emits client config meta tags; djust auto-injects the client runtime #}
</head>
<body dj-view="{{ dj_view_id }}">   {# Identifies the WebSocket session #}
    <div dj-root>                    {# Reactive boundary — only this is diffed #}
        <h1>Count: {{ count }}</h1>
        <button dj-click="increment">+</button>
    </div>
    {# Static content outside dj-root is never touched by VDOM patching #}
</body>
</html>
```

| Attribute | Where | Purpose |
|---|---|---|
| `{% djust_client_config %}` | `<head>` | Emits client config meta tags; djust auto-injects the ~5KB client runtime into every LiveView response — no manual `<script>` tag needed |
| `dj-view="{{ dj_view_id }}"` | `<body>` | Connects page to WebSocket session |
| `dj-root` | Inner `<div>` | Marks the reactive region; only HTML inside is diffed and patched |

### Stable List Identity

For lists that can reorder or have items inserted/deleted, add `data-key` or
`dj-key` on each item. djust uses this to emit `MoveChild` patches instead of
remove-then-insert pairs, preserving DOM state (focus, scroll position,
animations):

```html
{% for item in items %}
<div data-key="{{ item.id }}">
    {{ item.name }}
    <button dj-click="delete" data-item-id="{{ item.id }}">Delete</button>
</div>
{% endfor %}
```

Without a key, djust diffs by position — correct, but it produces more DOM
mutations for reorders.

### Common Pitfall: One-Sided `{% if %}` in Class Attributes

Using `{% if %}` without `{% else %}` inside an HTML attribute value can cause
VDOM patching misalignment, because of djust's branch-aware div-depth counting:

```html
{# WRONG: one-sided if inside class attribute #}
<div class="card {% if active %}active{% endif %}">

{# CORRECT: use full if/else #}
<div class="card {% if active %}active{% else %}{% endif %}">

{# ALSO CORRECT: move conditional outside the tag #}
{% if active %}
<div class="card active">
{% else %}
<div class="card">
{% endif %}
    ...
</div>
```

This applies only to attribute values — `{% if %}` blocks in element content
work fine.

See the [VDOM Architecture guide](docs/website/advanced/vdom-architecture.md)
and [Template Cheat Sheet](docs/website/guides/template-cheatsheet.md) for full
details.

## Getting Started

A complete walkthrough from zero to a working reactive counter in five steps.

### Step 1 — Install

```bash
pip install djust django-channels
```

### Step 2 — Add to `INSTALLED_APPS` and configure settings

In `myproject/settings.py`:

```python
INSTALLED_APPS = [
    # ... your existing apps ...
    'channels',   # WebSocket support
    'djust',
]

ASGI_APPLICATION = 'myproject.asgi.application'

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels.layers.InMemoryChannelLayer',
    }
}
```

### Step 3 — Configure `asgi.py`

Replace `myproject/asgi.py` with:

```python
import os
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from djust.websocket import LiveViewConsumer
from django.urls import path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": AuthMiddlewareStack(
        URLRouter([
            path('ws/live/', LiveViewConsumer.as_asgi()),
        ])
    ),
})
```

### Step 4 — Add the URL route

In `myproject/urls.py`:

```python
from django.urls import path
from myapp.views import CounterView

urlpatterns = [
    path('counter/', CounterView.as_view(), name='counter'),
]
```

### Step 5 — Write the view and template

`myapp/views.py`:

```python
from djust import LiveView, event_handler

class CounterView(LiveView):
    template_name = 'counter.html'

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler
    def increment(self):
        self.count += 1

    @event_handler
    def decrement(self):
        self.count -= 1
```

`myapp/templates/counter.html`:

```html
{% load live_tags %}
<!DOCTYPE html>
<html>
<head>
    <title>Counter</title>
    {% djust_client_config %}
</head>
<body dj-view="{{ dj_view_id }}">
    <div dj-root>
        <h1>Count: {{ count }}</h1>
        <button dj-click="increment">+</button>
        <button dj-click="decrement">-</button>
    </div>
</body>
</html>
```

Run with `uvicorn myproject.asgi:application --reload` and open `/counter/`.
Clicking the buttons updates the count without a page reload — no JavaScript
written, no build step.

**Next steps:**
- [Template Cheat Sheet](docs/website/guides/template-cheatsheet.md) — all directives and filters at a glance
- [Components Guide](docs/website/guides/components.md) — build reusable components with theming
- [CSS Framework Guide](docs/website/guides/css-frameworks.md) — Tailwind and Bootstrap integration
- [Deployment Guide](docs/website/guides/deployment.md) — production deployment with uvicorn, Redis, and Nginx

---

## Performance

Benchmarked on an M1 MacBook Pro (2021):

| Operation | Django | djust | Speedup |
|-----------|---------|-------|---------|
| Template rendering (100 items) | 2.5 ms | 0.15 ms | **16.7x** |
| Large list (10k items) | 450 ms | 12 ms | **37.5x** |
| Virtual DOM diff | N/A | 0.08 ms | **sub-ms** |
| Round-trip update | 50 ms | 5 ms | **10x** |

Run the benchmarks yourself:

```bash
cd benchmarks
python benchmark.py
```

## Installation

### Prerequisites

- Python 3.10+
- Django 4.2+
- Rust 1.70+ (only required when building from source)

### Install from PyPI

```bash
pip install djust
```

### Build from Source

#### Using Make (recommended for development)

```bash
# Clone the repository
git clone https://github.com/djust-org/djust.git
cd djust

# Install Rust (if needed)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Install everything and build
make install

# Start the development server
make start

# See all available commands
make help
```

Common Make commands:

- `make start` — start development server with hot reload
- `make stop` — stop the development server
- `make status` — check if the server is running
- `make test` — run all tests
- `make clean` — clean build artifacts
- `make help` — show all available commands

#### Using uv

```bash
# Clone the repository
git clone https://github.com/djust-org/djust.git
cd djust

# Install Rust (if needed)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Install uv (if needed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create virtual environment and install dependencies
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install maturin and build
uv pip install maturin
maturin develop --release
```

#### Using pip

```bash
# Clone the repository
git clone https://github.com/djust-org/djust.git
cd djust

# Install Rust (if needed)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install maturin
pip install maturin

# Build and install
maturin develop --release

# Or build a wheel
maturin build --release
pip install target/wheels/djust-*.whl
```

## Documentation

The full documentation lives at [docs.djust.org](https://docs.djust.org). The
sections below cover the core API; see [Getting Started](#getting-started) above
for first-time setup.

### Creating LiveViews

#### Class-Based LiveView

```python
from djust import LiveView, event_handler

class TodoListView(LiveView):
    template_name = 'todos.html'  # Or use template_string

    def mount(self, request, **kwargs):
        """Called when view is first loaded"""
        self.todos = []

    @event_handler
    def add_todo(self, text):
        """Event handler — called from client"""
        self.todos.append({'text': text, 'done': False})

    @event_handler
    def toggle_todo(self, index):
        self.todos[index]['done'] = not self.todos[index]['done']
```

#### Function-Based LiveView

```python
from djust import live_view

@live_view(template_name='counter.html')
def counter_view(request):
    count = 0

    def increment():
        nonlocal count
        count += 1

    return locals()  # Returns all local variables as context
```

### Template Syntax

djust supports Django template syntax with event binding:

```html
<!-- Variables -->
<h1>{{ title }}</h1>

<!-- Filters (all 57 Django built-in filters supported) -->
<p>{{ text|upper }}</p>
<p>{{ description|truncatewords:20 }}</p>
<a href="?q={{ query|urlencode }}">Search</a>
{{ body|urlize }}  {# No |safe needed — djust auto-marks urlize output as safe (see note below) #}

<!-- Control flow -->
{% if show %}
    <div>Visible</div>
{% endif %}

{% if count > 10 %}
    <div>Many items!</div>
{% endif %}

{% for item in items %}
    <li>{{ item }}</li>
{% endfor %}

<!-- URL resolution -->
<a href="{% url 'myapp:detail' pk=item.id %}">View</a>

<!-- Template includes -->
{% include "partials/header.html" %}

<!-- Event binding -->
<button dj-click="increment">Click me</button>
<input dj-input="on_search" type="text" />
<form dj-submit="submit_form">
    <input name="email" />
    <button type="submit">Submit</button>
</form>
```

> **Django migration note:** In standard Django, `urlize` requires `|safe` to
> render its HTML output. djust's Rust template engine automatically marks
> `urlize`, `urlizetrunc`, and `unordered_list` as safe (via
> `safe_output_filters` in the renderer), because these filters handle their own
> HTML escaping internally. Adding `|safe` after them is unnecessary.

### Supported Events

- `dj-click` — click events
- `dj-input` — input events (passes `value`)
- `dj-change` — change events (passes `value`)
- `dj-submit` — form submission (passes form data as a dict)

### Reusable Components

djust includes a component system with automatic state management and stable
component IDs.

#### Basic Component Example

```python
from djust.components import AlertComponent

class MyView(LiveView):
    def mount(self, request):
        # Components get automatic IDs based on attribute names
        self.alert_success = AlertComponent(
            message="Operation successful!",
            type="success",
            dismissible=True
        )
        # component_id automatically becomes "alert_success"
```

#### Component ID Management

Components automatically receive a stable `component_id` based on their
**attribute name** in your view, which eliminates manual ID management:

```python
# When you write:
self.alert_success = AlertComponent(message="Success!")

# The framework automatically:
# 1. Sets component.component_id = "alert_success"
# 2. Persists this ID across renders and events
# 3. Uses it in HTML: data-component-id="alert_success"
# 4. Routes events back to the correct component
```

Why it works:

- The attribute name (`alert_success`) is already unique within your view
- It's stable across re-renders and WebSocket reconnections
- Event handlers can reference components by their attribute names
- No manual ID strings to keep in sync

Event routing example:

```python
class MyView(LiveView):
    def mount(self, request):
        self.alert_warning = AlertComponent(
            message="Warning message",
            dismissible=True
        )

    @event_handler
    def dismiss(self, component_id: str = None):
        """Handle dismissal — automatically routes to correct component"""
        if component_id and hasattr(self, component_id):
            component = getattr(self, component_id)
            if hasattr(component, 'dismiss'):
                component.dismiss()  # component_id="alert_warning"
```

When the dismiss button is clicked, the client sends `component_id="alert_warning"`,
and the handler uses `getattr(self, "alert_warning")` to find the component.

#### Creating Custom Components

```python
from djust import LiveComponent, event_handler
from djust.components import register_component

class ButtonComponent(LiveComponent):
    template = '<button dj-click="on_click" data-component-id="{{ component_id }}">{{ label }}</button>'

    def mount(self, **kwargs):
        self.label = kwargs.get("label", "Click")
        self.clicks = 0

    @event_handler()
    def on_click(self, **kwargs):
        self.clicks += 1
        self.trigger_update()

    def get_context_data(self):
        return {"label": self.label, "clicks": self.clicks}

# register_component accepts LiveComponent subclasses (stateful, event-driven)
register_component('my-button', ButtonComponent)
```

### Decorators

```python
from djust import LiveView, event_handler, reactive

class MyView(LiveView):
    @event_handler
    def handle_click(self):
        """Marks method as event handler"""
        pass

    @reactive
    def count(self):
        """Reactive property — auto-triggers updates"""
        return self._count

    @count.setter
    def count(self, value):
        self._count = value
```

### Configuration

Configure djust in your Django `settings.py`:

```python
LIVEVIEW_CONFIG = {
    # Transport mode
    'use_websocket': True,  # Set to False for HTTP-only mode (no WebSocket dependency)

    # Debug settings
    'debug_vdom': False,  # Enable detailed VDOM patch logging (for troubleshooting)

    # Serialization (issue #292)
    'strict_serialization': False,  # Raise TypeError for non-serializable state values (recommended in development)

    # CSS Framework
    'css_framework': 'bootstrap5',  # Options: 'bootstrap5', 'tailwind', None
}
```

Common configuration options:

| Option | Default | Description |
|--------|---------|-------------|
| `use_websocket` | `True` | Use WebSocket transport (requires Django Channels) |
| `debug_vdom` | `False` | Enable detailed VDOM debugging logs |
| `strict_serialization` | `False` | Raise TypeError for non-serializable state (recommended in dev) |
| `css_framework` | `'bootstrap5'` | CSS framework for components |

CSS framework setup. For Tailwind CSS, use the one-command setup:

```bash
python manage.py djust_setup_css tailwind
```

This auto-detects template directories, creates config files, and builds your
CSS. For production:

```bash
python manage.py djust_setup_css tailwind --minify
```

See the [CSS Framework Guide](docs/website/guides/css-frameworks.md) for detailed
setup instructions, Bootstrap configuration, and CI/CD integration.

Debug mode. When troubleshooting VDOM issues, enable debug logging:

```python
# In settings.py
LIVEVIEW_CONFIG = {
    'debug_vdom': True,
}

# Or programmatically
from djust.config import config
config.set('debug_vdom', True)
```

This logs:

- Server-side: patch generation details (stderr)
- Client-side: patch application and DOM traversal (browser console)

### State Management

djust provides Python-only state management decorators that remove the need for
manual JavaScript.

#### Quick Start

Build a debounced search in eight lines of Python (no JavaScript):

```python
from djust import LiveView
from djust.decorators import debounce

class ProductSearchView(LiveView):
    template_string = """
    <input dj-input="search" placeholder="Search products..." />
    <div>{% for p in results %}<div>{{ p.name }}</div>{% endfor %}</div>
    """

    def mount(self, request):
        self.results = []

    @debounce(wait=0.5)  # Wait 500ms after typing stops
    def search(self, query: str = "", **kwargs):
        self.results = Product.objects.filter(name__icontains=query)[:10]
```

The server only queries after you stop typing. Add `@optimistic` for instant UI
updates, or `@cache(ttl=300)` to cache responses for five minutes.

See the [State Management Quick Start](docs/STATE_MANAGEMENT_QUICKSTART.md).

#### Available Decorators

| Decorator | Use When | Example |
|-----------|----------|---------|
| `@debounce(wait)` | User is typing | Search, autosave |
| `@throttle(interval)` | Rapid events | Scroll, resize |
| `@optimistic` | Instant feedback | Counter, toggle |
| `@cache(ttl, key_params)` | Repeated queries | Autocomplete |
| `@client_state(keys)` | Multi-component | Dashboard filters |
| `@background` | Long operations | AI generation, file processing |
| `DraftModeMixin` | Auto-save forms | Contact form |

Quick decision guide:

- Typing in an input? → `@debounce(0.5)`
- Scrolling/resizing? → `@throttle(0.1)`
- Need an instant UI update? → `@optimistic`
- Same query multiple times? → `@cache(ttl)`
- Multiple components? → `@client_state([keys])`
- Long-running work? → `@background` or `self.start_async(callback)`
- Auto-save forms? → `DraftModeMixin`

More documentation:

- [Quick Start](docs/STATE_MANAGEMENT_QUICKSTART.md) — get productive fast
- [Full Tutorial](docs/STATE_MANAGEMENT_TUTORIAL.md) — step-by-step product search
- [API Reference](docs/STATE_MANAGEMENT_API.md) — complete decorator docs and cheat sheet
- [Examples](docs/STATE_MANAGEMENT_EXAMPLES.md) — copy-paste-ready code
- [Migration Guide](docs/STATE_MANAGEMENT_MIGRATION.md) — convert JavaScript to Python
- [Framework Comparison](docs/STATE_MANAGEMENT_COMPARISON.md) — vs Phoenix LiveView and Laravel Livewire

### Navigation Patterns

djust provides three navigation mechanisms for building multi-view applications
without full page reloads:

#### When to Use What

| Scenario | Use | Why |
|----------|-----|-----|
| Filter/sort/paginate within same view | `dj-patch` / `live_patch()` | No remount, URL stays bookmarkable |
| Navigate to a different LiveView | `dj-navigate` / `live_redirect()` | Same WebSocket, no page reload |
| Link to non-LiveView page | Standard `<a href>` | Full page load needed |

#### Quick Decision Tree

```
Is this a direct user click on a link?
├─ Yes → Is it the same view (filter/sort)?
│   ├─ Yes → Use dj-patch
│   └─ No → Use dj-navigate
│
└─ No → Is navigation conditional on server logic?
    ├─ Yes → Use live_redirect() in @event_handler
    │   Examples: form validation, auth checks, async operations
    └─ No → You probably need dj-navigate (see anti-pattern below)
```

#### Anti-Pattern: Don't Use `dj-click` for Navigation

This is the most common mistake when building multi-view djust apps. Using
`dj-click` to trigger a handler that immediately calls `live_redirect()` creates
an unnecessary round-trip.

Wrong — using `dj-click` to trigger a handler that calls `live_redirect()`:

```python
# Anti-pattern: handler does nothing but navigate
@event_handler()
def go_to_item(self, item_id, **kwargs):
    self.live_redirect(f"/items/{item_id}/")  # Wasteful round-trip
```

```html
<!-- Wrong: forces a WebSocket round-trip just to navigate -->
<button dj-click="go_to_item" dj-value-item_id="{{ item.id }}">View</button>
```

What actually happens:

1. User clicks button → client sends WebSocket message (50–100ms)
2. Server receives message, processes handler (10–50ms)
3. Server responds with `live_redirect` command (50–100ms)
4. Client finally navigates to the new view

Total: 110–250ms, plus handler processing time.

Right — using `dj-navigate` directly:

```html
<!-- Right: client navigates immediately, no server round-trip -->
<a dj-navigate="/items/{{ item.id }}/">View Item</a>
```

What happens:

1. User clicks link → client navigates directly

Total: ~10ms (just DOM updates).

Why it matters:

- Performance: 10–20x faster navigation
- Network efficiency: saves WebSocket bandwidth
- User experience: instant response, no loading indicators needed
- Simplicity: less code, fewer moving parts

#### When to Use `live_redirect()` in Handlers

Use handlers for navigation only when navigation depends on server-side logic or
validation.

Conditional navigation after form validation:

```python
@event_handler()
def submit_form(self, **kwargs):
    if self.form.is_valid():
        self.form.save()
        self.live_redirect("/success/")  # OK: conditional on validation
    else:
        # Stay on form to show errors
        pass
```

Navigation based on auth/permissions:

```python
@event_handler()
def view_sensitive_data(self, **kwargs):
    if not self.request.user.has_perm('app.view_sensitive'):
        self.live_redirect("/access-denied/")  # OK: auth check required
        return
    self.show_sensitive = True
```

Navigation after async operations:

```python
@event_handler()
async def create_and_view_item(self, name, **kwargs):
    item = await Item.objects.acreate(name=name, owner=self.request.user)
    self.live_redirect(f"/items/{item.id}/")  # OK: navigate to newly created item
```

Multi-step wizard logic:

```python
@event_handler()
def next_step(self, **kwargs):
    if self.current_step == "payment" and not self.payment_valid:
        # Stay on payment step if invalid
        return
    self.current_step = self.get_next_step()
    self.live_patch(params={"step": self.current_step})  # OK: conditional flow
```

The common theme: the handler does meaningful work before navigating. If your
handler only calls `live_redirect()`, use `dj-navigate` instead.

#### Quick Example: Multi-View App

```python
from djust import LiveView
from djust.mixins.navigation import NavigationMixin
from djust.decorators import event_handler

class ProductListView(NavigationMixin, LiveView):
    template_string = """
    <!-- Filter within same view: use dj-patch -->
    <a dj-patch="?category=electronics">Electronics</a>
    <a dj-patch="?category=books">Books</a>

    <div>
        {% for product in products %}
            <!-- Navigate to different view: use dj-navigate -->
            <a dj-navigate="/products/{{ product.id }}/">{{ product.name }}</a>
        {% endfor %}
    </div>
    """

    def mount(self, request, **kwargs):
        self.category = "all"
        self.products = []

    def handle_params(self, params, uri):
        """Called when URL changes via dj-patch or browser back/forward"""
        self.category = params.get("category", "all")
        self.products = Product.objects.filter(category=self.category)
```

See the [Navigation Guide](docs/guides/navigation.md) for the complete API
reference (`live_patch()`, `live_redirect()`, `handle_params()`).

### Developer Tooling

#### Debug Panel

Interactive debugging tool for LiveView development (DEBUG mode only):

```python
# In settings.py
DEBUG = True  # Debug panel automatically enabled
```

Open it with `Ctrl+Shift+D` (Windows/Linux) or `Cmd+Shift+D` (Mac), or click the
floating debug button.

Features:

- **Event handlers** — discover all handlers with parameters, types, and descriptions
- **Event history** — real-time log with timing metrics (e.g., `search • 45.2ms`)
- **VDOM patches** — monitor DOM updates with sub-millisecond precision
- **Variables** — inspect current view state

See the [Debug Panel Guide](docs/DEBUG_PANEL.md) and
[Event Handler Best Practices](docs/EVENT_HANDLERS.md).

#### Event Handlers

Always use the `@event_handler` decorator for auto-discovery and validation:

```python
from djust.decorators import event_handler

@event_handler()
def search(self, value: str = "", **kwargs):
    """Search handler — description shown in debug panel"""
    self.search_query = value
```

Parameter convention: use `value` for form inputs (`dj-input`, `dj-change`
events):

```python
# Correct — matches what form events send
@event_handler()
def search(self, value: str = "", **kwargs):
    self.search_query = value

# Wrong — won't receive input value
@event_handler()
def search(self, query: str = "", **kwargs):
    self.search_query = query  # Always "" (default)
```

## Architecture

```
┌─────────────────────────────────────────────┐
│  Browser                                    │
│  ├── client.js (~55 KB gz) — events & DOM  │
│  └── WebSocket connection                   │
└─────────────────────────────────────────────┘
           ↕ WebSocket (Binary/JSON)
┌─────────────────────────────────────────────┐
│  Django + Channels (Python)                 │
│  ├── LiveView classes                       │
│  ├── Event handlers                         │
│  └── State management                       │
└─────────────────────────────────────────────┘
           ↕ Python/Rust FFI (PyO3)
┌─────────────────────────────────────────────┐
│  Rust core (native speed)                   │
│  ├── Template engine (<1ms)                │
│  ├── Virtual DOM diffing (<100μs)          │
│  ├── HTML parser                            │
│  └── Binary serialization (MessagePack)    │
└─────────────────────────────────────────────┘
```

## Examples

See the [examples/demo_project](examples/demo_project) directory for complete
working examples:

- **Counter** — simple reactive counter
- **Todo List** — CRUD operations with lists
- **Chat** — real-time messaging

Run the demo:

```bash
cd examples/demo_project
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Visit http://localhost:8000.

## Development

### Project Structure

```
djust/
├── crates/
│   ├── djust_core/        # Core types & utilities
│   ├── djust_templates/   # Template engine
│   ├── djust_vdom/        # Virtual DOM & diffing
│   ├── djust_components/  # Reusable component library
│   └── djust_live/        # Main PyO3 bindings
├── python/
│   └── djust/             # Python package
│       ├── live_view.py         # LiveView base class
│       ├── component.py         # Component system
│       ├── websocket.py         # WebSocket consumer
│       └── static/
│           └── client.js        # Client runtime
├── branding/                    # Logo and brand assets
├── examples/                    # Example projects
├── benchmarks/                  # Performance benchmarks
└── tests/                       # Tests
```

### Running Tests

```bash
# All tests (Python + Rust + JavaScript)
make test

# Individual test suites
make test-python       # Python tests
make test-rust         # Rust tests
make test-js           # JavaScript tests

# Specific tests
pytest tests/unit/test_live_view.py
cargo test --workspace --exclude djust_live
```

For comprehensive testing documentation, see the
[Testing Guide](docs/TESTING.md).

### Building Documentation

```bash
cargo doc --open
```

## Roadmap

djust 1.0 is released and stable. Active planning lives in
[the issue tracker](https://github.com/djust-org/djust/issues). One notable
item still open:

- React/Vue component compatibility

## Security

- CSRF protection via Django middleware
- XSS protection via automatic template escaping (the Rust engine escapes all variables by default)
- HTML-producing filters (`urlize`, `urlizetrunc`, `unordered_list`) handle their own escaping internally; the Rust engine's `safe_output_filters` whitelist prevents double-escaping, so `|safe` is never needed with these filters
- WebSocket authentication via Django sessions
- WebSocket origin validation and HMAC message signing
- Per-view and global rate limiting
- Configurable allowed origins for WebSocket connections
- View-level auth enforcement (`login_required`, `permission_required`) before `mount()`
- Handler-level `@permission_required` for protecting individual event handlers
- `djust_audit` command and `djust.S005` system check for auth-posture visibility

Report security issues to security@djust.org.

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) first.

Areas where help is especially useful:

- More example applications
- Performance optimizations
- Documentation improvements
- Browser compatibility testing

## Supporting djust

djust is open source (MIT licensed) and free. If you use djust in production or
want to support development:

- Star this repository to help others discover it
- [Sponsor on GitHub](https://github.com/sponsors/djust-org) — from $5/month

## License

MIT License — see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- Inspired by [Phoenix LiveView](https://hexdocs.pm/phoenix_live_view/)
- Built with [PyO3](https://pyo3.rs/) for Python/Rust interop
- Uses [html5ever](https://github.com/servo/html5ever) for HTML parsing
- Built on the Rust and Django communities

## Community & Support

- [djust.org](https://djust.org) — official website
- [Documentation](https://docs.djust.org) — guides and API reference
- [Examples](https://djust.org/examples/) — live code examples
- [Issues](https://github.com/djust-org/djust/issues) — bug reports and feature requests
- Email: support@djust.org

---

Maintained by the djust community.
