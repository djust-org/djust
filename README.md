# 🚀 djust

**Blazing fast reactive server-side rendering for Django, powered by Rust**

djust brings Phoenix LiveView-style reactive components to Django, with performance that feels native. Write server-side Python code with automatic, instant client updates—no JavaScript bundling, no build step, no complexity.

🌐 **[djust.org](https://djust.org)** | 🚀 **[Quick Start](https://djust.org/quickstart/)** | 📝 **[Examples](https://djust.org/examples/)**

[![MIT License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![Django 3.2+](https://img.shields.io/badge/django-3.2+-green.svg)](https://www.djangoproject.com/)

## ✨ Features

- ⚡ **10-100x Faster** - Rust-powered template engine and Virtual DOM diffing
- 🔄 **Reactive Components** - Phoenix LiveView-style server-side reactivity
- 🔌 **Django Compatible** - Works with existing Django templates and components
- 📦 **Zero Build Step** - Just ~5KB of client JavaScript, no bundling needed
- 🌐 **WebSocket Updates** - Real-time DOM patches over WebSocket (with HTTP fallback)
- 🎯 **Minimal Client Code** - Smart diffing sends only what changed
- 🔒 **Type Safe** - Rust guarantees for core performance-critical code
- 🐞 **Developer Debug Panel** - Interactive debugging with event history and VDOM inspection
- 💤 **Lazy Hydration** - Defer WebSocket connections for below-fold content (20-40% memory savings)
- 🚀 **TurboNav Compatible** - Works seamlessly with Turbo-style client-side navigation

## 🎯 Quick Example

```python
from djust import LiveView

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

    def increment(self):
        self.count += 1  # Automatically updates client!

    def decrement(self):
        self.count -= 1
```

That's it! No JavaScript needed. State changes automatically trigger minimal DOM updates.

## 📊 Performance

Benchmarked on M1 MacBook Pro (2021):

| Operation | Django | djust | Speedup |
|-----------|---------|-------|---------|
| Template Rendering (100 items) | 2.5 ms | 0.15 ms | **16.7x** |
| Large List (10k items) | 450 ms | 12 ms | **37.5x** |
| Virtual DOM Diff | N/A | 0.08 ms | **Sub-ms** |
| Round-trip Update | 50 ms | 5 ms | **10x** |

Run benchmarks yourself:
```bash
cd benchmarks
python benchmark.py
```

## 🚀 Installation

### Prerequisites

- Python 3.8+
- Rust 1.70+ (for building from source)
- Django 3.2+

### Install from PyPI (when published)

```bash
pip install djust
```

### Build from Source

#### Using Make (Easiest - Recommended for Development)

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

**Common Make Commands:**
- `make start` - Start development server with hot reload
- `make stop` - Stop the development server
- `make status` - Check if server is running
- `make test` - Run all tests
- `make clean` - Clean build artifacts
- `make help` - Show all available commands

#### Using uv (Fast)

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

# Or build wheel
maturin build --release
pip install target/wheels/djust-*.whl
```

## 📖 Documentation

### Setup

1. Add to `INSTALLED_APPS`:

```python
INSTALLED_APPS = [
    # ...
    'channels',  # Required for WebSocket support
    'djust',
    # ...
]
```

2. Configure ASGI application (`asgi.py`):

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

3. Add to `settings.py`:

```python
ASGI_APPLICATION = 'myproject.asgi.application'

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels.layers.InMemoryChannelLayer'
    }
}
```

### Creating LiveViews

#### Class-Based LiveView

```python
from djust import LiveView

class TodoListView(LiveView):
    template_name = 'todos.html'  # Or use template_string

    def mount(self, request, **kwargs):
        """Called when view is first loaded"""
        self.todos = []

    def add_todo(self, text):
        """Event handler - called from client"""
        self.todos.append({'text': text, 'done': False})

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

<!-- Filters -->
<p>{{ text|upper }}</p>
<a href="?q={{ query|urlencode }}">Search</a>

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

### Supported Events

- `dj-click` - Click events
- `dj-input` - Input events (passes `value`)
- `dj-change` - Change events (passes `value`)
- `dj-submit` - Form submission (passes form data as dict)

### Reusable Components

djust provides a powerful component system with automatic state management and stable component IDs.

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

Components automatically receive a stable `component_id` based on their **attribute name** in your view. This eliminates manual ID management:

```python
# When you write:
self.alert_success = AlertComponent(message="Success!")

# The framework automatically:
# 1. Sets component.component_id = "alert_success"
# 2. Persists this ID across renders and events
# 3. Uses it in HTML: data-component-id="alert_success"
# 4. Routes events back to the correct component
```

**Why it works:**
- The attribute name (`alert_success`) is already unique within your view
- It's stable across re-renders and WebSocket reconnections
- Event handlers can reference components by their attribute names
- No manual ID strings to keep in sync

**Event Routing Example:**

```python
class MyView(LiveView):
    def mount(self, request):
        self.alert_warning = AlertComponent(
            message="Warning message",
            dismissible=True
        )

    def dismiss(self, component_id: str = None):
        """Handle dismissal - automatically routes to correct component"""
        if component_id and hasattr(self, component_id):
            component = getattr(self, component_id)
            if hasattr(component, 'dismiss'):
                component.dismiss()  # component_id="alert_warning"
```

When the dismiss button is clicked, the client sends `component_id="alert_warning"`, and the handler uses `getattr(self, "alert_warning")` to find the component.

#### Creating Custom Components

```python
from djust import Component, register_component

@register_component('my-button')
class Button(Component):
    template = '<button dj-click="on_click">{{ label }}</button>'

    def __init__(self, label="Click"):
        super().__init__()
        self.label = label
        self.clicks = 0

    def on_click(self):
        self.clicks += 1
        print(f"Clicked {self.clicks} times!")
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
        """Reactive property - auto-triggers updates"""
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

    # CSS Framework
    'css_framework': 'bootstrap5',  # Options: 'bootstrap5', 'tailwind', None
}
```

**Common Configuration Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `use_websocket` | `True` | Use WebSocket transport (requires Django Channels) |
| `debug_vdom` | `False` | Enable detailed VDOM debugging logs |
| `css_framework` | `'bootstrap5'` | CSS framework for components |

**Debug Mode:**

When troubleshooting VDOM issues, enable debug logging:

```python
# In settings.py
LIVEVIEW_CONFIG = {
    'debug_vdom': True,
}

# Or programmatically
from djust.config import config
config.set('debug_vdom', True)
```

This will log:
- Server-side: Patch generation details (stderr)
- Client-side: Patch application and DOM traversal (browser console)

### State Management

djust provides Python-only state management decorators that eliminate the need for manual JavaScript.

#### 🚀 Quick Start (5 minutes)

Build a debounced search in **8 lines of Python** (no JavaScript):

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

**That's it!** Server only queries after you stop typing. Add `@optimistic` for instant UI updates, `@cache(ttl=300)` to cache responses for 5 minutes.

**👉 [Full Quick Start Guide (5 min)](docs/STATE_MANAGEMENT_QUICKSTART.md)**

---

#### Key Features

- ✅ **Zero JavaScript Required** - Common patterns work without writing any JS
- ✅ **87% Code Reduction** - Decorators replace hundreds of lines of manual JavaScript
- ✅ **Smallest Bundle** - 7.1 KB client.js (vs Phoenix ~30KB, Livewire ~50KB)
- ✅ **Competitive DX** - Matches Phoenix LiveView and Laravel Livewire developer experience

#### Available Decorators

| Decorator | Use When | Example |
|-----------|----------|---------|
| `@debounce(wait)` | User is typing | Search, autosave |
| `@throttle(interval)` | Rapid events | Scroll, resize |
| `@optimistic` | Instant feedback | Counter, toggle |
| `@cache(ttl, key_params)` | Repeated queries | Autocomplete |
| `@client_state(keys)` | Multi-component | Dashboard filters |
| `DraftModeMixin` | Auto-save forms | Contact form |

**Quick Decision Matrix:**
- Typing in input? → `@debounce(0.5)`
- Scrolling/resizing? → `@throttle(0.1)`
- Need instant UI update? → `@optimistic`
- Same query multiple times? → `@cache(ttl)`
- Multiple components? → `@client_state([keys])`
- Auto-save forms? → `DraftModeMixin`

#### Learn More

- 🚀 **[Quick Start (5 min)](docs/STATE_MANAGEMENT_QUICKSTART.md)** - Get productive fast
- 📚 **[Full Tutorial (20 min)](docs/STATE_MANAGEMENT_TUTORIAL.md)** - Step-by-step Product Search
- 📖 **[API Reference](docs/STATE_MANAGEMENT_API.md)** - Complete decorator docs + cheat sheet
- 🎯 **[Examples](docs/STATE_MANAGEMENT_EXAMPLES.md)** - Copy-paste ready code
- 🔄 **[Migration Guide](docs/STATE_MANAGEMENT_MIGRATION.md)** - Convert JavaScript to Python
- ⚖️ **[Framework Comparison](docs/STATE_MANAGEMENT_COMPARISON.md)** - vs Phoenix LiveView & Laravel Livewire

### Developer Tooling

#### Debug Panel

Interactive debugging tool for LiveView development (DEBUG mode only):

```python
# In settings.py
DEBUG = True  # Debug panel automatically enabled
```

**Open**: Press `Ctrl+Shift+D` (Windows/Linux) or `Cmd+Shift+D` (Mac), or click the 🐞 floating button

**Features**:
- 🔍 **Event Handlers** - Discover all handlers with parameters, types, and descriptions
- 📊 **Event History** - Real-time log with timing metrics (e.g., `search • 45.2ms`)
- ⚡ **VDOM Patches** - Monitor DOM updates with sub-millisecond precision
- 🔬 **Variables** - Inspect current view state

**Learn More**:
- 📖 **[Debug Panel Guide](docs/DEBUG_PANEL.md)** - Complete user guide
- 📝 **[Event Handler Best Practices](docs/EVENT_HANDLERS.md)** - Patterns and conventions

#### Event Handlers

Always use `@event_handler` decorator for auto-discovery and validation:

```python
from djust.decorators import event_handler

@event_handler()
def search(self, value: str = "", **kwargs):
    """Search handler - description shown in debug panel"""
    self.search_query = value
```

**Parameter Convention**: Use `value` for form inputs (`dj-input`, `dj-change` events):

```python
# ✅ Correct - matches what form events send
@event_handler()
def search(self, value: str = "", **kwargs):
    self.search_query = value

# ❌ Wrong - won't receive input value
@event_handler()
def search(self, query: str = "", **kwargs):
    self.search_query = query  # Always "" (default)
```

## 🏗️ Architecture

```
┌─────────────────────────────────────────────┐
│  Browser                                    │
│  ├── Client.js (5KB) - Event & DOM patches │
│  └── WebSocket Connection                   │
└─────────────────────────────────────────────┘
           ↕️ WebSocket (Binary/JSON)
┌─────────────────────────────────────────────┐
│  Django + Channels (Python)                 │
│  ├── LiveView Classes                       │
│  ├── Event Handlers                         │
│  └── State Management                       │
└─────────────────────────────────────────────┘
           ↕️ Python/Rust FFI (PyO3)
┌─────────────────────────────────────────────┐
│  Rust Core (Native Speed)                   │
│  ├── Template Engine (<1ms)                │
│  ├── Virtual DOM Diffing (<100μs)          │
│  ├── HTML Parser                            │
│  └── Binary Serialization (MessagePack)    │
└─────────────────────────────────────────────┘
```

## 🎨 Examples

See the [examples/demo_project](examples/demo_project) directory for complete working examples:

- **Counter** - Simple reactive counter
- **Todo List** - CRUD operations with lists
- **Chat** - Real-time messaging

Run the demo:

```bash
cd examples/demo_project
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Visit http://localhost:8000

## 🔧 Development

### Project Structure

```
djust/
├── crates/
│   ├── djust_core/      # Core types & utilities
│   ├── djust_templates/ # Template engine
│   ├── djust_vdom/      # Virtual DOM & diffing
│   └── djust_live/      # Main PyO3 bindings
├── python/
│   └── djust/      # Python package
│       ├── live_view.py       # LiveView base class
│       ├── component.py       # Component system
│       ├── websocket.py       # WebSocket consumer
│       └── static/
│           └── client.js      # Client runtime
├── examples/                  # Example projects
├── benchmarks/               # Performance benchmarks
└── tests/                    # Tests
```

### Running Tests

```bash
# Rust tests
cargo test

# Python tests
pytest

# Integration tests
cd examples/demo_project
python manage.py test
```

### Building Documentation

```bash
cargo doc --open
```

## 💰 Supporting djust

djust is open source (MIT licensed) and free forever. If you're using djust in production or want to support development:

- ⭐ **Star this repo** - Help others discover djust
- 💜 **[GitHub Sponsors](https://github.com/sponsors/djust-org)** - Monthly support from $5/month

Your support helps us maintain and improve djust for everyone!

## 🤝 Contributing

Contributions welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) first.

Areas we'd love help with:
- Additional Django template filters/tags
- More example applications
- Performance optimizations
- Documentation improvements
- Browser compatibility testing

## 📝 Roadmap

- [x] Template inheritance (`{% extends %}`)
- [x] `{% url %}` and `{% include %}` tags
- [x] Comparison operators in `{% if %}` tags
- [x] More Django template filters (`urlencode`)
- [ ] File upload handling
- [ ] Server-sent events (SSE) fallback
- [ ] React/Vue component compatibility
- [ ] TypeScript definitions
- [ ] Redis-backed session storage
- [ ] Horizontal scaling support

## 🔒 Security

- CSRF protection via Django middleware
- XSS protection via automatic template escaping
- WebSocket authentication via Django sessions
- Rate limiting support

Report security issues to: security@djust.org

## 📄 License

MIT License - see [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- Inspired by [Phoenix LiveView](https://hexdocs.pm/phoenix_live_view/)
- Built with [PyO3](https://pyo3.rs/) for Python/Rust interop
- Uses [html5ever](https://github.com/servo/html5ever) for HTML parsing
- Powered by the amazing Rust and Django communities

## 💬 Community & Support

- 🌐 **[djust.org](https://djust.org)** - Official website
- 🚀 **[Quick Start](https://djust.org/quickstart/)** - Get started in minutes
- 📝 **[Examples](https://djust.org/examples/)** - Live code examples
- 🐛 **[Issues](https://github.com/djust-org/djust/issues)** - Bug reports & feature requests
- 📧 **Email**: support@djust.org

---

**[djust.org](https://djust.org)** — Made with ❤️ by the djust community
