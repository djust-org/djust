# Client-Side JavaScript Hooks

djust hooks let you run custom JavaScript when elements are mounted, updated, or removed from the DOM. This is essential for integrating third-party libraries (charts, maps, editors) or any behavior that requires direct DOM access.

## Overview

- **Lifecycle callbacks** - `mounted()`, `updated()`, `destroyed()`, `disconnected()`, `reconnected()`, `beforeUpdate()`
- **Server communication** - `pushEvent()` to send events, `handleEvent()` to receive server push events
- **DOM element access** - `this.el` gives direct access to the hooked element
- **Automatic management** - Hooks are mounted/destroyed automatically as the DOM changes via LiveView patches

## Quick Start

### 1. Register hooks in JavaScript

```html
<script>
window.djust.hooks = {
    MyChart: {
        mounted() {
            // this.el is the DOM element
            this.chart = new Chart(this.el, {
                type: 'line',
                data: JSON.parse(this.el.dataset.values),
            });
        },
        updated() {
            // Re-render chart when server updates data attributes
            this.chart.data = JSON.parse(this.el.dataset.values);
            this.chart.update();
        },
        destroyed() {
            // Clean up to prevent memory leaks
            this.chart.destroy();
        }
    }
};
</script>
```

### 2. Use dj-hook in your template

```html
<canvas dj-hook="MyChart" data-values='{{ chart_data_json }}'></canvas>
```

### 3. Communicate with the server

```html
<script>
window.djust.hooks = {
    MapPicker: {
        mounted() {
            this.map = new MapLibrary(this.el);
            this.map.on('click', (e) => {
                // Send event to server
                this.pushEvent('location_selected', {
                    lat: e.lat, lng: e.lng
                });
            });

            // Listen for server push events
            this.handleEvent('center_map', (payload) => {
                this.map.setCenter(payload.lat, payload.lng);
            });
        },
        destroyed() {
            this.map.remove();
        }
    }
};
</script>
```

## API Reference

### Registering Hooks

Register hooks by assigning to `window.djust.hooks`:

```javascript
window.djust.hooks = {
    HookName: {
        mounted() {},
        updated() {},
        destroyed() {},
        disconnected() {},
        reconnected() {},
        beforeUpdate() {},
    }
};
```

Alternatively, for Phoenix LiveView compatibility:

```javascript
window.DjustHooks = {
    HookName: { mounted() {}, ... }
};
```

Both registries are merged, with `window.djust.hooks` taking precedence.

### Hook Instance Properties

| Property | Type | Description |
|----------|------|-------------|
| `this.el` | `HTMLElement` | The DOM element with `dj-hook`. Updated automatically if the element is replaced during a patch. |
| `this.viewName` | `string` | The LiveView name from the closest `[dj-view]` ancestor. |

### Hook Instance Methods

#### `this.pushEvent(event, payload)`

Send a custom event to the server-side LiveView. The server handles it via `@event_handler`.

| Parameter | Type | Description |
|-----------|------|-------------|
| `event` | `string` | Event name (matches the server handler method name) |
| `payload` | `object` | Data to send (serialized as JSON) |

```javascript
mounted() {
    this.el.addEventListener('click', () => {
        this.pushEvent('item_clicked', { id: this.el.dataset.itemId });
    });
}
```

#### `this.handleEvent(eventName, callback)`

Register a callback for server-sent push events (via `self.push_event()` on the server).

| Parameter | Type | Description |
|-----------|------|-------------|
| `eventName` | `string` | Event name to listen for |
| `callback` | `function` | Called with the event payload |

```javascript
mounted() {
    this.handleEvent('highlight', (payload) => {
        this.el.style.backgroundColor = payload.color;
    });
}
```

### Lifecycle Callbacks

#### `mounted()`

Called once when the element first appears in the DOM. Use for initialization: creating library instances, binding event listeners, fetching data.

#### `updated()`

Called after a server re-render patches the DOM and the hooked element is still present. Use to sync third-party libraries with new data attributes or content.

#### `beforeUpdate()`

Called just before a DOM patch is applied. Use to save state that would be lost during the patch (e.g., scroll position, selection ranges).

#### `destroyed()`

Called when the hooked element is removed from the DOM. Use for cleanup: destroying library instances, removing global event listeners, clearing timers.

#### `disconnected()`

Called when the WebSocket connection drops. Use to show offline indicators or pause real-time features.

#### `reconnected()`

Called when the WebSocket connection is restored after a disconnect. Use to refresh state or restart real-time features.

### Template Directive

#### `dj-hook="HookName"`

Attach a hook to a DOM element. The `HookName` must match a key in the hook registry.

```html
<div dj-hook="MyWidget" data-config='{"option": true}'>
    Widget content
</div>
```

## Examples

### Chart.js Integration

```python
import json
from djust import LiveView
from djust.decorators import event_handler

class DashboardView(LiveView):
    template_name = 'dashboard.html'

    def mount(self, request, **kwargs):
        self.sales_data = self.fetch_sales()

    @event_handler()
    def refresh_data(self, **kwargs):
        self.sales_data = self.fetch_sales()

    def get_context_data(self, **kwargs):
        return {
            'chart_json': json.dumps(self.sales_data),
        }
```

```html
<canvas dj-hook="SalesChart" data-chart='{{ chart_json }}'></canvas>
<button dj-click="refresh_data">Refresh</button>

<script>
window.djust.hooks = {
    SalesChart: {
        mounted() {
            const data = JSON.parse(this.el.dataset.chart);
            this.chart = new Chart(this.el, {
                type: 'bar',
                data: {
                    labels: data.labels,
                    datasets: [{
                        label: 'Sales',
                        data: data.values,
                    }]
                }
            });
        },
        updated() {
            const data = JSON.parse(this.el.dataset.chart);
            this.chart.data.labels = data.labels;
            this.chart.data.datasets[0].data = data.values;
            this.chart.update();
        },
        destroyed() {
            this.chart.destroy();
        }
    }
};
</script>
```

### Rich Text Editor

```html
<div dj-hook="RichEditor" data-field="body"></div>

<script>
window.djust.hooks = {
    RichEditor: {
        mounted() {
            this.editor = new Quill(this.el, { theme: 'snow' });
            this.editor.on('text-change', () => {
                this.pushEvent('editor_change', {
                    field: this.el.dataset.field,
                    content: this.editor.root.innerHTML,
                });
            });

            this.handleEvent('set_content', (payload) => {
                this.editor.root.innerHTML = payload.html;
            });
        },
        destroyed() {
            // Quill cleans up automatically when element is removed
        },
        disconnected() {
            this.el.classList.add('editor-offline');
        },
        reconnected() {
            this.el.classList.remove('editor-offline');
        }
    }
};
</script>
```

### Infinite Scroll Observer

```html
<div id="items">
    {% for item in items %}
    <div class="item">{{ item.name }}</div>
    {% endfor %}
</div>
<div dj-hook="InfiniteScroll" data-page="{{ page }}"></div>

<script>
window.djust.hooks = {
    InfiniteScroll: {
        mounted() {
            this.observer = new IntersectionObserver((entries) => {
                if (entries[0].isIntersecting) {
                    const page = parseInt(this.el.dataset.page) + 1;
                    this.pushEvent('load_more', { page });
                }
            });
            this.observer.observe(this.el);
        },
        updated() {
            // Re-observe after DOM update (element may have been replaced)
            this.observer.observe(this.el);
        },
        destroyed() {
            this.observer.disconnect();
        }
    }
};
</script>
```

## Best Practices

### Cleanup in destroyed()

Always clean up in `destroyed()` to prevent memory leaks:
- Destroy third-party library instances (charts, editors, maps)
- Disconnect observers (`IntersectionObserver`, `MutationObserver`, `ResizeObserver`)
- Remove global event listeners (`window.addEventListener`)
- Clear intervals and timeouts

```javascript
destroyed() {
    if (this.chart) this.chart.destroy();
    if (this.observer) this.observer.disconnect();
    if (this.interval) clearInterval(this.interval);
    window.removeEventListener('resize', this._resizeHandler);
}
```

### Avoiding Memory Leaks

- Store references to event handlers so you can remove them later.
- Use `destroyed()` for every resource created in `mounted()`.
- Be aware that `updated()` is called on every re-render -- do not create new instances in `updated()`, only refresh existing ones.

### Data Passing

- Pass data from the server via `data-*` attributes on the hooked element.
- For complex data, use JSON in a data attribute and parse it in the hook.
- Use `this.pushEvent()` to send data back to the server, not direct WebSocket calls.
- Use `this.handleEvent()` to receive targeted push events from the server.

### Hook Registration Timing

- Register hooks before the LiveView mounts. Place the `<script>` tag with hook definitions in `<head>` or before the djust client script.
- If hooks are registered after mount, they will be picked up on the next DOM patch.
