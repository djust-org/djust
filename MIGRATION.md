# Migration Guide

This guide helps you upgrade between major versions of djust.

## Upgrading to 0.2.1 — Event Handler Security

Version 0.2.1 defaults `event_security` to `"strict"`: only methods decorated with `@event` or `@event_handler` (or listed in `_allowed_events`) are callable via WebSocket. Undecorated handler methods will be silently blocked.

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
WARNING Deprecation: handler 'increment' on CounterView is not decorated with @event.
This will be blocked in strict mode.
```

### Step 2: Add `@event` to all handler methods

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
from djust.decorators import event

class CounterView(LiveView):
    def mount(self, request):
        self.count = 0

    @event
    def increment(self):      # ← allowed
        self.count += 1

    @event
    def decrement(self):      # ← allowed
        self.count -= 1
```

Methods that are **not** called via WebSocket (`mount`, `get_context_data`, private `_helpers`) do **not** need the decorator.

If you already use `@event_handler()`, `@debounce`, `@throttle`, `@optimistic`, `@cache`, or `@rate_limit`, add `@event` as the outermost decorator:

```python
@event
@debounce(wait=0.5)
def search(self, value: str = "", **kwargs):
    ...
```

### Step 3: Alternative — bulk allowlisting

For views with many handlers, use `_allowed_events` instead of decorating each method:

```python
class MyView(LiveView):
    _allowed_events = frozenset({"handler_a", "handler_b", "handler_c"})
```

### Step 4: Switch to strict mode

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
| `data-liveview-root` | `data-djust-root` |
| `data-live-view` | `data-djust-view` |
| `data-live-lazy` | `data-djust-lazy` |
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
