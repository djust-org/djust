# djust - Quick Start Guide

Get up and running in 5 minutes!

## 🚀 Try the Demo

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
# Create virtual environment
uv venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install maturin and build
uv pip install maturin
maturin develop --release
```

This compiles the Rust code and installs the Python bindings. Takes ~2-3 minutes first time.

### 3. Run the Demo

```bash
# Go to demo project
cd examples/demo_project

# Install Django dependencies
uv pip install -r requirements.txt

# Setup database
python manage.py migrate

# Start server
python manage.py runserver
```

### 4. Open Browser

Visit: **http://localhost:8000**

Try the three demos:
- **Counter** - Click buttons, see instant updates
- **Todo List** - Add, toggle, delete todos
- **Chat** - Send messages in real-time

## 📝 Create Your First LiveView

Create a new file `myapp/views.py`:

```python
from djust import LiveView

class HelloView(LiveView):
    template_string = """
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

    def update_name(self, value):
        self.name = value

    def increment(self):
        self.clicks += 1
```

Add to `urls.py`:

```python
from django.urls import path
from myapp.views import HelloView

urlpatterns = [
    path('hello/', HelloView.as_view()),
]
```

That's it! State changes automatically update the client.

## 🎯 Key Features

### Reactive State
```python
def increment(self):
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
    def mount(self, request):
        # Component IDs are automatically set from attribute names
        self.alert_success = AlertComponent(
            message="Success!",
            type="success",
            dismissible=True
        )
        # component_id is now "alert_success" automatically!

    def dismiss(self, component_id: str = None):
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

## ⚡ Performance

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

## 📚 Next Steps

- [Full Documentation](README.md)
- [API Reference](https://djust.org/docs)
- [Example Projects](examples/)
- [Contributing Guide](CONTRIBUTING.md)

## 💬 Need Help?

- 🐛 [Issue Tracker](https://github.com/johnrtipton/djust/issues)
- 💬 [Discord](https://discord.gg/djust)
- 📧 [Email](mailto:support@djust.org)

---

Made with ❤️ and ⚡ by the djust community
