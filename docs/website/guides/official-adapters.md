# Official Adapters

Integrating a third-party client library with a server-rendered LiveView means
the same dance every time: a `dj-hook`, careful teardown in `destroyed()`, and
making sure a server re-render updates the widget instead of destroying it.
Get it wrong and you hit the class of bug tracked in
[#1724](https://github.com/djust-org/djust/issues/1724) — a chart that renders
on first paint and goes blank on the next update.

Official adapters are djust's pre-written version of that glue. **You bring the
library; djust ships only the glue.** Nothing is bundled, downloaded, or
vendored — the adapter is a single file already inside the wheel, served only
when you ask for it.

The pilot adapter is `chart` (Chart.js). More will follow only where demand
justifies the ongoing maintenance.

## Enabling an adapter

```python
# settings.py
DJUST_CONFIG = {
    "extensions": ["chart"],
}
```

That's the whole setup. djust injects the adapter's script after its own
client script on any LiveView page. When the list is empty or absent, nothing
is emitted — an adapter you don't enable costs you nothing.

A name that isn't a real adapter is reported at startup by system check
`djust.C015` rather than silently doing nothing:

```
?: (djust.C015) DJUST_CONFIG['extensions'] lists unknown adapter 'charts'.
   It will be ignored -- no script is injected and the adapter's hook never mounts.
   HINT: Available adapters: chart.
```

## `chart` — Chart.js

### 1. Load Chart.js yourself

djust never ships or fetches it. Load it **before** djust's client script —
put it in a template block that renders outside your `dj-root`:

```html
{% block extra_head %}
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
{% endblock %}
```

### 2. Mark up the canvas

```html
<canvas dj-hook="Chart"
        dj-hook-value-type='"bar"'
        dj-hook-value-data='{{ chart_data_json }}'></canvas>
```

`dj-hook-value-*` are [ADR-025 typed values](hooks.md): each is parsed as JSON,
falling back to the raw string. They are read live, so whatever the server
renders on the next update is what the chart sees.

### 3. Drive it from the server

Update the values in your view and the chart updates in place — no JavaScript:

```python
import json
from djust import LiveView
from djust.decorators import event_handler


class SalesView(LiveView):
    template_name = "sales.html"

    def mount(self, request, **kwargs):
        self.period = "week"

    @event_handler()
    def set_period(self, value: str = "week", **kwargs):
        self.period = value

    def get_context_data(self, **kwargs):
        return {"chart_data_json": json.dumps(self.chart_data())}
```

### Do NOT add `dj-update="ignore"`

It looks like the right morph-safety knob. It is the one thing that will break
this adapter:

- The patcher returns early on `dj-update="ignore"` **before** it syncs
  attributes, so the server could never change `dj-hook-value-data` and the
  chart would show its first dataset forever.
- It isn't needed. A `<canvas>` has no server-owned children, and the morph
  already refuses to remove a canvas's `width`/`height` — the attributes whose
  removal resets the drawing context.

If one specific attribute really is client-owned, name it in `dj-ignore-attrs`.
That's per-attribute and doesn't block the rest of the sync.

### JS commands

The adapter pre-registers two [`JS.ext`](js-commands.md) commands, so a chart
can be driven from a server-composed chain:

```python
from djust import LiveView
from djust.js import JS


class DashboardView(LiveView):
    refresh = JS.ext.chart_update(to="#sales")
```

| Command | Arguments | Effect |
|---|---|---|
| `chart_update` | — | Re-renders the target chart(s) in place |
| `chart_set_data` | `data` | Replaces the dataset, then re-renders |

### Lifecycle

| Hook | Behavior |
|---|---|
| `mounted()` | Builds the chart from the typed values |
| `updated()` | Mutates the **existing** instance and calls `update()` — never destroy-and-rebuild, which is the #1724 class |
| `destroyed()` | Calls `chart.destroy()` exactly once |

If Chart.js isn't loaded, the adapter logs one clear `console.error` per
element and does nothing else — it never throws, so other hooks on the page
still mount.

## Writing your own instead

An adapter is only ever a convenience. Everything it does is built from the
public extension points, so you can write the same glue yourself:

- [Hooks](hooks.md) — `dj-hook`, typed values, targets
- [JS Commands](js-commands.md) — `JS.ext.*` custom commands

See [ADR-025](https://github.com/djust-org/djust/blob/main/docs/adr/025-js-extension-sockets.md)
for the design rationale, including why djust doesn't bundle a reactivity
framework.
