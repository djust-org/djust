---
title: "Progressive Web App (PWA) Support"
slug: pwa
section: guides
order: 8
level: intermediate
description: "Add offline support, service workers, and installability with PWAMixin"
---

# Progressive Web App (PWA) Support

djust provides built-in PWA support for offline-first applications with automatic synchronization, service worker generation, and offline-aware template directives.

## What You Get

- **Service worker integration** -- Automatic caching of HTML responses and static assets
- **Offline state management** -- IndexedDB/LocalStorage abstraction via mixins
- **Optimistic UI updates** -- Immediate feedback with sync when online
- **Offline directives** -- `dj-offline-hide`, `dj-offline-show`, `dj-offline-disable`
- **Automatic manifest generation** -- PWA manifest with customizable settings

## Quick Start

### 1. Enable PWA in Your Templates

```html
{% load djust_pwa %}
<!DOCTYPE html>
<html>
<head>
    {% djust_pwa_head name="My App" theme_color="#007bff" %}
</head>
<body>
    {% djust_offline_indicator offline_text="You're offline" %}
    {% djust_offline_styles %}

    <div dj-offline-hide>Only shown when online</div>
    <div dj-offline-show>Only shown when offline</div>
    <button dj-offline-disable dj-click="submit">Submit</button>
</body>
</html>
```

### 2. Use PWA Mixins in Your Views

```python
from djust import LiveView
from djust.decorators import event_handler
from djust.pwa.mixins import PWAMixin, OfflineMixin

class MyView(OfflineMixin, LiveView):
    template_name = 'app.html'

    def mount(self, request, **kwargs):
        # No enable call: inheriting OfflineMixin is what enables offline mode.
        self.items = self.storage.get('items', [])

    @event_handler()
    def add_item(self, name: str = "", **kwargs):
        created = self.create_offline('Item', {'name': name})
        self.items.append(created)
        self.storage.set('items', self.items)
        self.sync_when_online()
```

### 3. Generate the Service Worker

```bash
python manage.py generate_sw
```

You can also serve the service worker from a view instead of a generated
file:

```python
# urls.py
from djust.pwa import service_worker_view

urlpatterns = [
    path("sw.js", service_worker_view),
    # ...
]
```

`service_worker_view` reads the `DJUST_CONFIG['PWA_*']` keys described
below.

## PWA Mixins

### PWAMixin

Base mixin for PWA functionality:

| Method | Description |
|--------|-------------|
| `get_pwa_config()` | PWA config dict injected into the template context |
| `register_pwa_handlers()` | Register the install/update event handlers |
| `handle_install_prompt()` | Called when the install prompt is shown |
| `handle_app_update(version)` | Called when a new app version is available |

There is no `enable_offline()` / `disable_offline()` / `is_offline_enabled()` —
inheriting the mixin is what turns the behaviour on.

### OfflineMixin

Enhanced offline state management. It does **not** subclass `PWAMixin` —
combine them explicitly (`class V(OfflineMixin, PWAMixin, LiveView)`) if you
want both.

| Method | Description |
|--------|-------------|
| `storage` | Property — the `OfflineStorage` instance (`.get(key, default)` / `.set(key, value)`) |
| `sync_queue` | Property — the pending `SyncQueue` |
| `create_offline(model, data)` | Queue a create for sync; returns the optimistic record |
| `update_offline(model, obj_id, data)` | Queue an update for sync |
| `delete_offline(model, obj_id)` | Queue a delete for sync |
| `get_cached_or_fetch(key, queryset)` | Serve from cache, else evaluate the queryset |
| `sync_when_online()` | Drain the sync queue |
| `get_offline_state()` | Current offline state dict |
| `is_online()` | Always `True` server-side — see the note below |
| `handle_connection_change(online)` | Connection-change hook (no built-in caller) |

There is no `save_offline_state()` / `load_offline_state()` / `handle_online()`;
use the `storage` property directly.

### SyncMixin

Automatic background synchronization. It does **not** subclass `OfflineMixin`,
and it depends on `storage` / `sync_queue` being supplied by one — list it
**after** `OfflineMixin` (`class V(SyncMixin, OfflineMixin, LiveView)`), or the
properties raise `AttributeError`.

| Method | Description |
|--------|-------------|
| `sync_queue` | Property — the pending `SyncQueue`; enqueue via `create_offline()` / `update_offline()` / `delete_offline()` |
| `sync_manager` | Property — the `SyncManager` that runs the sync |
| `sync_create_<Model>(data)`, `sync_update_<Model>(obj_id, data)`, `sync_delete_<Model>(obj_id)` | Your hooks, e.g. `sync_create_Item`; return truthy on success. **Case-sensitive** (built as `f"sync_create_{action.model}"`) |

## Template Tags

### `{% djust_pwa_head %}`

Complete PWA setup in one tag (manifest + service worker registration):

```html
{% djust_pwa_head name="My App" theme_color="#007bff" %}
```

### `{% djust_pwa_manifest %}`

Generate the PWA manifest link:

```html
{% djust_pwa_manifest name="My App" short_name="App"
   theme_color="#007bff" background_color="#ffffff" display="standalone" %}
```

### `{% djust_sw_register %}`

Register the service worker:

```html
{% djust_sw_register sw_url="/sw.js" scope="/" %}
```

### `{% djust_offline_indicator %}`

Visual offline status banner:

```html
{% djust_offline_indicator offline_text="You're offline" show_when="offline" %}
```

## Offline Directives

| Directive | Behavior |
|-----------|----------|
| `dj-offline-hide` | Hide element when offline |
| `dj-offline-show` | Show element only when offline |
| `dj-offline-disable` | Disable form element when offline |
| `dj-offline-queued` | **Not implemented** — no client code reads this attribute; listed here only so it is not mistaken for a working directive |

The client keeps `djust-online` or `djust-offline` on `<body>`, set at page
load from `navigator.onLine` and updated on the browser's `online` /
`offline` events; the directives are CSS rules on those classes, emitted by
`{% djust_pwa_head %}` or `{% djust_offline_styles %}`, so include one of
them. This is browser network state: a WebSocket reconnect does not count as
offline. (Before 1.2.1 nothing set the classes, so `dj-offline-hide` elements
were always hidden and `dj-offline-show` elements never appeared.)

```html
<div dj-offline-hide>
    <button dj-click="save_to_server">Save</button>
</div>
<div dj-offline-show>
    <p>Changes will sync when you're back online.</p>
</div>
```

## Service Worker Configuration

There are two settings surfaces, and which one applies depends on the consumer:

- **`DJUST_CONFIG` flat `PWA_*` keys** (below) are read by `PWAMixin.get_pwa_config()`,
  `PWAManifestGenerator` / `manifest_view`, and `ServiceWorkerGenerator` /
  `service_worker_view`. The two views only take effect once you route them in
  `urls.py`.
- **Plain Django settings `DJUST_PWA_*`** are what the template tags
  (`{% djust_pwa_head %}`, `{% djust_pwa_manifest %}`) and `generate_sw` read.
  They do **not** read `DJUST_CONFIG`.

There is no `DJUST_PWA` dict setting.

```python
# settings.py
DJUST_CONFIG = {
    # Manifest
    "PWA_NAME": "My Application",
    "PWA_SHORT_NAME": "MyApp",
    "PWA_DESCRIPTION": "A djust-powered app",
    "PWA_THEME_COLOR": "#007bff",
    "PWA_BACKGROUND_COLOR": "#ffffff",
    "PWA_DISPLAY": "standalone",          # fullscreen | standalone | minimal-ui
    "PWA_ICONS": [
        {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
    ],

    # Service worker
    "PWA_CACHE_NAME": "djust-v1",
    "PWA_CACHE_STRATEGY": "cache_first",  # or "network_first" / "stale_while_revalidate"
    "PWA_PRECACHE_URLS": ["/static/css/app.css", "/static/js/app.js"],
    "PWA_OFFLINE_PAGE": "/offline/",
    "PWA_CACHE_DURATION": 86400,
    "PWA_ENABLE_BACKGROUND_SYNC": False,
    "PWA_SYNC_ENDPOINT": "/djust/pwa/sync/",
}
```

Unknown `PWA_CACHE_STRATEGY` values silently fall back to `cache_first`.

The template tags take their values from their arguments
(`{% djust_pwa_head name="My App" theme_color="#007bff" %}`) and fall back to
these plain Django settings when an argument is not passed: `DJUST_PWA_NAME`,
`DJUST_PWA_SHORT_NAME`, `DJUST_PWA_DESCRIPTION`, `DJUST_PWA_THEME_COLOR`,
`DJUST_PWA_BACKGROUND_COLOR`.

## Offline Storage

Structured offline data goes through a configurable backend, chosen once for
the project:

```python
DJUST_CONFIG = {
    "PWA_OFFLINE_STORAGE": "indexeddb",  # or "localstorage"
}
```

`indexeddb` is the default. What a view sets is **not** a backend selector:

```python
class TodoView(OfflineMixin, LiveView):
    offline_storage = "todos"   # a NAMESPACE, not a backend
```

`offline_storage` is passed through as `storage_name=` — it names the key
space this view persists under, so two views with different values keep
separate data **on the same backend**. To change where data is stored, set
`PWA_OFFLINE_STORAGE`; to change which slice of it a view owns, set
`offline_storage`.

## Example: Offline Todo App

```python
from djust import LiveView
from djust.decorators import event_handler
from djust.pwa.mixins import OfflineMixin

class TodoView(OfflineMixin, LiveView):
    template_name = 'todos.html'

    def mount(self, request, **kwargs):
        self.todos = self.storage.get('todos', [])

    @event_handler()
    def add_todo(self, text: str = "", **kwargs):
        todo = self.create_offline('Todo', {'text': text, 'done': False})
        self.todos.append(todo)
        self.storage.set('todos', self.todos)
        self.sync_when_online()

    @event_handler()
    def toggle_todo(self, todo_id: str = "", **kwargs):
        for todo in self.todos:
            if todo['id'] == todo_id:
                todo['done'] = not todo['done']
                self.update_offline('Todo', todo_id, {'done': todo['done']})
        self.storage.set('todos', self.todos)
        self.sync_when_online()
```

## Management Commands

```bash
# Basic generation
python manage.py generate_sw

# Custom output path
python manage.py generate_sw --output static/custom-sw.js

# Include static files in the cache
python manage.py generate_sw --cache-static

# Custom cache version (--sw-version: Django reserves --version)
python manage.py generate_sw --sw-version 2.1.0
```

## Adding PWA to an Existing App

1. Add `{% load djust_pwa %}` to your base template
2. Include `{% djust_pwa_head %}` in your `<head>`
3. Mix `PWAMixin` or `OfflineMixin` into your LiveViews
4. Serve the service worker: run `python manage.py generate_sw`, or route `djust.pwa.service_worker_view` at `/sw.js`
5. Deploy with HTTPS (required for service workers in production)

## Browser Support

| Browser | Support |
|---------|---------|
| Chrome / Edge | Full |
| Firefox | Full |
| Safari | Partial (no background sync) |
| Mobile Safari | Full with install prompt |

## Best Practices

- Service workers require HTTPS in production (localhost is exempt for development).
- Use the `network_first` strategy for dynamic content and `cache_first` for static assets.
- Validate data in the sync queue before sending to the server.
- Consider authentication token expiry when designing offline flows.
- Test offline behavior in Chrome DevTools (Application > Service Workers > Offline).
