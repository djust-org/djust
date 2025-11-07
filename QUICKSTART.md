# Django Rust Live - Quick Start Guide

Get up and running in 5 minutes!

## 🚀 Try the Demo

### 1. Clone and Build

```bash
# Clone the repository
git clone https://github.com/django-rust/django-rust-live.git
cd django-rust-live

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
from django_rust_live import LiveView

class HelloView(LiveView):
    template_string = """
    <div>
        <h1>Hello {{ name }}!</h1>
        <input type="text" @input="update_name" placeholder="Your name" />
        <p>You clicked {{ clicks }} times</p>
        <button @click="increment">Click me!</button>
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
<button @click="save">Save</button>
<input @input="on_search" type="text" />
<form @submit="submit_form">...</form>
```

### Template Syntax (Django-compatible)
```html
{{ variable }}
{{ text|upper }}
{% if show %}...{% endif %}
{% for item in items %}...{% endfor %}
```

### Components
```python
from django_rust_live import Component

class Button(Component):
    template = '<button @click="clicked">{{ label }}</button>'

    def __init__(self, label="Click"):
        self.label = label
```

## ⚡ Performance

Rust powers the core rendering engine:

| Operation | Django | Django Rust Live | Speedup |
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
- [API Reference](https://docs.django-rust.org)
- [Example Projects](examples/)
- [Contributing Guide](CONTRIBUTING.md)

## 💬 Need Help?

- 🐛 [Issue Tracker](https://github.com/django-rust/django-rust-live/issues)
- 💬 [Discord](https://discord.gg/django-rust)
- 📧 [Email](mailto:help@django-rust.org)

---

Made with ❤️ and ⚡ by the Django Rust community
