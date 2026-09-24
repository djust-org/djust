<p align="center">
  <img src="branding/logo/djust-wordmark-dark.png" alt="djust" width="300" />
</p>

<p align="center"><strong>Reactive server-side rendering for Django, powered by Rust</strong></p>

djust brings Phoenix LiveView-style reactive views to Django. You write
server-side Python; the browser updates itself over a WebSocket. There is no
JavaScript to write, no bundler, and no build step in your project.

**[djust.org](https://djust.org)** · **[Documentation](https://docs.djust.org)** · **[Quick Start](https://docs.djust.org/getting-started/)** · **[Examples](https://djust.org/examples/)**

[![PyPI version](https://img.shields.io/pypi/v/djust.svg)](https://pypi.org/project/djust/)
[![CI](https://github.com/djust-org/djust/actions/workflows/test.yml/badge.svg)](https://github.com/djust-org/djust/actions/workflows/test.yml)
[![MIT License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Django 4.2+](https://img.shields.io/badge/django-4.2+-green.svg)](https://www.djangoproject.com/)
[![PyPI Downloads](https://img.shields.io/pypi/dm/djust.svg)](https://pypi.org/project/djust/)

```python
from djust import LiveView, event_handler

class CounterView(LiveView):
    template_string = """
    <div dj-root>
        <h1>Count: {{ count }}</h1>
        <button dj-click="increment">+</button>
        <button dj-click="decrement">-</button>
    </div>
    """

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler
    def increment(self):
        self.count += 1  # the page updates; no JavaScript

    @event_handler
    def decrement(self):
        self.count -= 1
```

## Why djust

- **One codebase.** Views, state and event handlers are Python. No API layer, no frontend build.
- **Small wire traffic.** A Rust virtual DOM diffs each render and sends only the changed patches.
- **Fast templates.** A Rust template engine renders Django templates 7–11x faster on variable- and filter-heavy pages ([Performance](#performance)).
- **Tiny client.** ~67 KB gzipped runtime, injected automatically. Nothing to bundle.
- **Django all the way down.** Your templates, forms, auth, permissions and ORM work as they are, with CSRF, escaping and per-view authorization built in.
- **Resilient transport.** WebSocket with automatic reconnection and an HTTP fallback.

## Getting started

The fastest start is the scaffold, which configures everything below:

```bash
pip install djust
djust new myproject
```

To add djust to an existing project instead:

**1. Settings.** Add the apps and a channel layer to `settings.py`:

```python
INSTALLED_APPS = [
    # ... your existing apps ...
    "channels",
    "djust",
]

ASGI_APPLICATION = "myproject.asgi.application"

CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
```

**2. `asgi.py`.** Route WebSockets to djust:

```python
import os
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from djust.websocket import LiveViewConsumer
from django.urls import path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": AuthMiddlewareStack(
        URLRouter([path("ws/live/", LiveViewConsumer.as_asgi())])
    ),
})
```

**3. A view, a URL and a template.**

```python
# myapp/views.py
from djust import LiveView, event_handler

class CounterView(LiveView):
    template_name = "counter.html"

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler
    def increment(self):
        self.count += 1
```

```python
# myproject/urls.py
from django.urls import path
from myapp.views import CounterView

urlpatterns = [path("counter/", CounterView.as_view(), name="counter")]
```

```html
<!-- myapp/templates/counter.html -->
{% load live_tags %}
<!DOCTYPE html>
<html>
<head>
    <title>Counter</title>
    {% djust_client_config %}
</head>
<body>
    <div dj-root>
        <h1>Count: {{ count }}</h1>
        <button dj-click="increment">+</button>
    </div>
</body>
</html>
```

**4. Run it** with `uvicorn myproject.asgi:application` and open `/counter/`.
In DEBUG, djust hot-reloads views without restarting or losing state, so you
don't need `--reload`.

## How reactivity works

On each event djust re-renders the view on the server, diffs the result
against the previous render in Rust, and sends only the patches.

| In the template | Purpose |
|---|---|
| `{% djust_client_config %}` in `<head>` | Emits client config. djust injects the client runtime into every LiveView response; you never add a `<script>` tag. |
| `dj-root` | Marks the reactive region. Only HTML inside it is diffed and patched. djust stamps `dj-view` onto it with the dotted path of the view. |
| `dj-view="myapp.views.MyView"` | Optional. Write it yourself only to name a specific view, such as an embedded or sticky view, or a template shared by several views. |
| `dj-click`, `dj-input`, `dj-change`, `dj-submit` | Send events to `@event_handler` methods. Inputs pass `value`; forms pass their fields. |
| `dj-key` or `data-key` on list items | Gives items a stable identity, so reorders become moves and keep focus, scroll position and animations. |

```html
{% for item in items %}
<div dj-key="{{ item.id }}">{{ item.name }}</div>
{% endfor %}
```

Without a key, lists are diffed by position: still correct, with more DOM
changes on reorders. Conditional attributes such as
`class="btn {% if active %}active{% endif %}"` are handled correctly too, with
or without `{% else %}`. See the [VDOM architecture guide](docs/website/advanced/vdom-architecture.md)
and the [template cheat sheet](docs/website/guides/template-cheatsheet.md).

## Rust templates for any Django view

You don't need LiveView to use the Rust engine. Point a `TEMPLATES` entry at the
backend, and your existing `TemplateView`s, `render()` calls and `{% include %}`s
render through Rust, with no WebSocket and no client runtime:

```python
TEMPLATES = [
    {
        "BACKEND": "djust.template_backend.DjustTemplateBackend",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": [...]},
    },
    {
        # admin and contrib templates still need Django's own backend
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": [...]},
    },
]
```

- **98.57%** of Django's own `template_tests` suite passes unmodified against this backend (1032 of the 1,047 cells that reach an engine at all; measured by `scripts/run-django-template-suite.py` against the Django tag matching the installed version — see [`docs/TEMPLATE_BACKEND.md`](docs/TEMPLATE_BACKEND.md) for the full breakdown and what the remaining cells are). <!-- django-suite-claim -->
- Rendering is **7–11x faster** on variable- and filter-heavy templates; static markup is not faster, because there is nothing to accelerate.

`djust new` configures this backend for you.

## Performance

Full render, same template on both engines, parsed once on each side.
`benchmarks/benchmark.py` on an Apple silicon laptop, Django 5.2.16, Python
3.12, `DEBUG=False`, release build:

| Template | Rows | Django | djust | Speedup |
|---|---|---|---|---|
| Static markup | 10,000 | 2.85 ms | 2.81 ms | **1.0x** |
| Simple list (2 vars/row) | 10,000 | 63.7 ms | 8.96 ms | **7.1x** |
| Filtered list (typical page) | 10,000 | 241 ms | 21.5 ms | **11.2x** |

The speedup grows with variable and filter density. The table leaves out the
bigger win on updates, where djust sends a diff and plain Django re-sends the
whole page. Reproduce it with `make build && python benchmarks/benchmark.py`.
The script refuses a debug build, which is roughly 7.6x slower.

## Learn more

| Topic | Guide |
|---|---|
| Directives, filters and tags | [Template cheat sheet](docs/website/guides/template-cheatsheet.md) |
| Reusable components and theming | [Components](docs/website/guides/components.md) |
| Tailwind and Bootstrap setup | [CSS frameworks](docs/website/guides/css-frameworks.md) |
| `dj-patch`, `dj-navigate`, `live_redirect()` | [Navigation](docs/website/guides/navigation.md) |
| `@debounce`, `@throttle`, `@cache`, `@background` | [State management](docs/state-management/STATE_MANAGEMENT_QUICKSTART.md) |
| Event handler conventions | [Event handlers](docs/EVENT_HANDLERS.md) |
| Debug panel (`Ctrl/Cmd+Shift+D`) | [Debug panel](docs/DEBUG_PANEL.md) |
| Production with uvicorn, Redis and Nginx | [Deployment](docs/website/guides/deployment.md) |
| Building with an AI coding agent | [Conventions](docs/ai/conventions.md) · [AI references](docs/ai/README.md) |

Everything else is at [docs.djust.org](https://docs.djust.org). Working
examples live in [examples/demo_project](examples/demo_project).

## Architecture

```
Browser        client runtime (~67 KB gz) ── events up, patches down
   ↕ WebSocket (or HTTP fallback)
Django         LiveView classes, event handlers, state (Python, Channels)
   ↕ PyO3
Rust core      template engine · VDOM diff · HTML parser · MessagePack
```

## Development

Building from source needs Rust 1.70+ and [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/djust-org/djust.git
cd djust
make install   # dependencies via uv, then a release build of the Rust core
make test      # Python + Rust + JavaScript
make help      # everything else
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and the [testing guide](docs/TESTING.md).

## Security

CSRF protection, automatic escaping in the Rust engine, WebSocket origin
validation and session auth, rate limiting, and view- and handler-level
permissions are built in; `manage.py djust_audit` reports your views' auth
posture. Report vulnerabilities to security@djust.org (see [SECURITY.md](SECURITY.md)).

## Community

- [djust.org](https://djust.org) · [Documentation](https://docs.djust.org) · [Issues](https://github.com/djust-org/djust/issues) · support@djust.org
- [Sponsor on GitHub](https://github.com/sponsors/djust-org), or star the repo to help others find it.

MIT licensed ([LICENSE](LICENSE)). Inspired by [Phoenix LiveView](https://hexdocs.pm/phoenix_live_view/);
built with [PyO3](https://pyo3.rs/) and [html5ever](https://github.com/servo/html5ever).
