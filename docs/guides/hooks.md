# JavaScript Hooks

djust hooks let you integrate client-side JavaScript libraries (charts, maps, editors, etc.) with LiveView's server-rendered DOM.

## Quick Start

Register hooks in your JavaScript:

```javascript
// Register hooks before djust initializes
window.DjustHooks = {
    Chart: {
        mounted() {
            // this.el is the DOM element with dj-hook="Chart"
            this.chart = new Chart(this.el.querySelector('canvas'), {
                type: 'line',
                data: JSON.parse(this.el.dataset.values),
            });
        },
        updated() {
            // Called after server re-renders the element
            this.chart.data = JSON.parse(this.el.dataset.values);
            this.chart.update();
        },
        destroyed() {
            // Called when element is removed from DOM
            this.chart.destroy();
        }
    }
};
```

Use in your template:

```html
<div dj-hook="Chart" data-values="{{ chart_data|json }}">
    <canvas></canvas>
</div>
```

## Hook Lifecycle

| Callback | When It's Called |
|----------|-----------------|
| `mounted()` | Element added to DOM |
| `beforeUpdate()` | Before server re-renders element |
| `updated()` | After server re-renders element |
| `destroyed()` | Element removed from DOM |
| `disconnected()` | WebSocket connection lost |
| `reconnected()` | WebSocket connection restored |

## Hook Instance Properties

Inside hook callbacks, `this` provides:

| Property | Description |
|----------|-------------|
| `this.el` | The DOM element with `dj-hook` |
| `this.viewName` | The LiveView class name |

## pushEvent — Send Events to Server

Send events from client-side JS to your LiveView:

```javascript
window.DjustHooks = {
    MapPicker: {
        mounted() {
            this.map = new MapLibre(this.el, { center: [0, 0], zoom: 2 });
            
            this.map.on('click', (e) => {
                // Send coordinates to server
                this.pushEvent('location_selected', {
                    lat: e.lngLat.lat,
                    lng: e.lngLat.lng,
                });
            });
        }
    }
};
```

```python
# In your LiveView
@event_handler
def location_selected(self, lat, lng, **kwargs):
    self.selected_location = {"lat": lat, "lng": lng}
```

## handleEvent — Receive Server Events

Listen for events pushed from the server:

```javascript
window.DjustHooks = {
    Notifications: {
        mounted() {
            this.handleEvent('new_notification', (payload) => {
                this.showToast(payload.message, payload.type);
            });
            
            this.handleEvent('clear_all', () => {
                this.clearAllToasts();
            });
        },
        
        showToast(message, type) {
            // Show notification toast
        },
        
        clearAllToasts() {
            // Clear all notifications
        }
    }
};
```

```python
# In your LiveView
@event_handler
def send_notification(self):
    self.push_event('new_notification', {
        'message': 'Item saved!',
        'type': 'success'
    })
```

## Registration Methods

### Method 1: window.DjustHooks (Recommended)

Phoenix LiveView-compatible style:

```javascript
window.DjustHooks = {
    MyHook: { mounted() { ... } }
};
```

### Method 2: window.djust.hooks

Alternative namespaced style:

```javascript
window.djust = window.djust || {};
window.djust.hooks = {
    MyHook: { mounted() { ... } }
};
```

Both methods work. They're merged automatically.

## Examples

### Rich Text Editor

```javascript
window.DjustHooks = {
    RichEditor: {
        mounted() {
            this.editor = new Quill(this.el.querySelector('.editor'), {
                theme: 'snow',
                modules: {
                    toolbar: [
                        ['bold', 'italic', 'underline'],
                        [{ 'list': 'ordered'}, { 'list': 'bullet' }],
                        ['link', 'image'],
                    ]
                }
            });
            
            // Sync content changes to server (debounced)
            let timeout;
            this.editor.on('text-change', () => {
                clearTimeout(timeout);
                timeout = setTimeout(() => {
                    this.pushEvent('content_changed', {
                        content: this.editor.root.innerHTML
                    });
                }, 500);
            });
        },
        
        updated() {
            // Server sent new content — update editor
            const serverContent = this.el.dataset.content;
            if (this.editor.root.innerHTML !== serverContent) {
                this.editor.root.innerHTML = serverContent;
            }
        },
        
        destroyed() {
            // Cleanup
        }
    }
};
```

```html
<div dj-hook="RichEditor" data-content="{{ content }}">
    <div class="editor">{{ content|safe }}</div>
</div>
```

### Interactive Map

```javascript
window.DjustHooks = {
    Map: {
        mounted() {
            const center = JSON.parse(this.el.dataset.center);
            const markers = JSON.parse(this.el.dataset.markers);
            
            this.map = L.map(this.el).setView(center, 13);
            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png').addTo(this.map);
            
            this.markerLayer = L.layerGroup().addTo(this.map);
            this.renderMarkers(markers);
            
            this.map.on('click', (e) => {
                this.pushEvent('map_clicked', {
                    lat: e.latlng.lat,
                    lng: e.latlng.lng,
                });
            });
        },
        
        updated() {
            const markers = JSON.parse(this.el.dataset.markers);
            this.renderMarkers(markers);
        },
        
        renderMarkers(markers) {
            this.markerLayer.clearLayers();
            markers.forEach(m => {
                L.marker([m.lat, m.lng])
                    .bindPopup(m.name)
                    .addTo(this.markerLayer);
            });
        },
        
        destroyed() {
            this.map.remove();
        }
    }
};
```

```html
<div dj-hook="Map" 
     data-center="{{ center|json }}"
     data-markers="{{ markers|json }}"
     style="height: 400px;">
</div>
```

### Infinite Scroll

```javascript
window.DjustHooks = {
    InfiniteScroll: {
        mounted() {
            this.page = 1;
            this.loading = false;
            
            this.observer = new IntersectionObserver((entries) => {
                if (entries[0].isIntersecting && !this.loading) {
                    this.loadMore();
                }
            });
            
            this.sentinel = this.el.querySelector('.sentinel');
            if (this.sentinel) {
                this.observer.observe(this.sentinel);
            }
        },
        
        loadMore() {
            this.loading = true;
            this.page += 1;
            this.pushEvent('load_more', { page: this.page });
        },
        
        updated() {
            this.loading = false;
            // Re-observe sentinel after DOM update
            this.sentinel = this.el.querySelector('.sentinel');
            if (this.sentinel) {
                this.observer.observe(this.sentinel);
            }
        },
        
        destroyed() {
            this.observer.disconnect();
        }
    }
};
```

```html
<div dj-hook="InfiniteScroll">
    {% for item in items %}
        <div class="item">{{ item.name }}</div>
    {% endfor %}
    
    {% if has_more %}
        <div class="sentinel"></div>
    {% endif %}
</div>
```

### Video Player

```javascript
window.DjustHooks = {
    VideoPlayer: {
        mounted() {
            this.video = this.el.querySelector('video');
            
            // Report playback progress
            this.video.addEventListener('timeupdate', () => {
                if (Math.floor(this.video.currentTime) % 10 === 0) {
                    this.pushEvent('playback_progress', {
                        currentTime: this.video.currentTime,
                        duration: this.video.duration,
                    });
                }
            });
            
            // Listen for server commands
            this.handleEvent('seek', ({ time }) => {
                this.video.currentTime = time;
            });
            
            this.handleEvent('play', () => {
                this.video.play();
            });
            
            this.handleEvent('pause', () => {
                this.video.pause();
            });
        },
        
        disconnected() {
            this.video.pause();
        },
        
        reconnected() {
            // Optionally resume
        }
    }
};
```

### Clipboard Copy

```javascript
window.DjustHooks = {
    CopyButton: {
        mounted() {
            this.el.addEventListener('click', () => {
                const text = this.el.dataset.copyText;
                navigator.clipboard.writeText(text).then(() => {
                    this.pushEvent('copied', { text: text });
                    
                    // Visual feedback
                    this.el.textContent = '✓ Copied!';
                    setTimeout(() => {
                        this.el.textContent = 'Copy';
                    }, 2000);
                });
            });
        }
    }
};
```

```html
<button dj-hook="CopyButton" data-copy-text="{{ share_url }}">
    Copy Link
</button>
```

## Best Practices

1. **Initialize in mounted()**: Create third-party library instances in `mounted()`, not at script load time.

2. **Cleanup in destroyed()**: Remove event listeners, destroy library instances, and clear timers.

3. **Handle reconnection**: Use `disconnected()` to pause live data, `reconnected()` to resume.

4. **Pass data via data-* attributes**: Use JSON for complex data:
   ```html
   <div dj-hook="Chart" data-config='{{ config|json }}'>
   ```

5. **Debounce frequent events**: For events like mouse move or typing, debounce before `pushEvent()`.

6. **Check for element existence in updated()**: After DOM patches, your element reference may be stale.
