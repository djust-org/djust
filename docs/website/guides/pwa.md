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
from djust.pwa.mixins import PWAMixin, OfflineMixin

class MyView(OfflineMixin, LiveView):
    template_name = 'app.html'

    def mount(self, request):
        self.enable_offline()
        self.items = self.load_offline_state('items', [])

    def add_item(self, name):
        self.items.append(name)
        self.save_offline_state('items', self.items)
        self.sync_when_online()
```

### 3. Generate the Service Worker

```bash
python manage.py generate_sw
```

## PWA Mixins

### PWAMixin

Base mixin for PWA functionality:

| Method | Description |
|--------|-------------|
| `enable_offline(storage='indexeddb')` | Enable offline mode with specified storage backend |
| `disable_offline()` | Disable offline functionality |
| `is_offline_enabled()` | Check if offline mode is enabled |

### OfflineMixin

Enhanced offline state management (extends PWAMixin):

| Method | Description |
|--------|-------------|
| `save_offline_state(key, data)` | Save data for offline access |
| `load_offline_state(key, default=None)` | Load saved offline data |
| `sync_when_online()` | Queue current state for sync when connection returns |
| `handle_online()` | Called when connection is restored |

### SyncMixin

Automatic background synchronization (extends OfflineMixin):

| Method | Description |
|--------|-------------|
| `queue_sync(action, data)` | Queue an action for background sync |
| `process_sync_queue()` | Process all queued sync actions |

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
| `dj-offline-queued` | Show visual feedback for queued actions |

```html
<div dj-offline-hide>
    <button dj-click="save_to_server">Save</button>
</div>
<div dj-offline-show>
    <p>Changes will sync when you're back online.</p>
</div>
```

## Service Worker Configuration

```python
# settings.py
DJUST_PWA = {
    'MANIFEST': {
        'name': 'My Application',
        'short_name': 'MyApp',
        'theme_color': '#007bff',
        'background_color': '#ffffff',
        'display': 'standalone',
        'icons': [
            {'src': '/static/icons/icon-192.png', 'sizes': '192x192', 'type': 'image/png'}
        ]
    },
    'SERVICE_WORKER': {
        'CACHE_NAME': 'djust-v1',
        'STRATEGY': 'cache-first',  # or 'network-first'
        'URLS_TO_CACHE': ['/static/css/app.css', '/static/js/app.js'],
        'OFFLINE_URL': '/offline/',
        'EXCLUDE_PATTERNS': [r'/admin/', r'/api/websocket/']
    }
}
```

## Example: Offline Todo App

```python
from djust import LiveView
from djust.pwa.mixins import OfflineMixin

class TodoView(OfflineMixin, LiveView):
    template_name = 'todos.html'

    def mount(self, request):
        self.enable_offline()
        self.todos = self.load_offline_state('todos', [])

    def add_todo(self, text):
        todo = {'id': len(self.todos), 'text': text, 'done': False}
        self.todos.append(todo)
        self.save_offline_state('todos', self.todos)
        self.sync_when_online()

    def toggle_todo(self, todo_id):
        for todo in self.todos:
            if todo['id'] == todo_id:
                todo['done'] = not todo['done']
        self.save_offline_state('todos', self.todos)
        self.sync_when_online()
```

## Management Commands

```bash
# Basic generation
python manage.py generate_sw

# Custom output path
python manage.py generate_sw --output static/custom-sw.js

# Include static file collection
python manage.py generate_sw --collect-static

# Custom version
python manage.py generate_sw --version 2.1.0
```

## Adding PWA to an Existing App

1. Add `{% load djust_pwa %}` to your base template
2. Include `{% djust_pwa_head %}` in your `<head>`
3. Mix `PWAMixin` or `OfflineMixin` into your LiveViews
4. Run `python manage.py generate_sw`
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
- Use `network-first` strategy for dynamic content and `cache-first` for static assets.
- Validate data in the sync queue before sending to the server.
- Consider authentication token expiry when designing offline flows.
- Test offline behavior in Chrome DevTools (Application > Service Workers > Offline).
