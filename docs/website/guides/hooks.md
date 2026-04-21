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
| `this.viewName` | `string` | LiveView name from closest `[dj-view]` ancestor. |

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

## Colocated Hooks (v0.5.0, Phoenix 1.1 parity)

Write hook JavaScript inline with the template that uses it, instead of in a separate file. The `{% colocated_hook %}` block tag emits a `<script type="djust/hook" data-hook="NAME">` tag; the djust client walks these on init and after each VDOM morph and registers each body as `window.djust.hooks[NAME]`.

```django
{% load live_tags %}

{% colocated_hook "Chart" %}
    hook.mounted = function() {
        const data = JSON.parse(this.el.dataset.values);
        this.chart = new Chart(this.el, { type: 'line', data });
    };
    hook.updated = function() {
        this.chart.data = JSON.parse(this.el.dataset.values);
        this.chart.update();
    };
    hook.destroyed = function() {
        this.chart.destroy();
    };
{% endcolocated_hook %}

<canvas dj-hook="Chart" data-values='{{ chart_data_json }}'></canvas>
```

**Convention**: your body assigns callbacks to a local `hook` object. The client wraps the body in an IIFE factory (`(function() { const hook = {}; <body>; return hook; })()`) and stores the result under `data-hook`.

### Namespacing

Two different views that each define `Chart` will collide if both register the hook globally. Opt into namespacing to prefix each hook with the view's module + class name:

```python
# settings.py
DJUST_CONFIG = {"hook_namespacing": "strict"}
```

With strict namespacing on, `{% colocated_hook "Chart" %}` inside a template rendered by `myapp.views.DashboardView` emits `data-hook="myapp.views.DashboardView.Chart"`, and the `<canvas dj-hook="myapp.views.DashboardView.Chart">` selector must match. Set `dj-hook` to the same namespaced name — typically this is done via a Django context variable or a helper that knows the current view.

Per-tag opt-out when a hook truly is global (e.g. a page-wide tooltip handler):

```django
{% colocated_hook "Tooltip" global %}
    hook.mounted = function() { /* ... */ };
{% endcolocated_hook %}
```

The `global` keyword emits the bare `data-hook="Tooltip"` regardless of the `hook_namespacing` setting.

### Security

- The hook body is **template-author JS**, not user input. Never interpolate request data into the body.
- The `{% colocated_hook %}` tag escapes `</script>` / `</SCRIPT>` in the body to prevent premature tag close.
- The client evaluates bodies via `new Function(...)`. Apps on strict CSP without `'unsafe-eval'` should skip this feature and register hooks via a nonce-bearing `<script>window.djust.hooks.X = {...}</script>`.
- Every emitted script carries a `/* COLOCATED HOOK: <name> */` banner so security auditors can grep for them.

## Ignored Attributes (`dj-ignore-attrs`) — v0.5.0

Mark specific HTML attributes as client-owned so VDOM `SetAttr` patches skip them. Essential for browser-native elements (`<dialog>`, `<details>`) and third-party JS libraries that manage attributes the server doesn't know about. Phoenix 1.1 calls this `JS.ignore_attributes/1`.

```html
<!-- Browser manages <dialog open> via .showModal() / .close() -->
<dialog id="confirm" dj-ignore-attrs="open">...</dialog>

<!-- Third-party library manages data-state + aria-expanded -->
<div dj-ignore-attrs="data-state, aria-expanded" ...></div>
```

**Format**: comma-separated list of attribute names. Whitespace around commas is tolerated.

**Scope**: only `SetAttr` patches are skipped — `RemoveAttr` is not affected. Users explicitly opt in per element; there is no framework-level default list. Rationale: the browser-native case is common, but silent magic is a bigger cost than the typing.
