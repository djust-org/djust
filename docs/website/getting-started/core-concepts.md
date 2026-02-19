# Core Concepts

How djust works, and when to use it.

## What is djust?

djust brings [Phoenix LiveView](https://hexdocs.pm/phoenix_live_view/)-style reactive server-side rendering to Django. Instead of writing JavaScript to update the UI, you write Python. The server renders HTML; the client patches the DOM.

**Key idea:** State lives on the server. Events travel up from the browser; HTML patches travel down. The client is a thin WebSocket layer (~5KB of JS).

## The LiveView Lifecycle

Every request goes through the same cycle:

```
1. HTTP GET  →  mount()  →  render  →  initial HTML sent to browser
2. WebSocket opens
3. User event  →  handler()  →  re-render  →  diff  →  patch sent to client
4. Client patches DOM  →  back to step 3
```

### `mount()`

Called **once** when the page loads. Initialize all state here:

```python
def mount(self, request, **kwargs):
    self.items = []
    self.query = ""
    self._refresh()  # build expensive querysets
```

`request` is the standard Django `HttpRequest`. URL kwargs (from `path("<int:id>/", ...)`) are passed as `**kwargs`.

### `get_context_data()`

Called **before every render** — both the first HTTP render and every WebSocket update. Return the template context:

```python
def get_context_data(self, **kwargs):
    # Assign private state to public context variables
    self.items = self._items
    return {"items": self.items, "query": self.query}
```

### Event handlers

Called when the user interacts with `dj-click`, `dj-input`, etc. After a handler returns, djust automatically re-renders and sends the diff:

```python
@event_handler()
def do_search(self, value: str = "", **kwargs):
    self.query = value
    self._refresh()
    # No need to call render — djust does it automatically
```

## State: Public vs Private

djust uses a naming convention to control what gets serialized:

| Prefix     | Example       | Behavior                                                      |
| ---------- | ------------- | ------------------------------------------------------------- |
| No prefix  | `self.count`  | **Public** — included in template context, serialized for JIT |
| `_` prefix | `self._items` | **Private** — internal state, not serialized                  |

Use private vars for QuerySets and expensive objects. Assign them to public vars in `get_context_data()`:

```python
def _refresh(self):
    self._items = Item.objects.filter(active=True)  # private QuerySet

def get_context_data(self, **kwargs):
    self.items = self._items  # public — JIT-evaluated in Rust
    return super().get_context_data(**kwargs)
```

## The Template Engine

djust uses a Rust-powered template engine that is **compatible with Django's template syntax** — all 57 built-in filters work. Templates render 16-37x faster than Django's Python renderer for typical workloads.

Templates use `dj-*` attributes for event binding:

| Attribute             | Fires when                  | Handler receives          |
| --------------------- | --------------------------- | ------------------------- |
| `dj-click="handler"`  | Button/element clicked      | `**kwargs`                |
| `dj-input="handler"`  | Input value changes (keyup) | `value=` current value    |
| `dj-change="handler"` | Input/select loses focus    | `value=` current value    |
| `dj-submit="handler"` | Form submitted              | All form fields as kwargs |

## VDOM Diffing

On every update, djust:

1. Renders the full template in Rust (fast)
2. Diffs the new DOM against the previous DOM in Rust (very fast)
3. Sends only the changed patches to the client (~bytes, not full HTML)

This means complex re-renders that change one row in a 1000-row table only transmit one row's worth of HTML.

## When to Use LiveView vs Standard Django Views

**Use LiveView for:**

- Forms with real-time validation
- Search/filter interfaces that update as you type
- Live dashboards, counters, feeds
- Multi-step wizards
- Collaborative features (presence, cursors)
- Any UI where you'd otherwise write custom fetch/AJAX code

**Use standard Django views for:**

- Simple read-only pages (no interactivity)
- REST API endpoints
- File download/redirect responses
- Admin-only interfaces that don't need real-time updates

**Both work together:** djust views live alongside standard Django views in the same project. Use `as_view()` for both.

## Security Model

djust enforces strict security by default:

- **Whitelisted handlers only** — only methods decorated with `@event_handler()` can be called by clients. Calling any other method name over WebSocket returns an error.
- **CSRF protection** — WebSocket connections require a valid CSRF token (included automatically by `{% djust_scripts %}`)
- **Auth integration** — use `LoginRequiredMixin` or `@permission_required` exactly as in standard Django views

## Next Steps

- [Guides](../guides/) — real-time features, navigation, presence, uploads
- [State Management](../state/index.md) — debounce, throttle, loading states, optimistic updates
- [API Reference](../api-reference/liveview.md) — complete LiveView API
