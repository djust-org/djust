# Offline & PWA Support

djust provides comprehensive Progressive Web App (PWA) and offline support, enabling your LiveViews to work even when users lose connectivity. Queue events while offline, persist state to IndexedDB, and provide graceful degradation with offline-specific templates.

## Quick Start

```python
from djust import LiveView, OfflineMixin, event_handler

class FormView(OfflineMixin, LiveView):
    template_name = "form.html"
    offline_template = "form_offline.html"  # Shown when offline
    sync_events = ["submit"]  # Queue these events when offline
    persist_state = True  # Save state to IndexedDB

    def mount(self, request, **kwargs):
        self.form_data = {}
        self.submitted = False

    @event_handler
    def submit(self, **kwargs):
        # This handler runs when back online if called while offline
        self.submitted = True
        self.save_to_database(self.form_data)
```

```html
<!-- form.html -->
{% load djust_pwa %}
<!DOCTYPE html>
<html>
<head>
    {% djust_pwa_manifest name="My App" theme_color="#007bff" %}
    {% djust_sw_register %}
</head>
<body>
    <!-- Shows connection status -->
    {% djust_offline_indicator %}
    
    <form dj-submit="submit">
        <input dj-model="form_data.name" placeholder="Name">
        
        <!-- Disable submit button when offline -->
        <button type="submit" dj-offline-disable>Submit</button>
        
        <!-- Show warning when offline -->
        <p dj-offline-show class="warning">
            You're offline. Form will submit when connected.
        </p>
    </form>
</body>
</html>
```

## OfflineMixin Configuration

The `OfflineMixin` adds offline capabilities to any LiveView:

```python
from djust import LiveView, OfflineMixin

class MyView(OfflineMixin, LiveView):
    # Fallback template for offline mode
    offline_template = "offline.html"
    
    # Events to queue when offline (synced when back online)
    sync_events = ["submit", "save", "update"]
    
    # Persist state to IndexedDB when going offline
    persist_state = True
    
    # Only persist specific fields (None = all serializable fields)
    persist_fields = ["form_data", "items", "user_preferences"]
    
    # Additional templates to pre-cache for offline access
    cache_templates = ["partials/help.html"]
```

### Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `offline_template` | `str` | `None` | Template to render when offline |
| `sync_events` | `list` | `[]` | Event names to queue while offline |
| `persist_state` | `bool` | `False` | Save view state to IndexedDB |
| `persist_fields` | `list` | `None` | Specific fields to persist (None = all) |
| `cache_templates` | `list` | `[]` | Templates to pre-cache |

## Offline Template

Provide a fallback UI for when the user is offline:

```python
class DashboardView(OfflineMixin, LiveView):
    template_name = "dashboard.html"
    offline_template = "dashboard_offline.html"
    
    def get_offline_context(self):
        """Customize context for offline template."""
        context = super().get_offline_context()
        context["cached_data"] = self.get_cached_summary()
        context["last_sync"] = self.last_sync_time
        return context
```

```html
<!-- dashboard_offline.html -->
<div class="offline-notice">
    <h2>📶 You're Offline</h2>
    <p>Last synced: {{ last_sync }}</p>
    
    {% if cached_data %}
        <h3>Cached Summary</h3>
        <p>{{ cached_data.summary }}</p>
    {% endif %}
    
    <p>You'll be reconnected automatically when online.</p>
</div>
```

## Event Queueing

Events listed in `sync_events` are automatically queued when offline and synced when the connection is restored:

```python
class TodoView(OfflineMixin, LiveView):
    sync_events = ["add_item", "toggle_item", "delete_item"]
    
    @event_handler
    def add_item(self, title, **kwargs):
        # Called immediately when online, or after sync when offline
        self.items.append({"title": title, "done": False})
    
    def process_queued_events(self, events):
        """Optional: Customize how queued events are processed."""
        results = []
        for event in events:
            # Add custom logic (deduplication, conflict resolution, etc.)
            if self.should_process(event):
                result = super().process_queued_events([event])
                results.extend(result)
        return results
```

### Queue Behavior

When offline:
1. User triggers an event (e.g., clicks submit)
2. Event is saved to IndexedDB with timestamp
3. User sees toast notification: "Action queued: submit. Will sync when back online."
4. Queued count indicator updates

When back online:
1. All queued events are sent to the server in order
2. Handlers are called with original parameters
3. IndexedDB queue is cleared
4. UI updates with latest state

## State Persistence

Save and restore view state across offline sessions:

```python
class EditorView(OfflineMixin, LiveView):
    persist_state = True
    persist_fields = ["content", "cursor_position", "unsaved_changes"]
    
    def mount(self, request, **kwargs):
        self.content = ""
        self.cursor_position = 0
        self.unsaved_changes = False
    
    def get_persistable_state(self):
        """Customize what state is saved."""
        state = super().get_persistable_state()
        # Add computed or derived state
        state["word_count"] = len(self.content.split())
        return state
    
    def restore_persisted_state(self, state):
        """Customize how state is restored."""
        super().restore_persisted_state(state)
        # Validate or transform restored state
        if self.unsaved_changes:
            self.show_recovery_notice = True
```

## Offline Directives

Control element visibility and behavior based on connection status:

### dj-offline-show

Show element only when offline:

```html
<div dj-offline-show class="offline-banner">
    ⚠️ You're currently offline
</div>
```

### dj-offline-hide

Hide element when offline:

```html
<button dj-click="refresh" dj-offline-hide>
    Refresh Data
</button>
```

### dj-offline-disable

Disable element when offline:

```html
<button dj-click="submit" dj-offline-disable>
    Submit Form
</button>

<input type="text" dj-model="search" dj-offline-disable>
```

### CSS Classes

The `<body>` element automatically gets connection status classes:

```css
/* Online state */
body.djust-online .sync-status {
    color: green;
}

/* Offline state */
body.djust-offline .sync-status {
    color: red;
}

body.djust-offline .online-only {
    display: none;
}
```

## PWA Manifest

Generate a PWA manifest with the `djust_pwa_manifest` template tag:

```html
{% load djust_pwa %}
<head>
    {% djust_pwa_manifest name="My App" short_name="App" theme_color="#007bff" %}
</head>
```

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `name` | `DJUST_PWA_NAME` or "djust App" | Full app name |
| `short_name` | `DJUST_PWA_SHORT_NAME` or "djust" | Short name for home screen |
| `description` | `DJUST_PWA_DESCRIPTION` | App description |
| `theme_color` | `DJUST_PWA_THEME_COLOR` or "#007bff" | Browser chrome color |
| `background_color` | `DJUST_PWA_BACKGROUND_COLOR` or "#ffffff" | Splash screen background |
| `display` | "standalone" | Display mode |
| `start_url` | "/" | Starting URL |
| `icons` | Auto-generated | Custom icon list |

### Django Settings

Configure PWA defaults in `settings.py`:

```python
DJUST_PWA_NAME = "My Application"
DJUST_PWA_SHORT_NAME = "MyApp"
DJUST_PWA_DESCRIPTION = "A fantastic djust-powered app"
DJUST_PWA_THEME_COLOR = "#1a73e8"
DJUST_PWA_BACKGROUND_COLOR = "#ffffff"
```

## Service Worker

### Generating the Service Worker

Use the management command to generate a service worker:

```bash
# Basic generation
python manage.py generate_sw

# With static file and template caching
python manage.py generate_sw --cache-static --cache-templates

# Custom output path
python manage.py generate_sw --output static/sw.js

# Custom version
python manage.py generate_sw --version 1.0.0

# Custom file extensions and exclusions
python manage.py generate_sw --static-extensions "js,css,png,jpg" --exclude-patterns "admin,debug"
```

### Command Options

| Option | Default | Description |
|--------|---------|-------------|
| `--output` | `STATIC_ROOT/sw.js` | Output path for sw.js |
| `--cache-static` | False | Include static files in cache |
| `--cache-templates` | False | Include offline templates |
| `--version` | Timestamp | Cache version string |
| `--static-extensions` | `js,css,png,jpg,...` | File extensions to cache |
| `--exclude-patterns` | `admin,debug` | Patterns to exclude |

### Registering the Service Worker

Use the template tag to register:

```html
{% load djust_pwa %}

<!-- Default registration -->
{% djust_sw_register %}

<!-- Custom URL and scope -->
{% djust_sw_register sw_url="/my-sw.js" scope="/app/" %}
```

### All-in-One Head Tag

Include all PWA tags at once:

```html
{% load djust_pwa %}
<head>
    {% djust_pwa_head name="My App" theme_color="#007bff" %}
</head>
```

This includes:
- PWA manifest link
- Theme color meta tag
- Apple mobile web app meta tags
- Service worker registration
- Offline CSS styles

## Offline Status Indicator

Display connection status with the indicator tag:

```html
{% load djust_pwa %}

<!-- Basic indicator -->
{% djust_offline_indicator %}

<!-- Customized -->
{% djust_offline_indicator 
    online_text="Connected" 
    offline_text="No Connection"
    show_when="always" %}
```

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `online_text` | "Online" | Text when connected |
| `offline_text` | "Offline" | Text when disconnected |
| `online_class` | "djust-status-online" | CSS class when online |
| `offline_class` | "djust-status-offline" | CSS class when offline |
| `show_when` | "offline" | When to show: "always", "offline", "online" |

## JavaScript API

Access offline functionality from JavaScript:

```javascript
// Check connection status
console.log(window.djust.offline.isOnline);        // Combined status
console.log(window.djust.offline.isNetworkOnline); // Network only
console.log(window.djust.offline.isWsConnected);   // WebSocket only

// Manually queue an event
await window.djust.offline.queueEvent("save", { data: myData });

// Get all queued events
const events = await window.djust.offline.getQueuedEvents();

// Force sync queued events
await window.djust.offline.syncQueuedEvents();

// Clear queued events
await window.djust.offline.clearQueuedEvents();

// State persistence
await window.djust.offline.saveState(viewState);
const state = await window.djust.offline.loadState();
await window.djust.offline.clearState();

// Service worker control
window.djust.offline.updateServiceWorker();  // Force SW update
const version = await window.djust.offline.getServiceWorkerVersion();

// Listen for status changes
window.addEventListener('djust:online-status', (e) => {
    console.log('Online:', e.detail.online);
    console.log('Network:', e.detail.networkOnline);
    console.log('WebSocket:', e.detail.wsConnected);
});

// Listen for SW updates
window.addEventListener('djust:sw-update-available', (e) => {
    if (confirm('New version available. Update now?')) {
        window.djust.offline.updateServiceWorker();
    }
});
```

## IndexedDB Storage

djust uses IndexedDB for offline storage:

- **Database name:** `djust_offline`
- **Stores:**
  - `queued_events` — Events waiting to sync
  - `view_state` — Persisted view state

### Storage Limits

IndexedDB storage varies by browser:
- Chrome: Up to 60% of disk space
- Firefox: Up to 2GB per origin
- Safari: ~1GB per origin

## Complete Example

```python
# views.py
from djust import LiveView, OfflineMixin, event_handler

class NotesView(OfflineMixin, LiveView):
    template_name = "notes.html"
    offline_template = "notes_offline.html"
    sync_events = ["save_note", "delete_note"]
    persist_state = True
    persist_fields = ["notes", "current_note"]

    def mount(self, request, **kwargs):
        self.notes = self.load_notes_from_db()
        self.current_note = None
        self.is_saving = False

    @event_handler
    def save_note(self, title, content, **kwargs):
        note = {"title": title, "content": content}
        self.notes.append(note)
        self.save_to_db(note)
        self.is_saving = False

    @event_handler  
    def delete_note(self, note_id, **kwargs):
        self.notes = [n for n in self.notes if n["id"] != note_id]
        self.delete_from_db(note_id)
```

```html
<!-- notes.html -->
{% load djust_pwa %}
<!DOCTYPE html>
<html>
<head>
    <title>Notes App</title>
    {% djust_pwa_head name="Notes App" theme_color="#4a5568" %}
</head>
<body>
    <header>
        <h1>📝 Notes</h1>
        {% djust_offline_indicator show_when="always" %}
    </header>

    <!-- Queued actions indicator -->
    <div class="djust-queued-indicator">
        <span class="djust-queued-count">0</span> pending changes
    </div>

    <main>
        <!-- Note list -->
        <ul class="notes-list">
            {% for note in notes %}
            <li>
                <span>{{ note.title }}</span>
                <button dj-click="delete_note" data-note-id="{{ note.id }}"
                        dj-offline-disable>Delete</button>
            </li>
            {% endfor %}
        </ul>

        <!-- New note form -->
        <form dj-submit="save_note">
            <input name="title" placeholder="Title" required>
            <textarea name="content" placeholder="Content"></textarea>
            
            <button type="submit" dj-offline-disable dj-loading="is_saving">
                <span dj-loading-hide="is_saving">Save Note</span>
                <span dj-loading-show="is_saving">Saving...</span>
            </button>
        </form>

        <!-- Offline notice -->
        <div dj-offline-show class="offline-notice">
            <p>You're offline. Changes will sync when connected.</p>
        </div>
    </main>
</body>
</html>
```

```html
<!-- notes_offline.html -->
<div class="offline-page">
    <h1>📝 Notes (Offline)</h1>
    
    <div class="cached-notes">
        <h2>Your cached notes:</h2>
        <ul>
            {% for note in view.notes %}
            <li>{{ note.title }}</li>
            {% empty %}
            <li>No cached notes available</li>
            {% endfor %}
        </ul>
    </div>
    
    <p class="reconnect-notice">
        Waiting for connection... Your changes are saved locally.
    </p>
</div>
```

## Best Practices

1. **Identify Critical Actions**: Only add truly important events to `sync_events` — avoid queuing everything.

2. **Provide Feedback**: Use `dj-offline-show` to inform users when they're offline and what will happen.

3. **Handle Conflicts**: If synced events might conflict with server state, implement conflict resolution in `process_queued_events`.

4. **Limit Persisted State**: Use `persist_fields` to avoid storing unnecessary data in IndexedDB.

5. **Test Offline Thoroughly**: Use browser DevTools to simulate offline conditions and test queue/sync behavior.

6. **Cache Wisely**: Only cache static assets and templates that are essential for offline functionality.

7. **Version Your Service Worker**: Use the `--version` flag to ensure users get updates.

## Troubleshooting

### Events Not Syncing

1. Check that the event name is in `sync_events`
2. Verify WebSocket connection is established
3. Check browser console for IndexedDB errors

### State Not Persisting

1. Ensure `persist_state = True`
2. Check that fields are JSON-serializable
3. Verify IndexedDB is not blocked by browser settings

### Service Worker Not Updating

1. Check if SW is cached by the browser
2. Use `--version` with a new value
3. Call `window.djust.offline.updateServiceWorker()` manually

### PWA Not Installing

1. Ensure HTTPS is enabled (required for PWA)
2. Check that manifest.json is valid
3. Verify icons exist at specified paths
