# Decorators API Reference

> **`@optimistic` is INERT.** It records handler metadata that nothing in the
> shipped client reads, so applying it changes nothing at runtime — a bare
> `@optimistic` declares no DOM change for a client to apply. Examples below
> that use it still work, but without the optimistic-update behaviour they
> describe. Tracked in issue #2699.
>
> `@debounce` and `@throttle` ARE implemented (#2656) — the client gate is
> `static/djust/src/05-handler-rate-limit.js`, configured from the mount
> frame's `handler_config`.


```python
from djust.decorators import (
    event_handler,
    debounce,
    throttle,
    optimistic,
    cache,
    client_state,
    permission_required,
    rate_limit,
    background,
    server_function,
)
```

The module also exports `reactive`, `state`, `computed` and `on_mount`, which are covered in their own guides.

---

## `@event_handler`

Mark a method as callable from the client. **Required** on all event handlers — djust blocks any unmarked method for security.

```python
@event_handler(params=None, description="", coerce_types=True, expose_api=False, serialize=None)
```

**Parameters:**

- `params` (`list[str]`, optional) — Explicit list of allowed parameter names. Defaults to auto-extraction from the function signature.
- `description` (`str`) — Human-readable description shown in the debug panel. Defaults to the method docstring.
- `coerce_types` (`bool`, default `True`) — Automatically coerce string values from `data-*` attributes to the expected types based on type hints (`"5"` → `5` for `int`).
- `expose_api` (`bool`, default `False`) — Also expose the handler as an HTTP API endpoint at `POST /djust/api/<view_slug>/<handler_name>/`, with the same validation, permissions and rate limiting. On that transport the handler's return value is the response body.
- `serialize` (callable or method name, optional) — Override the HTTP API response shape. HTTP only; requires `expose_api=True` (otherwise `TypeError` at decoration time).

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

- Declare the parameters the binding sends; `manage.py check` reports a mismatch (`djust.T020`)
- Under the legacy policy, `dj-input` and `dj-change` also send `field` and `_target`, and `dj-submit` sends `_target` with the form fields: keep `**kwargs` on those handlers, or declare the names
- Give a parameter a default when a binding may omit it
- `value` is the magic parameter name for `dj-input` and `dj-change` events
- `data-item-id` becomes `item_id` (kebab-case → snake_case)

---

## `@debounce`

Debounce the handler on the client. The browser delays the SEND until `wait` seconds after the last event, so a burst of keystrokes reaches the server as one event carrying the last payload. Implemented in `static/djust/src/05-handler-rate-limit.js`; the config rides the mount frame as `handler_config`.

```python
@debounce(wait=0.3, max_wait=None)
```

**Parameters:**

- `wait` (`float`) — Seconds to wait after the last event before firing. Default `0.3`.
- `max_wait` (`float | None`) — Upper bound on the total delay, measured from the FIRST event of the burst, so a user who never pauses still gets a send. Default `None` (unlimited).

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

By convention it is applied **inside** `@event_handler()` (closer to the function); since it only records metadata, the order does not change its behaviour.

---

## `@throttle`

Cap how often the handler is SENT — at most once per `interval`. With both edges on (the default) the first event of a window goes immediately and everything inside the window collapses into one trailing send carrying the last payload. Useful for scroll, resize, or mouse-move events. When a handler carries both `@debounce` and `@throttle`, `@debounce` wins.

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

**INERT (#2699):** records metadata only. No optimistic UI update and no rollback occur; the handler behaves exactly like an undecorated one.

```python
@optimistic
```

No arguments — apply directly.

**Usage:**

```python
@event_handler()
@optimistic
def toggle_like(self, item_id: int = 0, **kwargs):
    """Behaves like an undecorated handler today."""
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

> **INERT (#2680)** — stamps metadata nothing in the shipped client reads, so a
> decorated handler behaves exactly like an undecorated one. The `StateBus` it
> named was deleted in #2680.

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

## Loading states

Loading indicators are **not a decorator**. They are declarative attributes on
the element that fires the event, so they need no handler code at all:

```html
<button dj-click="save" dj-loading.disable>Save</button>
<div dj-loading.show style="display:none">Saving...</div>
```

See [Loading States & Background Work](../guides/loading-states.md) for the
full set — `.show`, `.hide`, `.disable`, `.class`, and `.for` scoping.

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

**Name collision with the view-level attribute.** `LiveView.permission_required` is also a class attribute (the permission checked at mount). Once a view assigns it, the name `permission_required` inside that class body is the string, not the decorator, and `@permission_required("...")` raises `TypeError: 'str' object is not callable` at import time. To use both in one view, import the decorator under another name, or spell it through its module:

```python
from djust import LiveView
from djust import decorators
from djust.decorators import event_handler
from djust.decorators import permission_required as require_permission


class BoardView(LiveView):
    login_required = True
    permission_required = "app.view_board"  # view-level: checked at mount

    @require_permission("app.add_decision")  # handler-level
    @event_handler()
    def decide(self, **kwargs): ...

    @decorators.permission_required("app.delete_decision")  # also fine
    @event_handler()
    def undo(self, **kwargs): ...
```

System check `djust.S009` follows the import, so both spellings count as a per-handler gate.

---

## `@background`

Run the entire event handler in a background thread after flushing current state. The view re-renders and sends patches when the handler completes.

```python
@background
```

No arguments — apply directly.

**Usage:**

```python
from djust.decorators import background

@event_handler
@background
def generate_content(self, prompt: str = "", **kwargs):
    """Entire method runs in background thread."""
    try:
        self.content = call_llm(prompt)  # Long-running operation
    except Exception as e:
        self.error = str(e)
    finally:
        self.generating = False
```

**How it works:**

1. Current view state is flushed to client
2. Handler executes in background thread
3. View re-renders and sends patches when handler completes

Because the whole body runs in the background, state you set inside it is rendered only once, when the body finishes. A `self.generating = True` at the top of the body never reaches the client. To show progress, use `dj-loading.*` attributes on the triggering element, or set the flag in a normal handler and then call `self.start_async(...)` (see below).

The first response carries `async_pending: true`, so a `dj-loading.*` indicator on the triggering element stays on until the background work finishes and its result is rendered.

**Task naming and cancellation:**

The task name is automatically set to the handler's function name. Cancel via `self.cancel_async(name)`:

```python
@event_handler
@background
def long_operation(self, **kwargs):
    # Task name is "long_operation"
    ...

@event_handler
def cancel_operation(self, **kwargs):
    self.cancel_async("long_operation")
```

**Combining with other decorators:**

```python
@event_handler
@debounce(wait=0.5)
@background
def auto_save(self, **kwargs):
    # Debounced and runs in background
    self.save_draft()
```

**When to use `@background` vs `start_async()`:**

- Use `@background` when the **entire handler** should run in background
- Use `self.start_async(callback)` when you need to update state **before** starting background work, or need multiple concurrent tasks with different names

See also: [Loading States & Background Work guide](../guides/loading-states.md)

---

## `@rate_limit`

Rate-limit a handler on the server with a per-handler token bucket. When the limit is exceeded, the event is dropped and the client is warned.

```python
@rate_limit(rate=10, burst=5)
```

**Parameters:**

- `rate` (`float`) — Tokens per second (sustained rate). Default `10`.
- `burst` (`int`) — Maximum burst capacity. Default `5`.
- `on_exceed` (`"disconnect"` or `"drop"`) — What a rejection costs the connection. Default `"disconnect"`: each rejection counts toward the connection's warning budget (`DJUST_CONFIG["rate_limit"]["max_warnings"]`, default 3), and at the limit djust closes the WebSocket with code 4429 and puts the client IP on a reconnect cooldown. `"drop"` only drops the event and warns the client, so a quick honest burst never disconnects anyone. The connection's global per-message limit still disconnects a flood in either mode.

**Usage:**

```python
@rate_limit(rate=5, burst=3)
@event_handler()
def expensive_operation(self, **kwargs):
    ...

@rate_limit(rate=2, burst=4, on_exceed="drop")
@event_handler()
def emote(self, **kwargs):
    ...
```

---

## `@server_function`

Mark a method as a same-origin browser RPC target. The client calls it with `await djust.call('<view_slug>', '<method_name>', {params})` and receives its JSON-serialized return value, with no re-render. It cannot be combined with `@event_handler`.

```python
@server_function
def search(self, q: str = "", **kwargs) -> list[dict]:
    return [{"id": p.id, "name": p.name} for p in Product.objects.filter(name__icontains=q)[:10]]
```

---

## Decorator Composition

Decorators compose — apply multiple to one handler:

```python
@event_handler()   # registers the handler
@debounce(0.5)     # wait for typing to stop
@cache(ttl=60)     # return cached result if available
def search(self, value: str = "", **kwargs):
    self.results = Product.objects.filter(name__icontains=value)
```

`@debounce`, `@throttle` and `@cache` only record metadata that the client reads, so their order is not significant. `@optimistic` is inert. `@background` is the only one that wraps the handler at runtime.

---

## See Also

- [Events guide](../core-concepts/events.md) — event binding in templates
- [State Management](../state/index.md) — higher-level patterns
- [LiveView API](./liveview.md)
