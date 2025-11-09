# 🚀 Django Rust Live

**Blazing fast reactive server-side rendering for Django, powered by Rust**

Django Rust Live brings Phoenix LiveView-style reactive components to Django, with performance that feels native. Write server-side Python code with automatic, instant client updates—no JavaScript bundling, no build step, no complexity.

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

## 🎯 Quick Example

```python
from django_rust_live import LiveView

class CounterView(LiveView):
    template_string = """
    <div>
        <h1>Count: {{ count }}</h1>
        <button @click="increment">+</button>
        <button @click="decrement">-</button>
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

| Operation | Django | Django Rust Live | Speedup |
|-----------|---------|------------------|---------|
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
pip install django-rust-live
```

### Build from Source

#### Using Make (Easiest - Recommended for Development)

```bash
# Clone the repository
git clone https://github.com/django-rust/django-rust-live.git
cd django-rust-live

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
git clone https://github.com/django-rust/django-rust-live.git
cd django-rust-live

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
git clone https://github.com/django-rust/django-rust-live.git
cd django-rust-live

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
pip install target/wheels/django_rust_live-*.whl
```

## 📖 Documentation

### Setup

1. Add to `INSTALLED_APPS`:

```python
INSTALLED_APPS = [
    # ...
    'channels',  # Required for WebSocket support
    'django_rust_live',
    # ...
]
```

2. Configure ASGI application (`asgi.py`):

```python
import os
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from django_rust_live.websocket import LiveViewConsumer
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
from django_rust_live import LiveView

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
from django_rust_live import live_view

@live_view(template_name='counter.html')
def counter_view(request):
    count = 0

    def increment():
        nonlocal count
        count += 1

    return locals()  # Returns all local variables as context
```

### Template Syntax

Django Rust Live supports Django template syntax with event binding:

```html
<!-- Variables -->
<h1>{{ title }}</h1>

<!-- Filters -->
<p>{{ text|upper }}</p>

<!-- Control flow -->
{% if show %}
    <div>Visible</div>
{% endif %}

{% for item in items %}
    <li>{{ item }}</li>
{% endfor %}

<!-- Event binding -->
<button @click="increment">Click me</button>
<input @input="on_search" type="text" />
<form @submit="submit_form">
    <input name="email" />
    <button type="submit">Submit</button>
</form>
```

### Supported Events

- `@click` - Click events
- `@input` - Input events (passes `value`)
- `@change` - Change events (passes `value`)
- `@submit` - Form submission (passes form data as dict)

### Reusable Components

Django Rust Live provides a powerful component system with automatic state management and stable component IDs.

#### Basic Component Example

```python
from django_rust_live.components import AlertComponent

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
from django_rust_live import Component, register_component

@register_component('my-button')
class Button(Component):
    template = '<button @click="on_click">{{ label }}</button>'

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
from django_rust_live import LiveView, event_handler, reactive

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
django_rust/
├── crates/
│   ├── django_rust_core/      # Core types & utilities
│   ├── django_rust_templates/ # Template engine
│   ├── django_rust_vdom/      # Virtual DOM & diffing
│   └── django_rust_live/      # Main PyO3 bindings
├── python/
│   └── django_rust_live/      # Python package
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

## 🤝 Contributing

Contributions welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) first.

Areas we'd love help with:
- Additional Django template filters/tags
- More example applications
- Performance optimizations
- Documentation improvements
- Browser compatibility testing

## 📝 Roadmap

- [ ] Template inheritance (`{% extends %}`)
- [ ] More Django template filters
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

Report security issues to: security@django-rust.org

## 📄 License

MIT License - see [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- Inspired by [Phoenix LiveView](https://hexdocs.pm/phoenix_live_view/)
- Built with [PyO3](https://pyo3.rs/) for Python/Rust interop
- Uses [html5ever](https://github.com/servo/html5ever) for HTML parsing
- Powered by the amazing Rust and Django communities

## 💬 Support

- 📖 [Documentation](https://docs.django-rust.org)
- 💬 [Discord Community](https://discord.gg/django-rust)
- 🐛 [Issue Tracker](https://github.com/django-rust/django-rust-live/issues)
- 📧 [Mailing List](https://groups.google.com/g/django-rust)

---

Made with ❤️ by the Django Rust community
