---
title: "Tutorial: Integrate Chart.js with a hook"
slug: tutorial-chart-hook
section: guides
order: 71
level: intermediate
description: "Drop a third-party JavaScript library (Chart.js, Mapbox, CodeMirror, anything) into a LiveView with a hook. Five lifecycle callbacks (mounted/updated/destroyed/disconnected/reconnected) cover every integration shape. Plus the right way to ship live data updates without re-mounting the chart on every patch."
---

# Tutorial: Integrate Chart.js with a hook

Eventually every real app needs a chart, a map, a code editor,
a date picker, or something else built by people who weren't
thinking about LiveView. djust's answer is **hooks** — small
JavaScript objects that mount when an element appears in the DOM
and clean up when it leaves. The framework handles
mount/update/unmount lifecycle; you write the integration code.

By the end of this tutorial you'll have:

- A live revenue dashboard with a **Chart.js line chart** that
  re-draws when the underlying data changes — without re-mounting
  the chart, so animations stay smooth.
- A **time-range selector** (1h / 24h / 7d / 30d) that triggers
  a server fetch and patches new data into the chart.
- A **destroy** callback so navigating away frees the chart's
  canvas context and prevents memory leaks.
- The same pattern applied to a **Mapbox map** and a **CodeMirror
  editor** in the "Where to go next" section, so the recipe sticks.

| You'll learn | Documented in |
|---|---|
| `dj-hook` + `mounted()`/`updated()`/`destroyed()` | [Client-Side JavaScript Hooks](hooks.md) |
| Passing data to hooks via `dj-hook-value-*` attributes | [Hooks](hooks.md#typed-values--targets) |
| `dj-update="ignore"` for library-owned DOM | [Hooks](hooks.md#why-dj-updateignore) |
| `pushEvent()` from JS → server handler | [Hooks](hooks.md) |
| When to use a hook vs a `@server_function` vs JS Commands | This tutorial |

> **Prerequisites:** [Your First LiveView](../getting-started/first-liveview.md), the [optimistic
> updates tutorial](tutorial-optimistic-updates.md) (recommended).
> A basic understanding of Chart.js helps but isn't required —
> swap in your charting library of choice.

---

## What you're building

```
┌────────────────────────────────────────────────────────┐
│ Revenue                          [1h][24h][7d][30d]    │
│                                          ^ selected   │
│                                                        │
│ $/day                                                  │
│  ┃                                              ╱╲    │
│  ┃                                          ╱─╮╱  ╲   │
│  ┃                                      ╱──╯  ╯    ╲  │
│  ┃                                  ╱──╯             ╲│
│  ┃                          ╱─────╯                   │
│  ┃                  ╱──────╯                          │
│  ┃              ╱──╯                                  │
│  ┃          ╱──╯                                      │
│  ┃    ╱────╯                                          │
│  ┃                                                    │
│  ┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ │
│   Apr 20    Apr 22   Apr 24   Apr 26   Apr 28         │
└────────────────────────────────────────────────────────┘
```

Click a different range button → the same chart instance
animates to the new data. Chart.js animation runs locally; the
server only fired the `set_range` event and pushed back a new
data payload.

---

## Step 1 — Define the hook

```html
<!-- templates/base.html: loaded once, OUTSIDE the per-page dj-root -->
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<script>
  window.djust.hooks = window.djust.hooks || {};
  window.djust.hooks.RevenueChart = {
    mounted() {
      // The hook sits on a wrapper; the canvas is its ignored child.
      const points = this.values.points;
      this.chart = new Chart(this.target("canvas"), {
        type: "line",
        data: {
          labels: points.map(p => p.date),
          datasets: [{
            label: "Revenue",
            data: points.map(p => p.value),
            borderColor: "#3b82f6",
            tension: 0.3,
          }],
        },
        options: {
          responsive: true,
          animation: { duration: 400 },
          scales: { y: { beginAtZero: true } },
        },
      });
    },

    updated() {
      // Called after a server re-render patches this element. We
      // update the chart in place instead of destroying and
      // recreating, so the animation tweens the new values.
      // this.values is live: it reads the freshly patched attribute.
      const points = this.values.points;
      this.chart.data.labels = points.map(p => p.date);
      this.chart.data.datasets[0].data = points.map(p => p.value);
      this.chart.update();
    },

    destroyed() {
      // Free the canvas context. Without this, navigating away
      // from the page leaves the chart in memory until GC catches
      // up, and navigating back hits Chart.js's "Canvas is already
      // in use" error.
      this.chart.destroy();
    },
  };
</script>
```

Register hooks in the base template, not in the page's own template:
a `<script>` inside the reactive root does not run when the page
arrives by `dj-navigate`, so the chart would stay blank after SPA
navigation (see [Hooks](hooks.md#integrating-third-party-libraries-chartjs-maps-editors)).

Three lifecycle callbacks cover the entire integration:

| Callback | When | What to do |
|---|---|---|
| `mounted()` | Element first enters the DOM | Initialize the third-party library against `this.el` (or a `this.target(...)` child) |
| `updated()` | Element re-rendered with new content | Update the library in place — DON'T tear down + remount; you'll lose state and animations |
| `destroyed()` | Element leaves the DOM | Free resources (canvas/WebGL contexts, event listeners, timers) |

`disconnected()` and `reconnected()` exist too, for handling
WebSocket reconnects — useful for libraries that need to
re-establish their own connections (live-streaming charts, peer-
to-peer maps). Most static integrations don't need them.

---

## Step 2 — The view

```python
# myapp/views.py
import json
from datetime import timedelta
from django.utils import timezone

from djust import LiveView, state, event_handler

from .models import Revenue


RANGE_PRESETS = {
    "1h":  timedelta(hours=1),
    "24h": timedelta(days=1),
    "7d":  timedelta(days=7),
    "30d": timedelta(days=30),
}


class RevenueDashboardView(LiveView):
    template_name = "dashboard.html"

    points_json = state("[]")
    selected_range = state("7d")
    # The picker loops this instead of splitting a string in the template:
    # there is no `split` filter, and the presets already live here.
    ranges = state(default_factory=lambda: list(RANGE_PRESETS))

    def mount(self, request, **kwargs):
        self._refresh()

    def _refresh(self):
        delta = RANGE_PRESETS[self.selected_range]
        cutoff = timezone.now() - delta
        rows = Revenue.objects.filter(date__gte=cutoff).order_by("date")
        self.points_json = json.dumps([
            {"date": r.date.isoformat(), "value": float(r.amount)}
            for r in rows
        ])

    @event_handler
    def set_range(self, range: str = "", **kwargs):
        if range not in RANGE_PRESETS:
            return
        self.selected_range = range
        self._refresh()
```

When `set_range` fires, two state vars change (`selected_range`
and `points_json`). The framework patches both into the DOM — the
range buttons update their `is-active` class, and the chart
wrapper's `dj-hook-value-points` attribute changes. The hook's
`updated()` runs and reads the new value from `this.values.points`.

---

## Step 3 — The template

```html
<!-- templates/dashboard.html -->
<section class="dashboard" dj-root>
  <header class="dashboard-head">
    <h1>Revenue</h1>
    <div class="range-picker" role="group" aria-label="Time range">
      {% for r in ranges %}
        <button
          type="button"
          dj-click="set_range"
          data-range="{{ r }}"
          class="range-btn {% if selected_range == r %}is-active{% endif %}"
          aria-pressed="{% if selected_range == r %}true{% else %}false{% endif %}"
        >{{ r }}</button>
      {% endfor %}
    </div>
  </header>

  <div dj-hook="RevenueChart" dj-hook-value-points="{{ points_json }}">
    <canvas
      id="revenue-chart"
      dj-update="ignore"
      dj-hook-target="canvas"
      role="img"
      aria-label="Revenue chart"
    ></canvas>
  </div>
</section>
```

Three patterns:

| Pattern | Effect |
|---|---|
| `dj-hook="RevenueChart"` | Mount the `RevenueChart` hook on the wrapper. |
| `dj-hook-value-points="{{ points_json }}"` | Hand the hook its data. Auto-escaping keeps the JSON safe inside the attribute, and the client JSON-parses it into `this.values.points`. |
| `dj-update="ignore"` + `id` on the `<canvas>` | Chart.js resizes and draws into the canvas; this tells the patcher to leave it alone so the next re-render doesn't fight the library. The `id` is required, and the value attribute stays on the wrapper because an ignored element's attributes are frozen too. `this.target("canvas")` finds it via `dj-hook-target`. |

---

## Step 4 — Why update-in-place beats remount

A naive integration would tear down + recreate the chart on every
`updated()`. Don't:

```javascript
// BAD — flickers, loses animation, leaks contexts under load
updated() {
  this.chart.destroy();
  this.mounted();  // call mount again to rebuild
}
```

Chart.js (and most chart libraries) animate **between** the old
and new datasets. If you destroy the chart, the animation is lost
— the new data appears abruptly. With `update()`-in-place, the
line tweens from old shape to new shape over 400 ms.

The same applies to **maps** (smooth pan/zoom > snap to new
center), **editors** (cursor position survives > caret jumps to
top), **video players** (state preserves > playback restarts), and
generally any library where "feels alive" matters.

The contract: `mounted()` allocates, `updated()` mutates,
`destroyed()` frees.

---

## Step 5 — When to reach for a hook vs other primitives

| You want… | Use |
|---|---|
| Click → server state change → re-render | `@event_handler` |
| Just-fetch-data, no re-render | `@server_function` |
| Pure client DOM ops, no server, no library | JS Commands |
| Initialize a third-party library on an element | **Hook** |
| Library + server data updates | **Hook** with `updated()` |
| Library + library calls server (e.g. map click → save pin) | **Hook** with `pushEvent()` |

The rule of thumb: if you'd `npm install` a library to do this
in a React project, it's a hook in djust. If you wouldn't (it's a
state change, a server fetch, or pure DOM toggling), it's one of
the other three primitives.

---

## What just happened, end to end

```
   Browser                                    Server
      │                                           │
      │ initial render: <div dj-hook="RevenueChart"
      │                   dj-hook-value-points="[…]">
      │ ─── hook.mounted() runs ───────►          │
      │     new Chart(canvas, …)                  │
      │     (chart visible)                       │
      │                                           │
      │ click "30d" button                        │
      │ ───────────────────────────────────────► set_range("30d")
      │                                           │ self.selected_range = "30d"
      │                                           │ self._refresh()
      │                                           │   → self.points = [...]
      │ ◄─── patch: button class flip,            │
      │       value attr changes ─────────────────│
      │                                           │
      │ DOM mutated → hook.updated() fires        │
      │   chart.data.labels = ...                 │
      │   chart.data.datasets[0].data = ...       │
      │   chart.update()                          │
      │   (chart animates to new data over 400ms) │
      │                                           │
      │ user navigates away                       │
      │   hook element removed from DOM           │
      │   hook.destroyed() fires                  │
      │     chart.destroy() → frees the canvas    │
```

`mounted()` runs once. `updated()` runs every time the server
patches the element. `destroyed()` runs once on removal. That's
the whole lifecycle.

---

## Where to go next

The same recipe applies to other libraries — `mounted` allocates,
`updated` mutates, `destroyed` frees:

- **Mapbox / Leaflet maps:**
  ```javascript
  mounted()    { this.map = new Map(this.el, …); }
  updated()    { this.map.setCenter(this.values.center); }
  destroyed()  { this.map.remove(); }
  ```
- **CodeMirror / Monaco editors:**
  ```javascript
  mounted()    { this.editor = new EditorView({ parent: this.el, … }); }
  updated()    { this.editor.dispatch({ changes: { … } }); }
  destroyed()  { this.editor.destroy(); }
  ```
- **Date pickers (Flatpickr, etc.):**
  ```javascript
  mounted()    { this.picker = flatpickr(this.el, …); }
  destroyed()  { this.picker.destroy(); }
  ```

Two lifecycle callbacks worth knowing about:

- `disconnected()` / `reconnected()` for WebSocket reconnects —
  useful when the library has its own connection (live charts
  pulling SSE, video streams). Pause + resume rather than
  destroy + remount.
- `beforeUpdate()` if you need to capture state from the DOM
  *before* the server's patch lands — e.g. preserve the user's
  current scroll position in a chat hook so re-renders don't
  yank them away.

The five-callback shape is small enough to memorize. Any
JavaScript library that lets you `new Library(element, options)`
fits this pattern. Once it clicks, "do I bring in this library?"
stops being a framework question and becomes a normal
size-vs-need tradeoff.
