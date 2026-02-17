# djust - Quick Start Guide

Get up and running in 5 minutes!

## Try the Demo

### 1. Clone and Build

```bash
# Clone the repository
git clone https://github.com/johnrtipton/djust.git
cd djust

# Install Rust (if needed)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source $HOME/.cargo/env

# Install uv (recommended for speed)
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Build the Rust Extension

```bash
# Create virtual environment and activate it
uv venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install maturin and build (dev build, ~10s)
uv pip install maturin
maturin develop
```

This compiles the Rust code and installs the Python bindings.

> **Note**: Use `maturin develop --release` for production-optimised builds (takes 2-3 minutes). The dev build is sufficient for local development.

### 3. Run the Demo

```bash
# Install Django + Channels dependencies
pip install -r examples/demo_project/requirements.txt

# Setup database
python examples/demo_project/manage.py migrate

# Start server (ASGI — required for WebSocket support)
uvicorn demo_project.asgi:application \
    --app-dir examples/demo_project \
    --reload
```

Or use the Makefile shortcut from the repo root:

```bash
make start
```

### 4. Open Browser

Visit: **http://localhost:8002**

Try the demos:
- **Counter** - Click buttons, see instant updates
- **Todo List** - Add, toggle, delete todos
- **Chat** - Send messages in real-time

---

## Create Your First LiveView

djust LiveViews use WebSockets. New projects need a few files beyond a standard Django setup.

### Project Setup

**`myproject/settings.py`** — add `channels` and `djust`:

```python
INSTALLED_APPS = [
    # ... standard Django apps ...
    'channels',
    'djust',
    'myapp',
]

ASGI_APPLICATION = 'myproject.asgi.application'
```

**`myproject/routing.py`** — WebSocket routing:

```python
from django.urls import path
from djust.websocket import LiveViewConsumer

websocket_urlpatterns = [
    path('ws/live/', LiveViewConsumer.as_asgi()),
]
```

**`myproject/asgi.py`** — ASGI application:

```python
import os
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
import myproject.routing

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": AuthMiddlewareStack(
        URLRouter(myproject.routing.websocket_urlpatterns)
    ),
})
```

> **Why ASGI?** Django's built-in `manage.py runserver` is WSGI-only and does not support WebSockets. Use `uvicorn` or `daphne` to run djust.

### Your First LiveView

Create `myapp/views.py`:

```python
from djust import LiveView
from djust.decorators import event_handler


class HelloView(LiveView):
    # Use `template` for inline HTML, or `template_name` for a file path
    template = """
    <div>
        <h1>Hello {{ name }}!</h1>
        <input type="text" dj-input="update_name" placeholder="Your name" />
        <p>You clicked {{ clicks }} times</p>
        <button dj-click="increment">Click me!</button>
    </div>
    """

    def mount(self, request, **kwargs):
        self.name = "World"
        self.clicks = 0

    @event_handler()
    def update_name(self, value: str = "", **kwargs):
        self.name = value

    @event_handler()
    def increment(self, **kwargs):
        self.clicks += 1
```

> **Important**: All event handlers must be decorated with `@event_handler()`. djust uses strict mode by default — undecorated methods will be blocked for security.

Add to `myproject/urls.py`:

```python
from django.urls import path
from myapp.views import HelloView

urlpatterns = [
    path('hello/', HelloView.as_view()),
]
```

Run the server:

```bash
uvicorn myproject.asgi:application --reload
```

Visit **http://localhost:8000/hello/** — state changes update the client automatically.

---

## Key Features

### Reactive State
```python
@event_handler()
def increment(self, **kwargs):
    self.count += 1  # Client auto-updates!
```

### Event Binding
```html
<button dj-click="save">Save</button>
<input dj-input="on_search" type="text" />
<form dj-submit="submit_form">...</form>
```

### Template Syntax (Django-compatible)
```html
{{ variable }}
{{ text|upper }}
{% if show %}...{% endif %}
{% for item in items %}...{% endfor %}
```

### Components

Pre-built and custom components with automatic ID management:

```python
from djust.components import AlertComponent

class MyView(LiveView):
    def mount(self, request, **kwargs):
        # Component IDs are automatically set from attribute names
        self.alert_success = AlertComponent(
            message="Success!",
            type="success",
            dismissible=True
        )
        # component_id is now "alert_success" automatically!

    @event_handler()
    def dismiss(self, component_id: str = None, **kwargs):
        """Event handlers can reference components by their attribute names"""
        if component_id and hasattr(self, component_id):
            getattr(self, component_id).dismiss()
```

Create custom components:

```python
from djust import Component

class Button(Component):
    template = '<button dj-click="clicked">{{ label }}</button>'

    def __init__(self, label="Click"):
        self.label = label
```

## Performance

Rust powers the core rendering engine:

| Operation | Django | djust | Speedup |
|-----------|---------|------------------|---------|
| Template (100 items) | 2.5ms | 0.15ms | **16x faster** |
| Large list (10k items) | 450ms | 12ms | **37x faster** |
| VDOM diff | N/A | 0.08ms | **Sub-millisecond** |

Run benchmarks:
```bash
cd benchmarks
python benchmark.py
```

## Next Steps

- [Full Documentation](README.md)
- [API Reference](https://djust.org/docs)
- [Example Projects](examples/)
- [Contributing Guide](CONTRIBUTING.md)

## Need Help?

- [Issue Tracker](https://github.com/johnrtipton/djust/issues)
- [Discord](https://discord.gg/djust)
- [Email](mailto:support@djust.org)

---

Made with love by the djust community
