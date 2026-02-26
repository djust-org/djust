# State Management

djust's state management decorators replace patterns that traditionally require JavaScript — debouncing, throttling, loading indicators, optimistic updates, caching, and more. All in Python.

## Quick Decision Guide

```
User is typing?           → @debounce(wait=0.5)
Rapid scroll/resize?      → @throttle(interval=0.1)
Need instant UI feedback? → @optimistic
Same query repeated?      → @cache(ttl=300)
Coordinating components?  → @client_state(keys=[...])
Auto-save forms?          → DraftModeMixin
```

## Debounce

Wait until the user stops typing before querying the server:

```python
from djust.decorators import event_handler, debounce

@event_handler()
@debounce(wait=0.5)  # 500ms after last keystroke
def search(self, value: str = "", **kwargs):
    self.results = Product.objects.filter(name__icontains=value)[:20]
```

Without `@debounce`, every keystroke fires a server request. With it, only one request fires per typing pause — typical for search boxes.

## Throttle

Limit how often a handler can fire — useful for scroll, resize, or other high-frequency events:

```python
from djust.decorators import event_handler, throttle

@event_handler()
@throttle(interval=0.1)  # Max 10 calls/second
def on_scroll(self, position: int = 0, **kwargs):
    self.scroll_position = position
```

## Loading States

Show feedback while a slow handler is running:

```python
from djust.decorators import event_handler, loading

@event_handler()
@loading("is_saving")
def save(self, **form_data):
    """Sets self.is_saving=True while running, False when done."""
    time.sleep(1)  # Simulate slow operation
    self.saved = True
```

```html
<button dj-click="save" {% if is_saving %}disabled{% endif %}>
    {% if is_saving %}Saving...{% else %}Save{% endif %}
</button>
```

## Optimistic Updates

Apply the state change immediately in the UI before the server confirms — makes the interface feel instant:

```python
from djust.decorators import event_handler, optimistic

@event_handler()
@optimistic
def toggle_like(self, item_id: int = 0, **kwargs):
    """UI updates instantly; server confirms asynchronously."""
    item = next(i for i in self.items if i["id"] == item_id)
    item["liked"] = not item["liked"]
```

If the handler raises an exception, djust rolls back the optimistic state change.

## Caching

Cache handler results to avoid redundant queries:

```python
from djust.decorators import event_handler, cache

@event_handler()
@cache(ttl=300, key_params=["value"])  # Cache for 5 minutes per unique value
def search(self, value: str = "", **kwargs):
    self.results = Product.objects.filter(name__icontains=value)[:20]
```

A search for "laptop" costs one DB query; subsequent searches for "laptop" within 5 minutes are free.

## Composing Decorators

Decorators compose — apply multiple to one handler. Order matters: decorators apply top-to-bottom (outer-to-inner):

```python
@event_handler()
@debounce(wait=0.5)   # 1. Wait for typing to stop
@optimistic           # 2. Update UI immediately
@cache(ttl=60)        # 3. Return cached result if available
def search(self, value: str = "", **kwargs):
    self.results = Product.objects.filter(name__icontains=value)[:20]
```

## Client State

Coordinate state across multiple components or views with `@client_state`. State is stored client-side and automatically synced:

```python
from djust.decorators import event_handler, client_state

@event_handler()
@client_state(keys=["filter", "sort"])
def update_filter(self, filter: str = "all", **kwargs):
    self.filter = filter
    self._refresh()
```

When `filter` changes, other components subscribed to the same key update automatically — without a server round-trip.

## DraftModeMixin

Auto-save form input to localStorage so users don't lose work on navigation or accidental close:

```python
from djust.mixins import DraftModeMixin
from djust import LiveView

class ContactFormView(DraftModeMixin, LiveView):
    template_name = "contact.html"
    draft_fields = ["name", "email", "message"]  # Fields to auto-save
    draft_ttl = 3600  # Expire after 1 hour
```

The draft is restored automatically when the user returns to the page.

## Debugging Decorators

Enable client-side logging to see decorator behavior:

```javascript
window.djustDebug = true;
```

You'll see logs like:

```
[djust:debounce] Waiting 500ms for search(value=laptop)
[djust:cache] Cache hit for search(value=laptop) - age: 45s / TTL: 300s
[djust:state] Published to bus: filter=electronics
```

## Full Reference

| Decorator                 | Parameters                              | Use case             |
| ------------------------- | --------------------------------------- | -------------------- |
| `@debounce(wait)`         | `wait`: seconds (float)                 | Search, autosave     |
| `@throttle(interval)`     | `interval`: seconds (float)             | Scroll, resize       |
| `@loading(attr)`          | `attr`: attribute name (str)            | Long operations      |
| `@background`             | —                                       | API calls, AI gen    |
| `@optimistic`             | —                                       | Toggles, counters    |
| `@cache(ttl, key_params)` | `ttl`: seconds, `key_params`: list[str] | Expensive queries    |
| `@client_state(keys)`     | `keys`: list[str]                       | Multi-component sync |
| `DraftModeMixin`          | `draft_fields`, `draft_ttl`             | Auto-save forms      |

For detailed API docs, see [API Reference: Decorators](../api-reference/decorators.md).

## More Resources

- [State Management Tutorial](../../state-management/STATE_MANAGEMENT_TUTORIAL.md) — step-by-step product search example
- [Patterns & Best Practices](../../state-management/STATE_MANAGEMENT_PATTERNS.md) — common patterns and anti-patterns
- [Examples](../../state-management/STATE_MANAGEMENT_EXAMPLES.md) — copy-paste ready examples
