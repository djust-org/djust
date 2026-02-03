# JavaScript Hooks

djust provides `dj-hook` for running custom JavaScript when elements are mounted, updated, or removed from the DOM. Inspired by Phoenix LiveView's `phx-hook`.

## Core Concepts

- **`dj-hook="HookName"`** — Attach a hook to an element
- **Lifecycle callbacks** — `mounted`, `updated`, `destroyed`, etc.
- **`pushEvent()`** — Send events from JS to server
- **`handleEvent()`** — Listen for server-pushed events

## Basic Usage

### 1. Register Your Hooks

```javascript
// In your app's JS (loaded before djust client)
window.djust = window.djust || {};
window.djust.hooks = {
    Counter: {
        mounted() {
            // Called when element is added to DOM
            this.count = parseInt(this.el.dataset.count || '0');
            console.log('Counter mounted with count:', this.count);
        },
        updated() {
            // Called when element's attributes change via VDOM patch
            this.count = parseInt(this.el.dataset.count || '0');
            console.log('Counter updated to:', this.count);
        },
        destroyed() {
            // Called when element is removed from DOM
            console.log('Counter destroyed');
        }
    }
};
```

### 2. Attach Hook in Template

```html
<div dj-hook="Counter" data-count="{{ count }}">
    Count: {{ count }}
</div>
```

## Hook Lifecycle Callbacks

| Callback | When Called |
|----------|-------------|
| `mounted()` | Element inserted into DOM |
| `beforeUpdate()` | Before VDOM patch (element still has old state) |
| `updated()` | After VDOM patch (element has new state) |
| `destroyed()` | Element removed from DOM |
| `disconnected()` | WebSocket connection lost |
| `reconnected()` | WebSocket connection restored |

## Hook Instance Properties

Inside any callback, `this` provides:

| Property | Description |
|----------|-------------|
| `this.el` | The DOM element with `dj-hook` |
| `this.viewName` | Name of the parent LiveView |
| `this.pushEvent(name, payload)` | Send event to server |
| `this.handleEvent(name, callback)` | Listen for server events |

## Example: Chart Hook

```javascript
window.djust.hooks = {
    Chart: {
        mounted() {
            // Initialize chart library
            const data = JSON.parse(this.el.dataset.values);
            this.chart = new Chart(this.el.querySelector('canvas'), {
                type: 'line',
                data: {
                    labels: data.labels,
                    datasets: [{
                        data: data.values,
                        borderColor: '#007bff'
                    }]
                }
            });

            // Listen for server updates
            this.handleEvent('chart_data', (payload) => {
                this.chart.data.datasets[0].data = payload.values;
                this.chart.update();
            });
        },
        updated() {
            // Re-read data attribute on VDOM update
            const data = JSON.parse(this.el.dataset.values);
            this.chart.data.datasets[0].data = data.values;
            this.chart.update();
        },
        destroyed() {
            // Clean up
            this.chart.destroy();
        }
    }
};
```

```html
<div dj-hook="Chart" data-values="{{ chart_data|json }}">
    <canvas></canvas>
</div>
```

```python
class DashboardView(LiveView):
    @event_handler
    def update_chart(self, **kwargs):
        self.chart_data = self.fetch_new_data()
        # Also push event for hooks listening via handleEvent
        self.push_event("chart_data", {"values": self.chart_data["values"]})
```

## Communicating with the Server

### pushEvent() — JS to Server

Send events from your hook to the server:

```javascript
window.djust.hooks = {
    VideoPlayer: {
        mounted() {
            const video = this.el.querySelector('video');
            
            video.addEventListener('play', () => {
                this.pushEvent('video_play', { time: video.currentTime });
            });
            
            video.addEventListener('pause', () => {
                this.pushEvent('video_pause', { time: video.currentTime });
            });
            
            video.addEventListener('ended', () => {
                this.pushEvent('video_ended', {});
            });
        }
    }
};
```

```python
class VideoView(LiveView):
    @event_handler
    def video_play(self, time, **kwargs):
        self.playing = True
        self.current_time = time
        log_analytics("video_play", self.video_id, time)

    @event_handler
    def video_pause(self, time, **kwargs):
        self.playing = False
        self.current_time = time

    @event_handler
    def video_ended(self, **kwargs):
        self.playing = False
        self.show_recommendations = True
```

### handleEvent() — Server to JS

Listen for events pushed from the server:

```javascript
window.djust.hooks = {
    Notifications: {
        mounted() {
            this.handleEvent('show_toast', (payload) => {
                showToast(payload.message, payload.type);
            });
            
            this.handleEvent('play_sound', (payload) => {
                new Audio(payload.url).play();
            });
        }
    }
};
```

```python
class ChatView(LiveView):
    @event_handler
    def new_message(self, content, **kwargs):
        self.messages.append(content)
        # Push event to JS hooks
        self.push_event("show_toast", {
            "message": f"New message from {self.sender}",
            "type": "info"
        })
        self.push_event("play_sound", {"url": "/static/notification.mp3"})
```

## Alternative Registration

For Phoenix LiveView compatibility, you can also use `window.DjustHooks`:

```javascript
// Phoenix-style registration
window.DjustHooks = {
    MyHook: {
        mounted() { /* ... */ }
    }
};

// Both are merged (djust.hooks takes precedence)
```

## Example: Copy to Clipboard

```javascript
window.djust.hooks = {
    Clipboard: {
        mounted() {
            this.el.addEventListener('click', () => {
                const text = this.el.dataset.copyText;
                navigator.clipboard.writeText(text).then(() => {
                    this.pushEvent('copied', { text });
                    
                    // Visual feedback
                    const original = this.el.textContent;
                    this.el.textContent = 'Copied!';
                    setTimeout(() => {
                        this.el.textContent = original;
                    }, 2000);
                });
            });
        }
    }
};
```

```html
<button dj-hook="Clipboard" data-copy-text="{{ share_url }}">
    📋 Copy Link
</button>
```

## Example: Infinite Scroll

```javascript
window.djust.hooks = {
    InfiniteScroll: {
        mounted() {
            this.observer = new IntersectionObserver((entries) => {
                if (entries[0].isIntersecting && !this.loading) {
                    this.loading = true;
                    this.pushEvent('load_more', {});
                }
            });
            
            this.observer.observe(this.el);
            
            this.handleEvent('items_loaded', () => {
                this.loading = false;
            });
        },
        destroyed() {
            this.observer.disconnect();
        }
    }
};
```

```html
<div class="items">
    {% for item in items %}
        <div class="item">{{ item.name }}</div>
    {% endfor %}
</div>

<!-- Sentinel element at bottom -->
<div dj-hook="InfiniteScroll" class="load-trigger">
    Loading more...
</div>
```

```python
class ItemListView(LiveView):
    def mount(self, request, **kwargs):
        self.page = 1
        self.items = self.load_items()

    @event_handler
    def load_more(self, **kwargs):
        self.page += 1
        new_items = self.load_items()
        self.items.extend(new_items)
        self.push_event("items_loaded", {})
```

## Example: Rich Text Editor

```javascript
window.djust.hooks = {
    RichEditor: {
        mounted() {
            // Initialize editor library (e.g., Quill, TipTap)
            this.editor = new Quill(this.el.querySelector('.editor'), {
                theme: 'snow',
                modules: { toolbar: true }
            });
            
            // Sync content on change (debounced)
            let timeout;
            this.editor.on('text-change', () => {
                clearTimeout(timeout);
                timeout = setTimeout(() => {
                    this.pushEvent('content_changed', {
                        content: this.editor.root.innerHTML
                    });
                }, 500);
            });
            
            // Handle server updates
            this.handleEvent('set_content', (payload) => {
                this.editor.root.innerHTML = payload.content;
            });
        },
        destroyed() {
            // Quill doesn't have a destroy method, but other libs might
        }
    }
};
```

## Connection State

Handle WebSocket disconnection/reconnection:

```javascript
window.djust.hooks = {
    RealtimeIndicator: {
        mounted() {
            this.setConnected(true);
        },
        disconnected() {
            this.setConnected(false);
        },
        reconnected() {
            this.setConnected(true);
            // Optionally refresh data
            this.pushEvent('sync_state', {});
        },
        setConnected(connected) {
            this.el.classList.toggle('online', connected);
            this.el.classList.toggle('offline', !connected);
            this.el.textContent = connected ? '🟢 Online' : '🔴 Reconnecting...';
        }
    }
};
```

## Complete Example: Map with Markers

```javascript
window.djust.hooks = {
    Map: {
        mounted() {
            const { lat, lng, zoom } = this.el.dataset;
            
            // Initialize map
            this.map = L.map(this.el).setView([lat, lng], zoom);
            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png').addTo(this.map);
            
            // Store markers for updates
            this.markers = {};
            
            // Initial markers
            this.updateMarkers(JSON.parse(this.el.dataset.markers || '[]'));
            
            // Listen for marker updates
            this.handleEvent('markers_updated', (payload) => {
                this.updateMarkers(payload.markers);
            });
            
            // Send map events to server
            this.map.on('click', (e) => {
                this.pushEvent('map_clicked', {
                    lat: e.latlng.lat,
                    lng: e.latlng.lng
                });
            });
        },
        
        updateMarkers(markers) {
            // Remove old markers
            Object.values(this.markers).forEach(m => m.remove());
            this.markers = {};
            
            // Add new markers
            markers.forEach(m => {
                const marker = L.marker([m.lat, m.lng])
                    .bindPopup(m.title)
                    .addTo(this.map);
                this.markers[m.id] = marker;
            });
        },
        
        updated() {
            // Re-read markers from data attribute
            const markers = JSON.parse(this.el.dataset.markers || '[]');
            this.updateMarkers(markers);
        },
        
        destroyed() {
            this.map.remove();
        }
    }
};
```

```html
<div dj-hook="Map" 
     data-lat="{{ center_lat }}" 
     data-lng="{{ center_lng }}" 
     data-zoom="13"
     data-markers="{{ markers|json }}"
     style="height: 400px;">
</div>
```

## API Reference

### Hook Definition

```javascript
window.djust.hooks = {
    HookName: {
        mounted() { },      // Element added to DOM
        beforeUpdate() { }, // Before VDOM patch
        updated() { },      // After VDOM patch
        destroyed() { },    // Element removed
        disconnected() { }, // WebSocket lost
        reconnected() { },  // WebSocket restored
    }
};
```

### Instance Methods

| Method | Description |
|--------|-------------|
| `this.pushEvent(name, payload)` | Send event to server |
| `this.handleEvent(name, callback)` | Listen for server events |

### Instance Properties

| Property | Description |
|----------|-------------|
| `this.el` | The DOM element |
| `this.viewName` | Parent LiveView name |
