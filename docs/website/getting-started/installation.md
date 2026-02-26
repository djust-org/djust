# Installation

Get djust running in your Django project.

## Requirements

- Python 3.10+
- Django 4.2+
- Django Channels 4.0+
- Rust toolchain (for building from source)

## Install

```bash
pip install djust
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv add djust
```

## Configure Django

Add `channels` and `djust` to `INSTALLED_APPS` in `settings.py`:

```python
INSTALLED_APPS = [
    # ... your existing apps ...
    "channels",
    "djust",
]

# Required: point Django at your ASGI application
ASGI_APPLICATION = "myproject.asgi.application"
```

## Set Up ASGI

djust requires ASGI (not WSGI) because it uses WebSockets. Replace or update `myproject/asgi.py`:

```python
import os
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
import myproject.routing

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": AuthMiddlewareStack(
        URLRouter(myproject.routing.websocket_urlpatterns)
    ),
})
```

## WebSocket Routing

Create `myproject/routing.py`:

```python
from django.urls import path
from djust.websocket import LiveViewConsumer

websocket_urlpatterns = [
    path("ws/live/", LiveViewConsumer.as_asgi()),
]
```

## Run the Dev Server

Use `uvicorn` (or `daphne`) instead of `manage.py runserver` — the standard Django dev server is WSGI-only and does not support WebSockets:

```bash
uvicorn myproject.asgi:application --reload
```

Or add a Makefile target:

```makefile
start:
    uvicorn myproject.asgi:application --reload
```

## Building from Source

If you're contributing to djust or want the latest unreleased version:

```bash
git clone https://github.com/djust-org/djust.git
cd djust

# Install Rust (if needed)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source $HOME/.cargo/env

# Build the Rust extension (dev build, ~10s)
uv venv && source .venv/bin/activate
uv pip install maturin
maturin develop

# For production builds (2-3 minutes, more optimized)
maturin develop --release
```

## Next Steps

- [Your First LiveView](./first-liveview.md) — build a reactive counter in minutes
- [Core Concepts](./core-concepts.md) — understand how djust works
