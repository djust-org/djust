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
    <div dj-offline-show>Only shown when offline</div>
    <button dj-offline-disable>Disabled when offline</button>
</body>
</html>
```

### 2. Use PWA mixins in your LiveViews

```python
from djust.pwa.mixins import PWAMixin, OfflineMixin

class MyView(PWAMixin, LiveView):
    def mount(self, request):
        self.enable_offline()  # Enable offline functionality
        self.items = []

    def add_item(self, name):
        # Works offline with automatic sync
        self.items.append(name)
        self.sync_when_online()  # Queue for sync
```

### 3. Generate service worker

```bash
python manage.py generate_sw
```

## PWA Mixins

### PWAMixin

Base mixin for PWA functionality:

```python
class PWAMixin:
    def enable_offline(self, storage='indexeddb'):
        """Enable offline mode with specified storage backend."""

    def disable_offline(self):
        """Disable offline functionality."""

    def is_offline_enabled(self):
        """Check if offline mode is enabled."""
```

### OfflineMixin

Enhanced offline state management:

```python
class OfflineMixin(PWAMixin):
    def save_offline_state(self, key, data):
        """Save data for offline access."""

    def load_offline_state(self, key, default=None):
        """Load saved offline data."""

    def sync_when_online(self):
        """Queue current state for sync when online."""

    def handle_online(self):
        """Called when connection restored."""
```

### SyncMixin

Automatic synchronization:

```python
class SyncMixin(OfflineMixin):
    def queue_sync(self, action, data):
        """Queue action for background sync."""

    def process_sync_queue(self):
        """Process queued sync actions."""
```

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

### dj-offline-hide

Hide elements when offline:

```html
<div dj-offline-hide>
    <button dj-click="save_to_server">Save Online</button>
</div>
```

### dj-offline-show

Show elements only when offline:

```html
<div dj-offline-show>
    <p>You're working offline. Changes will sync when online.</p>
</div>
```

### dj-offline-disable

Disable form elements when offline:

```html
<button dj-offline-disable dj-click="submit">
    Submit Form
</button>
```

### dj-offline-queued

Show visual feedback for queued actions:

```html
<div dj-offline-queued>
    <span class="sync-indicator">Syncing...</span>
</div>
```

## Storage Backends

### IndexedDB Backend

For structured offline data:

```javascript
// Automatic via PWAMixin.enable_offline('indexeddb')
```

### LocalStorage Backend

For simple key-value storage:

```javascript
// Automatic via PWAMixin.enable_offline('localstorage')
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

### Offline Form with Validation

```python
class ContactForm(SyncMixin, LiveView):
    template_name = 'contact.html'

    def mount(self, request):
        self.enable_offline()
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
                self.queue_sync('submit_contact', self.form_data)
                self.show_message("Form saved. Will submit when online.")
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
3. Mix `PWAMixin` into existing LiveViews
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
