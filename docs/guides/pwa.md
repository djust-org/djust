# PWA (Progressive Web App) Support

djust v0.3.0 introduces comprehensive Progressive Web App support, enabling offline-first applications with automatic synchronization.

## Overview

The PWA implementation provides:
- **Service Worker Integration** - Automatic caching of HTML responses
- **Offline State Management** - IndexedDB/LocalStorage abstraction
- **Optimistic UI Updates** - Immediate feedback with sync when online
- **Offline Awareness** - Template directives for offline states
- **Automatic Manifest Generation** - PWA manifest with customizable settings

## Quick Start

### 1. Enable PWA in your templates

```html
{% load djust_pwa %}
<!DOCTYPE html>
<html>
<head>
    {% djust_pwa_head %}
    <!-- Or individual components -->
    {% djust_pwa_manifest %}
    {% djust_sw_register %}
</head>
<body>
    {% djust_offline_indicator %}
    {% djust_offline_styles %}

    <div dj-offline-hide>Only shown when online</div>
    <div dj-offline-show style="display: none;">Only shown when offline</div>
    <button dj-offline-disable>Disabled when offline</button>
</body>
</html>
```

### 2. Use PWA mixins in your LiveViews

```python
from djust.pwa.mixins import OfflineMixin

class MyView(OfflineMixin, LiveView):
    def mount(self, request):
        self.items = self.storage.get("items", [])  # offline-capable KV store

    def add_item(self, name):
        # Optimistic offline create: queued for sync automatically
        created = self.create_offline("Item", {"name": name})
        self.items.append(created)
        self.storage.set("items", self.items)
```

### 3. Generate service worker

```bash
python manage.py generate_sw
```

## PWA Mixins

### PWAMixin (`python/djust/pwa/mixins.py`)

Install-prompt / update handling for the PWA shell:

- `get_pwa_config() -> dict`
- `register_pwa_handlers()`
- `handle_install_prompt()`
- `handle_app_update(version)`

### OfflineMixin

Offline state + optimistic mutations (standalone — it does **not** subclass
`PWAMixin`; mix both if you want install handling too):

- `storage` — property returning the `OfflineStorage` KV backend
  (`.get/.set/.delete/.clear/.keys/.size`; backend picked from
  `self.offline_storage`, defaulting per view)
- `sync_queue` — property returning the `SyncQueue` of pending
  create/update/delete actions
- `is_online() -> bool`
- `get_cached_or_fetch(key, queryset, **kwargs) -> list[dict]`
- `create_offline(model_name, data) -> dict` — optimistic create with a
  temporary ID, queued for sync
- `update_offline(model_name, obj_id, data) -> dict`
- `delete_offline(model_name, obj_id) -> bool`
- `sync_when_online()` — flush the sync queue when connectivity returns
- `get_offline_state() -> dict`
- `handle_connection_change(online)`

### SyncMixin

Synchronization of offline data (combine **after** `OfflineMixin`:
`class DataView(SyncMixin, OfflineMixin, LiveView)`):

- Class knobs: `sync_model`, `sync_conflict_strategy`
  (`'client_wins'|'server_wins'|'merge_by_timestamp'|'manual_resolution'` —
  the four `MergeStrategy` values at `pwa/sync.py:20-23`; an unrecognised
  string falls back to `client_wins` **silently**, so a typo here loses data
  rather than raising), `sync_batch_size`,
  `sync_timeout`
- Hook: `sync_create_item(action_data)` (and siblings) for custom sync logic
- The queue is processed automatically; there is no `queue_sync()` /
  `process_sync_queue()` public API

## Template Tags

### djust_pwa_head

Complete PWA setup in one tag:

```html
{% djust_pwa_head name="My App" theme_color="#007bff" %}
```

### djust_pwa_manifest

Generate PWA manifest:

```html
{% djust_pwa_manifest
   name="My Application"
   short_name="MyApp"
   theme_color="#007bff"
   background_color="#ffffff"
   display="standalone" %}
```

### djust_sw_register

Register service worker with custom options:

```html
{% djust_sw_register
   sw_url="/sw.js"
   scope="/" %}
```

### djust_offline_indicator

Visual offline status indicator:

```html
{% djust_offline_indicator
   offline_text="You're offline"
   online_class="online-banner"
   show_when="offline" %}
```

### djust_offline_styles

CSS for offline directives:

```html
{% djust_offline_styles %}
```

## Offline Directives

**Use the bare-name forms** — `dj-offline-hide`, `dj-offline-show`,
`dj-offline-disable`. They are what ships working: `{% djust_offline_styles %}`
emits CSS keyed on exactly those attribute selectors
(`templatetags/djust_pwa.py:328`), and `{% djust_offline_indicator %}` emits
them itself (`djust_pwa.py:227-229`). They need no JavaScript — visibility
follows the `djust-offline` / `djust-online` class on `<body>`.

A value form (`dj-offline="<show|hide|disable|enable|queue>"`) is also parsed,
but only by `static/djust/js/pwa.js`, which is **not** in `static/djust/src/`,
is **not** in the built bundle, and is loaded by no template tag — so on a
default install nothing evaluates it. Prefer the bare names until that file is
either bundled or removed (the two forms are tracked as a documentation-vs-code
gap, not a supported choice).

### Only shown when online / offline

```html
<div dj-offline="hide">
    <button dj-click="save_to_server">Save Online</button>
</div>
<div dj-offline="show">
    <p>You're working offline. Changes will sync when online.</p>
</div>
```

### Disable / enable form elements

```html
<button dj-offline="disable" dj-click="submit">Submit Form</button>
```

### Queue the action when offline

```html
<button dj-offline="queue" dj-click="submit">Submit</button>
```

`queue` attaches a fallback click handler while offline so the intent is
captured (and synced) instead of lost.

## Storage Backends

### IndexedDB Backend

For structured offline data:

```python
# `offline_storage` is the NAMESPACE, not a backend selector: it is passed as
# `storage_name=` (pwa/mixins.py:189). The backend comes from
# `DJUST_CONFIG['PWA_OFFLINE_STORAGE']` and defaults to `indexeddb`
# (pwa/storage.py:529-544), so both views below use the SAME backend.
class TodoView(OfflineMixin, LiveView):
    offline_storage = "todos"  # named backend; IndexedDB-backed via the SW
```

### A second namespace (still the configured backend)

For simple key-value storage:

```python
# Same API — the backend name decides where OfflineStorage persists
class PrefView(OfflineMixin, LiveView):
    offline_storage = "prefs"  # simple key-value data
```

## Service Worker Configuration

### Basic Configuration

```python
# settings.py
DJUST_PWA = {
    'SERVICE_WORKER': {
        'CACHE_NAME': 'djust-v1',
        'URLS_TO_CACHE': [
            '/static/css/app.css',
            '/static/js/app.js',
        ],
        'OFFLINE_URL': '/offline/',
    }
}
```

### Advanced Configuration

```python
DJUST_PWA = {
    'MANIFEST': {
        'name': 'My Application',
        'short_name': 'MyApp',
        'theme_color': '#007bff',
        'background_color': '#ffffff',
        'display': 'standalone',
        'icons': [
            {
                'src': '/static/icons/icon-192.png',
                'sizes': '192x192',
                'type': 'image/png'
            }
        ]
    },
    'SERVICE_WORKER': {
        'STRATEGY': 'cache-first',  # or 'network-first'
        'EXCLUDE_PATTERNS': [
            r'/admin/',
            r'/api/websocket/',
        ]
    }
}
```

## Management Commands

### generate_sw

Generate service worker file:

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

## Examples

### Offline Todo App

```python
from djust import LiveView
from djust.pwa.mixins import OfflineMixin

class TodoView(OfflineMixin, LiveView):
    template_name = 'todos.html'

    def mount(self, request):
        self.todos = self.storage.get('todos', [])

    def add_todo(self, text):
        todo = self.create_offline('Todo', {'text': text, 'done': False})
        self.todos.append(todo)
        self.storage.set('todos', self.todos)

    def toggle_todo(self, todo_id):
        for todo in self.todos:
            if todo['id'] == todo_id:
                todo['done'] = not todo['done']
                self.update_offline('Todo', todo_id, {'done': todo['done']})
        self.storage.set('todos', self.todos)
        self.sync_when_online()
```

### Offline Form with Validation

```python
from djust.pwa.mixins import OfflineMixin

class ContactForm(OfflineMixin, LiveView):
    template_name = 'contact.html'

    def mount(self, request):
        self.form_data = {}
        self.errors = {}

    def update_field(self, field, value):
        self.form_data[field] = value
        self.validate_field(field, value)

    def validate_field(self, field, value):
        if field == 'email' and '@' not in value:
            self.errors[field] = 'Invalid email'
        else:
            self.errors.pop(field, None)

    def submit_form(self):
        if not self.errors:
            if self.is_online():
                self.send_to_server()
            else:
                self.create_offline('ContactSubmission', self.form_data)
                self.storage.set('last_message', 'Form saved. Will submit when online.')
```

## Browser Support

- **Chrome/Edge**: Full support
- **Firefox**: Full support
- **Safari**: Partial support (no background sync)
- **Mobile Safari**: Full support with install prompt

## Performance Considerations

- Service worker caching reduces server load
- Offline storage keeps UI responsive
- Background sync minimizes data loss
- Optimistic updates improve perceived performance

## Security Notes

- Service workers require HTTPS in production
- Offline data is stored locally and may persist
- Sync queue should validate data before sending
- Consider authentication tokens in offline mode

## Migration from v0.2.x

No breaking changes. To add PWA support to existing views:

1. Add `{% load djust_pwa %}` to templates
2. Include `{% djust_pwa_head %}` in your base template
3. Mix `PWAMixin` (install/update handling) and/or `OfflineMixin` (offline data) into existing LiveViews
4. Run `python manage.py generate_sw`
5. Deploy with HTTPS

## Troubleshooting

### Service Worker Not Registering

Check:
- HTTPS is enabled (required except localhost)
- Service worker file is accessible
- No console errors in browser dev tools

### Offline Storage Not Working

Check:
- IndexedDB is enabled in browser
- Storage quota not exceeded
- PWAMixin is properly mixed into view

### Sync Not Triggering

Check:
- Connection events are firing
- Sync queue has pending actions
- Server endpoints are accessible

## API Reference

<!-- TODO: Create docs/api/pwa.md with full API documentation -->
