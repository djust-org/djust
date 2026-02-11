---
title: "Client-Side JavaScript Hooks"
slug: hooks
section: guides
order: 5
level: intermediate
description: "Lifecycle callbacks for integrating charts, maps, editors, and custom DOM behavior"
---

# Client-Side JavaScript Hooks

djust hooks let you run custom JavaScript when elements are mounted, updated, or removed from the DOM. This is essential for integrating third-party libraries (charts, maps, editors) or any behavior that requires direct DOM access.

## What You Get

- **Lifecycle callbacks** -- `mounted()`, `updated()`, `destroyed()`, `disconnected()`, `reconnected()`, `beforeUpdate()`
- **Server communication** -- `pushEvent()` to send events, `handleEvent()` to receive server pushes
- **DOM access** -- `this.el` gives direct access to the hooked element
- **Automatic management** -- Hooks mount and destroy as the DOM changes via LiveView patches

## Quick Start

### 1. Register Hooks in JavaScript

```html
<script>
window.djust.hooks = {
    MyChart: {
        mounted() {
            this.chart = new Chart(this.el, {
                type: 'line',
                data: JSON.parse(this.el.dataset.values),
            });
        },
        updated() {
            this.chart.data = JSON.parse(this.el.dataset.values);
            this.chart.update();
        },
        destroyed() {
            this.chart.destroy();
        }
    }
};
</script>
```

### 2. Use `dj-hook` in Your Template

```html
<canvas dj-hook="MyChart" data-values='{{ chart_data_json }}'></canvas>
```

### 3. Communicate with the Server

```javascript
window.djust.hooks = {
    MapPicker: {
        mounted() {
            this.map = new MapLibrary(this.el);
            this.map.on('click', (e) => {
                this.pushEvent('location_selected', {
                    lat: e.lat, lng: e.lng
                });
            });

            this.handleEvent('center_map', (payload) => {
                this.map.setCenter(payload.lat, payload.lng);
            });
        },
        destroyed() {
            this.map.remove();
        }
    }
};
```

## Lifecycle Callbacks

| Callback | When Called |
|----------|------------|
| `mounted()` | Element first appears in the DOM. Initialize libraries, bind listeners. |
| `updated()` | After a server re-render patches the DOM. Sync libraries with new data. |
| `beforeUpdate()` | Just before a DOM patch. Save state that would be lost (scroll position, selection). |
| `destroyed()` | Element removed from the DOM. Clean up libraries, observers, timers. |
| `disconnected()` | WebSocket connection drops. Show offline indicators. |
| `reconnected()` | WebSocket restored after disconnect. Refresh state. |

## Hook Instance API

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `this.el` | `HTMLElement` | The DOM element with `dj-hook`. Updated automatically on patches. |
| `this.viewName` | `string` | LiveView name from closest `[data-djust-view]` ancestor. |

### Methods

**`this.pushEvent(event, payload)`** -- Send an event to the server LiveView.

```javascript
this.pushEvent('item_clicked', { id: this.el.dataset.itemId });
```

**`this.handleEvent(eventName, callback)`** -- Listen for server push events.

```javascript
this.handleEvent('highlight', (payload) => {
    this.el.style.backgroundColor = payload.color;
});
```

## Example: Chart.js Integration

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
        return {'chart_json': json.dumps(self.sales_data)}
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
                    datasets: [{ label: 'Sales', data: data.values }]
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

## Example: Infinite Scroll

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

- **Always clean up in `destroyed()`**: Destroy library instances, disconnect observers, remove global listeners, clear timers.
- **Do not create new instances in `updated()`**: Only refresh existing ones. Creating new instances on every re-render causes memory leaks.
- **Pass data via `data-*` attributes**: Use JSON in data attributes for complex data. Parse in the hook.
- **Use `pushEvent` / `handleEvent`** for server communication, not direct WebSocket calls.
- **Register hooks before mount**: Place hook definitions in `<head>` or before the djust client script. Hooks registered late are picked up on the next DOM patch.
