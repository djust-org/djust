# Migration Guide

This guide helps you upgrade between major versions of djust.

## Upgrading to 0.9.0 — Additive Changes (No Breaking Changes)

The 0.9.0 release is **fully backwards-compatible**. All listed
behaviors are additive — your existing v0.8.x apps will continue
working without changes. The notes below describe optional
opt-ins and the new defaults that downstream consumers can take
advantage of.

### 1. Hot View Replacement (HVR) auto-enabled in DEBUG

**What changed:** djust's own `DjustConfig.ready()` now auto-calls
`enable_hot_reload()` whenever `DEBUG=True` and `watchdog` is
installed. This means HVR — the state-preserving `__class__` swap
on `.py` save shipped in v0.6.1 — works **out of the box** with no
integration step.

**Action required:** none. Existing `enable_hot_reload()` calls in
your own `AppConfig.ready()` keep working unchanged (the function
is idempotent).

**Recommended cleanup:** if you previously added
`from djust import enable_hot_reload; enable_hot_reload()` to an
`AppConfig.ready()`, you can delete those lines — they are now
redundant. Existing calls still work; this is purely cosmetic.

**Opt out:** if you orchestrate the file watcher externally (e.g.
`watchfiles` wrapping `uvicorn`):

```python
# settings.py
LIVEVIEW_CONFIG = {"hot_reload_auto_enable": False}
```

If you previously wrapped `uvicorn` in `watchfiles` to get
auto-reload, **drop that wrapper** and switch to plain `uvicorn`
— djust's HVR is strictly better (preserves form input, scroll
position, counters across edits).

### 2. Async render path (`streaming_render`)

**What's new:** views can opt in to a fully-async render path that
streams the HTML response chunk-by-chunk:

```python
class MyView(LiveView):
    streaming_render = True  # opt-in; default is False (sync render)

    async def aget(self, request, *args, **kwargs):
        # Optional async GET handler. Default is sync get().
        ...
```

**Action required:** none. Sync rendering remains the default; the
async path is purely opt-in.

**When to enable:** views that fan out to multiple slow data
sources (DB queries, LLM calls, external APIs) benefit from
`streaming_render = True` paired with `lazy=True` slots — the
shell streams immediately, slow regions fill in as their data
arrives.

### 3. Per-component time-travel + forward-replay (debug panel)

**What's new:** the debug panel's Time Travel tab now supports:

- **Per-component scrubbing** — scrub a single component's history
  without affecting the parent view or other components.
- **Forward-replay** — re-run a recorded event from its
  `state_before` baseline, optionally with override params,
  producing a branched timeline.
- **Branch indicator** — visualizes which timeline branch the
  current cursor is on (`main` or `branch-N`).

**Action required:** none. The debug panel only runs in `DEBUG=True`.

**Wire protocol additions:** the `time_travel_state` and
`time_travel_event` frames now include three additive fields
(`branch_id`, `forward_replay_enabled`, `max_events`). Old clients
ignore unknown fields; new clients fall back to v0.6.1 behavior
when the server doesn't supply them. No breakage on either side.

### 4. Behavior unchanged but worth noting

- All v0.8.x APIs (`@event_handler`, `assign_async`, `Component`,
  `LiveComponent`, `start_async`, `@background`, etc.) are
  unchanged.
- Wire protocol versions are backwards-compatible with v0.6.1+
  clients.
- `time_travel_max_events` default (100) is unchanged.
- HVR fallback to full-page reload on settings/migration changes
  is unchanged — only view-code edits get the state-preserving
  path.

## Upgrading to 1.0 — Breaking Changes

### 1. VDOM tracking attribute renamed: `data-dj-id` → `dj-id`

The internal VDOM node-tracking attribute has been renamed from `data-dj-id` to `dj-id`
to be consistent with all other `dj-` prefixed attributes (`dj-view`, `dj-click`, `dj-model`,
etc.).

**Impact:** Only affects you if your code reads or queries `data-dj-id` directly (e.g. custom
JavaScript that relies on the attribute for DOM lookups). djust-generated HTML and the client
patch engine are updated automatically.

**Migration:** Replace any `querySelector('[data-dj-id="..."]')` calls with
`querySelector('[dj-id="..."]')`. A system check (`djust.T011`) will warn you about
`data-dj-id` attributes found in your templates.

### 2. Keyed VDOM diffing: `id=` fallback removed; use explicit `dj-key`

Previously, elements with an `id=` attribute had that value silently used as a keyed
diffing key, even when the developer intended `id=` purely as a CSS/JS selector handle.
This caused surprising DOM reuse behaviour when unrelated elements happened to share IDs
across renders.

The implicit fallback is removed in v1.0. Use the explicit `dj-key` attribute to opt in to
keyed diffing:

**Before (relied on implicit id= key):**
```html
<ul>
  {% for item in items %}
    <li id="item-{{ item.id }}">{{ item.name }}</li>
  {% endfor %}
</ul>
```

**After (explicit opt-in):**
```html
<ul>
  {% for item in items %}
    <li id="item-{{ item.id }}" dj-key="{{ item.id }}">{{ item.name }}</li>
  {% endfor %}
</ul>
```

The legacy `data-key` attribute continues to work as an explicit opt-in.

**Impact:** If you had `id=` attributes on list items and were relying on keyed diffing
to preserve element state (focus, scroll position, animations) across re-renders, add
`dj-key="{{ item.pk }}"` to those elements.

**Migration checklist:**
1. Identify dynamic lists in your templates (loops that render repeated elements)
2. On list items where identity-stable DOM reuse matters, add `dj-key="{{ item.pk }}"` or
   `dj-key="{{ item.id }}"`
3. Run `python manage.py check --deploy` — no new warnings should appear

## Upgrading to 0.2.1 — Event Handler Security

Version 0.2.1 defaults `event_security` to `"strict"`: only methods decorated with `@event_handler` are callable via WebSocket or HTTP POST. Undecorated handler methods will be silently blocked.

### Step 1: Enable warn mode (optional)

To identify which handlers need decorators without breaking anything, temporarily use warn mode:

```python
# settings.py
LIVEVIEW_CONFIG = {
    "event_security": "warn",  # Log warnings for undecorated handlers
}
```

Then check your Django logs for messages like:

```
WARNING Deprecation: handler 'increment' on CounterView is not decorated with @event_handler.
This will be blocked in strict mode.
```

### Step 2: Add `@event_handler` to all handler methods

**Before:**
```python
from djust import LiveView

class CounterView(LiveView):
    def mount(self, request):
        self.count = 0

    def increment(self):      # ← blocked in strict mode!
        self.count += 1

    def decrement(self):      # ← blocked in strict mode!
        self.count -= 1
```

**After:**
```python
from djust import LiveView
from djust.decorators import event_handler

class CounterView(LiveView):
    def mount(self, request):
        self.count = 0

    @event_handler
    def increment(self):      # ← allowed
        self.count += 1

    @event_handler
    def decrement(self):      # ← allowed
        self.count -= 1
```

Methods that are **not** called via WebSocket (`mount`, `get_context_data`, private `_helpers`) do **not** need the decorator.

If you already use `@debounce`, `@throttle`, `@optimistic`, `@cache`, or `@rate_limit`, add `@event_handler` as the outermost decorator:

```python
@event_handler
@debounce(wait=0.5)
def search(self, value: str = "", **kwargs):
    ...
```

### Step 3: Switch to strict mode

Remove the `"warn"` override (or set `"strict"` explicitly). This is the default, so you can simply delete the `event_security` key.

### Rate limiting configuration

New rate-limit settings are available (all optional, shown with defaults):

```python
LIVEVIEW_CONFIG = {
    "rate_limit": {
        "rate": 100,                  # Tokens per second
        "burst": 20,                  # Max burst capacity
        "max_warnings": 3,            # Warnings before disconnect (code 4429)
        "max_connections_per_ip": 10,  # Max concurrent WebSocket connections per IP
        "reconnect_cooldown": 5,       # Seconds before a rate-limited IP can reconnect
    },
}
```

## Upgrading from 0.1.x to 0.2.0

Version 0.2.0 includes several breaking changes to improve API consistency. Follow this guide to update your code.

### 1. Event Binding Syntax (0.2.0-alpha.1)

**Change:** All event bindings now use `dj-` prefix instead of `@` prefix.

**Before:**
```html
<button @click="increment">Click</button>
<input @input="search" @keydown.enter="submit">
<div @loading.class="opacity-50">Loading...</div>
```

**After:**
```html
<button dj-click="increment">Click</button>
<input dj-input="search" dj-keydown.enter="submit">
<div dj-loading.class="opacity-50">Loading...</div>
```

**Migration:** Find and replace in your templates:
- `@click` → `dj-click`
- `@input` → `dj-input`
- `@change` → `dj-change`
- `@submit` → `dj-submit`
- `@blur` → `dj-blur`
- `@focus` → `dj-focus`
- `@keydown` → `dj-keydown`
- `@keyup` → `dj-keyup`
- `@loading` → `dj-loading`

### 2. Data Attribute Renames

**Change:** Data attributes renamed for consistency.

| Before | After |
|--------|-------|
| `dj-liveview-root` | `dj-root` |
| `data-live-view` | `dj-view` |
| `data-live-lazy` | `dj-lazy` |
| `data-dj` | `data-dj-id` |

**Migration:** Update any custom JavaScript or templates that reference these attributes.

### 3. Component Import Path

**Change:** The legacy `python/djust/component.py` has been removed.

**Before:**
```python
from djust.component import Component  # Old path
```

**After:**
```python
from djust import Component  # Recommended
# or
from djust.components.base import Component  # Explicit
```

**Migration:** Update import statements. The `djust.Component` import still works but now imports from `components/base.py`.

### 4. LiveComponent Method Rename

**Change:** `get_context()` renamed to `get_context_data()` for Django consistency.

**Before:**
```python
class MyComponent(LiveComponent):
    def get_context(self):
        return {"items": self.items}
```

**After:**
```python
class MyComponent(LiveComponent):
    def get_context_data(self):
        return {"items": self.items}
```

**Migration:** Rename all `get_context()` methods to `get_context_data()` in your LiveComponent subclasses.

### 5. Decorator Attribute Changes

**Change:** Deprecated decorator attributes removed.

**Before (checking decorator metadata):**
```python
if hasattr(method, '_is_event_handler'):
    event_name = method._event_name

if hasattr(method, '_debounce_seconds'):
    wait = method._debounce_seconds
```

**After:**
```python
if hasattr(method, '_djust_decorators'):
    if 'event_handler' in method._djust_decorators:
        event_name = method._djust_decorators['event_handler']['name']

    if 'debounce' in method._djust_decorators:
        wait = method._djust_decorators['debounce']['wait']
```

**Migration:** Update any code that inspects decorator attributes to use the `_djust_decorators` dict.

### 6. WebSocket Message Types

**Change:** Message types renamed for consistency.

| Before | After |
|--------|-------|
| `connected` | `connect` |
| `mounted` | `mount` |
| `hotreload.message` | `hotreload` |

**Migration:** If you have custom WebSocket handling code, update the message type names.

### Quick Migration Script

For templates, you can use these commands to update event bindings:

```bash
# macOS/BSD sed
find . -name "*.html" -exec sed -i '' \
  -e 's/@click=/dj-click=/g' \
  -e 's/@input=/dj-input=/g' \
  -e 's/@change=/dj-change=/g' \
  -e 's/@submit=/dj-submit=/g' \
  -e 's/@blur=/dj-blur=/g' \
  -e 's/@focus=/dj-focus=/g' \
  -e 's/@keydown/dj-keydown/g' \
  -e 's/@keyup/dj-keyup/g' \
  -e 's/@loading/dj-loading/g' \
  {} \;

# GNU/Linux sed
find . -name "*.html" -exec sed -i \
  -e 's/@click=/dj-click=/g' \
  -e 's/@input=/dj-input=/g' \
  -e 's/@change=/dj-change=/g' \
  -e 's/@submit=/dj-submit=/g' \
  -e 's/@blur=/dj-blur=/g' \
  -e 's/@focus=/dj-focus=/g' \
  -e 's/@keydown/dj-keydown/g' \
  -e 's/@keyup/dj-keyup/g' \
  -e 's/@loading/dj-loading/g' \
  {} \;
```

For Python files with `get_context`:

```bash
# macOS/BSD sed
find . -name "*.py" -exec sed -i '' 's/def get_context(self)/def get_context_data(self)/g' {} \;

# GNU/Linux sed
find . -name "*.py" -exec sed -i 's/def get_context(self)/def get_context_data(self)/g' {} \;
```

### Need Help?

If you encounter issues during migration:
1. Check the [CHANGELOG](CHANGELOG.md) for detailed change descriptions
2. Open an issue on [GitHub](https://github.com/djust-org/djust/issues)
