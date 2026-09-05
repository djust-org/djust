# Quick Start

Get a LiveView running in under 5 minutes.

## Prerequisites

- Python 3.10+
- Django 4.2+
- Django Channels 4.0+ (for WebSocket support)

## Install

```bash
pip install djust
```

## Setup

1. Add to `INSTALLED_APPS` in `settings.py`:

```python
INSTALLED_APPS = [
    # ...
    'channels',
    'djust',
]

ASGI_APPLICATION = 'myproject.asgi.application'

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels.layers.InMemoryChannelLayer'
    }
}
```

2. Create `myproject/routing.py`:

```python
from django.urls import path
from djust.websocket import LiveViewConsumer

websocket_urlpatterns = [
    path("ws/live/", LiveViewConsumer.as_asgi()),
]
```

3. Configure `asgi.py`:

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

## Your First LiveView

### 1. Create the View

```python
# myapp/views.py
from djust import LiveView
from djust.decorators import event_handler

class CounterView(LiveView):
    template_name = "myapp/counter.html"

    def mount(self, request, **kwargs):
        self.count = 0

    def get_context_data(self, **kwargs):
        return {"count": self.count}

    @event_handler()
    def increment(self, **kwargs):
        self.count += 1

    @event_handler()
    def decrement(self, **kwargs):
        self.count -= 1
```

### 2. Create the Template

```html
<!-- myapp/templates/myapp/counter.html -->
<!DOCTYPE html>
<html>
<head>
    <title>Counter</title>
    {% load live_tags %}
    {% djust_client_config %}
</head>
<body>
    <div dj-root>
        <h1>Count: {{ count }}</h1>
        <button dj-click="decrement">-</button>
        <button dj-click="increment">+</button>
    </div>
</body>
</html>
```

### LiveView Root Container

Every LiveView template needs **one** attribute on its root element: **`dj-root`**, marking the subtree djust patches on updates. That is all you write — the server stamps `dj-view` onto it with the dotted path of the view rendering the page, so the path is never duplicated into your template.

Given either attribute djust fills in the other (with `dj-view` alone, the client stamps `dj-root`). With **neither**, no `dj-view` reaches the browser, no WebSocket opens, and the page is silently static — no error, it just never updates.

```html
<body>
    <div dj-root>
        <!-- Only this subtree is patched on state changes -->
        <p>{{ content }}</p>
    </div>
</body>
```

Write `dj-view="myapp.views.MyView"` yourself only when you need to name a specific view: an embedded or sticky view, or one template shared by several views. It is a literal dotted path — there is no `dj_view_id` context variable.

### Event Handler Parameters

All event handlers receive metadata from the client (such as `_targetElement`). Always include `**kwargs` in your handler signature:

```python
@event_handler()
def search(self, value: str = "", **kwargs):
    self.query = value
```

Without `**kwargs`, you will get a validation error when the handler is called.

### 3. Add a URL

```python
# myapp/urls.py
from django.urls import path
from myapp.views import CounterView

urlpatterns = [
    path("counter/", CounterView.as_view(), name="counter"),
]
```

### 4. Run It

```bash
uvicorn myproject.asgi:application
```

No `--reload` needed **if `watchdog` is installed** — it ships in the `dev`
extra (`pip install 'djust[dev]'`), not in the base install, and djust's
hot-view-replacement (HVR) silently stays off without it
(`python/djust/__init__.py:292`). HVR auto-enables when
`DEBUG=True` and reloads template/Python changes **without** dropping view
state (a `--reload` process restart resets counters, form input, and scroll
position).

Visit **http://localhost:8000/counter/** -- the buttons update the count instantly.

## Common First Errors

### "No containers found"

**Cause:** No root element carries `dj-root` (or `dj-view`), so djust has nothing to mount.

**Fix:** Put `dj-root` on a root element (typically a wrapper `<div>` inside `<body>`). djust stamps `dj-view` onto it server-side with your view's dotted path. If you wrote `dj-view` yourself, make sure it is a literal path such as `myapp.views.CounterView` — djust does not provide a `dj_view_id` context variable, so `dj-view="{{ dj_view_id }}"` renders empty and cannot mount.

### "DOM not updating" / DJE-053

**Cause:** The template has `dj-view` but is missing `dj-root`.

**Fix:** Add `dj-root` to the element that wraps your reactive content:

```html
<div dj-root>
    <!-- reactive content here -->
</div>
```

### "Validation error on event handler"

**Cause:** The handler signature is missing `**kwargs`. The client sends metadata parameters (like `_targetElement`) that your handler must accept.

**Fix:** Add `**kwargs` to every event handler:

```python
# Before (breaks)
@event_handler()
def increment(self):
    self.count += 1

# After (works)
@event_handler()
def increment(self, **kwargs):
    self.count += 1
```

## Next Steps

- [Event Handlers](docs/EVENT_HANDLERS.md) — parameter conventions, type coercion, debugging
- [State Management](docs/STATE_MANAGEMENT_QUICKSTART.md) — debounce, throttle, optimistic updates
- [Forms](docs/website/forms/index.md) — real-time form validation
- [Loading States](docs/website/guides/loading-states.md) — background work, streaming, optimistic updates
