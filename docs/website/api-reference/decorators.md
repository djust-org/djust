# Decorators API Reference

```python
from djust.decorators import (
    event_handler,
    debounce,
    throttle,
    optimistic,
    cache,
    client_state,
    loading,
    permission_required,
)
```

---

## `@event_handler`

Mark a method as callable from the client. **Required** on all event handlers — djust blocks any unmarked method for security.

```python
@event_handler(params=None, description="", coerce_types=True)
```

**Parameters:**
- `params` (`list[str]`, optional) — Explicit list of allowed parameter names. Defaults to auto-extraction from the function signature.
- `description` (`str`) — Human-readable description shown in the debug panel. Defaults to the method docstring.
- `coerce_types` (`bool`, default `True`) — Automatically coerce string values from `data-*` attributes to the expected types based on type hints (`"5"` → `5` for `int`).

**Usage:**

```python
# Simple — no arguments
@event_handler()
def increment(self, **kwargs):
    self.count += 1

# With type coercion (item_id="5" → item_id=5)
@event_handler()
def delete(self, item_id: int = 0, **kwargs):
    Item.objects.filter(pk=item_id).delete()

# Input/change: parameter must be named 'value'
@event_handler()
def search(self, value: str = "", **kwargs):
    self.query = value

# Form submit: named fields arrive as kwargs
@event_handler()
def save_form(self, name="", email="", **kwargs):
    User.objects.create(name=name, email=email)

# Disable type coercion to receive raw strings
@event_handler(coerce_types=False)
def raw_handler(self, value: str = "", **kwargs):
    # value is always a string, not coerced
    pass
```

**Rules:**
- Always accept `**kwargs` — djust passes extra metadata
- Always provide default values for all parameters
- `value` is the magic parameter name for `dj-input` and `dj-change` events
- `data-item-id` becomes `item_id` (kebab-case → snake_case)

---

## `@debounce`

Debounce event handler calls on the client side. The handler fires only after the specified delay has elapsed since the last event.

```python
@debounce(wait=0.3, max_wait=None)
```

**Parameters:**
- `wait` (`float`) — Seconds to wait after the last event before firing. Default `0.3`.
- `max_wait` (`float | None`) — Maximum seconds to wait even if events keep firing. Default `None` (unlimited).

**Usage:**

```python
@event_handler()
@debounce(wait=0.5)
def search(self, value: str = "", **kwargs):
    """Fires 500ms after the user stops typing."""
    self.results = Product.objects.filter(name__icontains=value)

@event_handler()
@debounce(wait=0.3, max_wait=2.0)
def autosave(self, content: str = "", **kwargs):
    """Fires 300ms after last change, but always fires within 2 seconds."""
    self.draft = content
```

Must be applied **inside** `@event_handler()` (closer to the function).

---

## `@throttle`

Limit how often a handler fires. Useful for scroll, resize, or mouse-move events.

```python
@throttle(interval=0.1, leading=True, trailing=True)
```

**Parameters:**
- `interval` (`float`) — Minimum seconds between calls. Default `0.1`.
- `leading` (`bool`) — Fire on the first event. Default `True`.
- `trailing` (`bool`) — Fire on the last event after the interval. Default `True`.

**Usage:**

```python
@event_handler()
@throttle(interval=0.1)
def on_scroll(self, position: int = 0, **kwargs):
    """Fires at most 10 times/second."""
    self.scroll_pos = position
```

---

## `@optimistic`

Apply state changes immediately in the UI before the server confirms. If the handler raises, djust rolls back the optimistic update.

```python
@optimistic
```

No arguments — apply directly.

**Usage:**

```python
@event_handler()
@optimistic
def toggle_like(self, item_id: int = 0, **kwargs):
    """UI updates instantly; server confirms asynchronously."""
    item = next(i for i in self.items if i["id"] == item_id)
    item["liked"] = not item["liked"]
```

---

## `@cache`

Cache handler responses client-side. The response is stored in the browser indexed by the specified key parameters.

```python
@cache(ttl=60, key_params=None)
```

**Parameters:**
- `ttl` (`int`) — Cache lifetime in seconds. Default `60`.
- `key_params` (`list[str] | None`) — Parameter names to use as cache key. Default `[]` (caches by handler name only).

**Usage:**

```python
@event_handler()
@cache(ttl=300, key_params=["value"])
def search(self, value: str = "", **kwargs):
    """Results for "laptop" are cached for 5 minutes."""
    self.results = Product.objects.filter(name__icontains=value)[:20]
```

---

## `@client_state`

Share state via a client-side pub/sub bus. When specified keys change, other components subscribed to those keys update automatically.

```python
@client_state(keys)
```

**Parameters:**
- `keys` (`list[str]`) — Attribute names to publish after this handler runs.

**Usage:**

```python
@event_handler()
@client_state(keys=["filter", "sort"])
def update_filter(self, filter: str = "all", **kwargs):
    self.filter = filter
    # Other components listening for 'filter' update automatically
```

---

## `@loading`

Set a boolean attribute to `True` while the handler is running, `False` after. Use to show loading spinners or disable buttons.

```python
@loading(attr)
```

**Parameters:**
- `attr` (`str`) — Name of the boolean attribute to set.

**Usage:**

```python
@event_handler()
@loading("is_saving")
def save(self, **form_data):
    """self.is_saving=True while this runs."""
    time.sleep(1)
    self.saved = True
```

```html
<button dj-click="save" {% if is_saving %}disabled{% endif %}>
    {% if is_saving %}Saving...{% else %}Save{% endif %}
</button>
```

---

## `@permission_required`

Check Django permissions before the handler executes. Returns an error if the user lacks the required permission(s).

```python
@permission_required(perm)
```

**Parameters:**
- `perm` (`str | list[str]`) — Django permission string(s) (e.g., `"myapp.can_delete"`).

**Usage:**

```python
@event_handler()
@permission_required("myapp.can_delete")
def delete_item(self, item_id: int = 0, **kwargs):
    Item.objects.filter(pk=item_id).delete()

# Require multiple permissions (all must be satisfied)
@event_handler()
@permission_required(["myapp.can_edit", "myapp.can_publish"])
def publish(self, **kwargs):
    self.item.published = True
    self.item.save()
```

---

## Decorator Composition

Decorators compose — apply multiple to one handler. Order matters: decorators execute from **outermost to innermost** (top to bottom):

```python
@event_handler()   # outermost — registers the handler
@debounce(0.5)     # wait for typing to stop
@optimistic        # update UI immediately
@cache(ttl=60)     # return cached result if available
def search(self, value: str = "", **kwargs):
    self.results = Product.objects.filter(name__icontains=value)
```

Execution order: `debounce → optimistic → cache → search()`

---

## See Also

- [Events guide](../core-concepts/events.md) — event binding in templates
- [State Management](../state/index.md) — higher-level patterns
- [LiveView API](./liveview.md)
