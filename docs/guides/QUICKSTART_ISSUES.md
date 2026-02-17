# Quickstart Testing Report

**Tested**: 2026-02-16
**Tester**: Claude (automated walkthrough of `docs/guides/QUICKSTART.md`)

---

## Summary

The current quickstart (`docs/guides/QUICKSTART.md`) has **6 issues**, including 2 that will cause complete failures for new users following the "Create Your First LiveView" section.

---

## Issues Found

### Issue 1 — Wrong attribute name: `template_string` vs `template`

**Severity**: Critical — code silently has no template
**Section**: "Create Your First LiveView"

The quickstart example uses:
```python
class HelloView(LiveView):
    template_string = """..."""
```

The correct attribute name is `template` (not `template_string`):
```python
class HelloView(LiveView):
    template = """..."""
```

`LiveView` defines `template: Optional[str] = None`. There is no `template_string` attribute. Setting `template_string` on a LiveView subclass silently does nothing — the view will render empty or fail.

**Fix**: Change `template_string` → `template` in the quickstart example.

---

### Issue 2 — Missing `@event_handler` decorator on handlers

**Severity**: Critical — events are blocked with error in strict mode
**Section**: "Create Your First LiveView"

The quickstart shows:
```python
def update_name(self, value):
    self.name = value

def increment(self):
    self.clicks += 1
```

But the default `event_security` config is `"strict"`, which requires all event handlers to be decorated with `@event_handler`. Undecorated methods are blocked with:

```
Event 'increment' on HelloView is not decorated with @event_handler.
  Fix: Add the decorator:
    @event_handler
    def increment(self, **kwargs):
```

The correct form (per `CLAUDE.md`):
```python
from djust.decorators import event_handler

@event_handler()
def update_name(self, value: str = "", **kwargs):
    self.name = value

@event_handler()
def increment(self, **kwargs):
    self.clicks += 1
```

**Fix**: Add `@event_handler()` decorator to all handler examples in the quickstart.

---

### Issue 3 — ASGI/WebSocket setup not documented

**Severity**: High — LiveView is purely WebSocket, WSGI runserver won't work
**Section**: "Run the Demo" and "Create Your First LiveView"

The quickstart says to run `python manage.py runserver` (Step 3). Django's standard WSGI development server does **not** support WebSockets. djust LiveViews require an ASGI server.

Required setup that's missing from the quickstart:
1. An `asgi.py` with `ProtocolTypeRouter` + WebSocket routing
2. A `routing.py` file with `LiveViewConsumer`
3. `channels` in `INSTALLED_APPS`
4. Running with uvicorn or daphne, not `runserver`

Example `asgi.py` (from demo project):
```python
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
import myproject.routing

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": AuthMiddlewareStack(
        URLRouter(myproject.routing.websocket_urlpatterns)
    ),
})
```

Example `routing.py`:
```python
from django.urls import path
from djust.websocket import LiveViewConsumer

websocket_urlpatterns = [
    path('ws/live/', LiveViewConsumer.as_asgi()),
]
```

Correct run command:
```bash
uvicorn myproject.asgi:application --reload
```

**Fix**: Add ASGI setup section before "Create Your First LiveView", and change `runserver` to uvicorn.

---

### Issue 4 — Demo runs on port 8002, quickstart says port 8000

**Severity**: Low — confusing for users following the guide
**Section**: "Run the Demo" → "Open Browser"

Quickstart says: **http://localhost:8000**
Demo server (`make start`) runs on: **http://localhost:8002**

**Fix**: Update quickstart to reference port 8002, or document both.

---

### Issue 5 — `maturin develop --release` takes a long time

**Severity**: Low — UX issue (the dev build works fine for development)
**Section**: "Build the Rust Extension"

The quickstart says `maturin develop --release`. A release build compiles with full optimizations and takes several minutes. For quickstart purposes, `maturin develop` (dev build, ~10s) is sufficient and much faster.

**Fix**: Use `maturin develop` (no `--release`) in the quickstart, with a note that `--release` is for production performance.

---

### Issue 6 — `maturin develop --uv` installs into project venv, not user's venv

**Severity**: Low — confusing install behavior
**Section**: "Build the Rust Extension"

When running `maturin develop --uv` from inside the cloned djust repo, maturin installs the package into the repo's own `.venv`, not the user's own scratch environment. If the user created a separate venv in a different directory (per the quickstart), the install goes to the wrong place.

The correct approach is to activate the target venv first, then run `maturin develop`:
```bash
uv venv
source .venv/bin/activate
maturin develop
```

Or use:
```bash
maturin develop --uv  # (from inside the djust repo, installs to djust's .venv)
```

**Fix**: Clarify that maturin must be run from the djust repo directory with the venv activated, and drop the `--release` flag.

---

## Updated "Create Your First LiveView" Example

Here is the corrected version of the main quickstart example:

**`myapp/views.py`:**
```python
from djust import LiveView
from djust.decorators import event_handler


class HelloView(LiveView):
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

**`myproject/routing.py`:**
```python
from django.urls import path
from djust.websocket import LiveViewConsumer

websocket_urlpatterns = [
    path('ws/live/', LiveViewConsumer.as_asgi()),
]
```

**`myproject/asgi.py`:**
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

**`myproject/urls.py`:**
```python
from django.urls import path
from myapp.views import HelloView

urlpatterns = [
    path('hello/', HelloView.as_view()),
]
```

**`settings.py` additions:**
```python
INSTALLED_APPS = [
    # ... standard apps ...
    'channels',
    'djust',
]

ASGI_APPLICATION = 'myproject.asgi.application'
```

**Run with:**
```bash
uvicorn myproject.asgi:application --reload
```

---

## What Worked

- `uv venv` + `maturin develop` (dev build): works correctly, ~10s build
- `python manage.py migrate` for demo project: works correctly
- `make start-bg` / `make stop`: works correctly
- Demo server on port 8002 returns HTML: works correctly
- `from djust import LiveView, Component`: works correctly
- `from djust.components import AlertComponent`: works correctly
- `from djust.decorators import event_handler`: works correctly
- `template = """..."""` (correct attribute): works correctly
- Migrations apply without errors
