# LiveView API Reference

## `class LiveView`

Base class for all reactive views. Extend this class to create a LiveView.

```python
from djust import LiveView
```

### Class Attributes

| Attribute           | Type   | Default | Description                                                                   |
| ------------------- | ------ | ------- | ----------------------------------------------------------------------------- |
| `template_name`     | `str`  | —       | Path to a Django template file                                                |
| `template`          | `str`  | —       | Inline HTML template string                                                   |
| `temporary_assigns` | `dict` | `{}`    | State that resets to the default after each render (e.g., `{"messages": []}`) |
| `use_actors`        | `bool` | `False` | Enable actor-based state management                                           |

Either `template_name` or `template` is required.

### Lifecycle Methods

#### `mount(request, **kwargs)`

Called once when the page first loads (HTTP request). Initialize all state here.

**Parameters:**

- `request` — Django `HttpRequest`
- `**kwargs` — URL parameters from the route (e.g., `path("<int:pk>/", ...)` passes `pk=...`)

```python
def mount(self, request, **kwargs):
    self.count = 0
    self.items = []
    pk = kwargs.get("pk")
    if pk:
        self.item = Item.objects.get(pk=pk)
```

---

#### `get_context_data(**kwargs) -> dict`

Called before every render — both the initial HTTP render and every WebSocket update. Return the template context.

Always call `super().get_context_data(**kwargs)` to include djust's required context variables (`dj_view_id`, etc.):

```python
def get_context_data(self, **kwargs):
    context = super().get_context_data(**kwargs)
    context.update({
        "items": self.items,
        "count": self.count,
    })
    return context
```

---

#### `handle_params(params, url, **kwargs)`

Called when URL parameters change via `live_patch()` (soft navigation without a full page reload).

**Parameters:**

- `params` — `dict` of current URL query parameters
- `url` — Current URL string
- `**kwargs` — Additional keyword arguments

```python
def handle_params(self, params, url, **kwargs):
    self.page = int(params.get("page", 1))
    self.sort = params.get("sort", "name")
    self._refresh()
```

---

#### `handle_info(event, data, **kwargs)`

Called when the server sends a message to this LiveView (e.g., from background tasks, PubSub, or `send_update()`).

**Parameters:**

- `event` — Event name string
- `data` — Event payload (dict)
- `**kwargs` — Additional metadata

```python
def handle_info(self, event, data, **kwargs):
    if event == "new_message":
        self.messages.append(data["message"])
    elif event == "task_complete":
        self.is_processing = False
        self.result = data["result"]
```

---

#### `disconnect(**kwargs)`

Called when the WebSocket connection closes (user navigates away, tab closes, etc.). Use for cleanup.

```python
def disconnect(self, **kwargs):
    self._cleanup_resources()
```

---

### Navigation Methods

#### `live_patch(url, params=None)`

Navigate to a new URL without a full page reload. Updates the browser URL and calls `handle_params()`:

```python
@event_handler()
def go_to_page(self, page: int = 1, **kwargs):
    self.live_patch(f"/items/?page={page}")
```

#### `live_redirect(url)`

Navigate to a new URL with a full page load (replaces the current LiveView):

```python
@event_handler()
def logout(self, **kwargs):
    self.live_redirect("/login/")
```

---

### Streaming

#### `stream(name, items)`

Stream a collection to the template — items are JIT-evaluated by the Rust engine when the template renders them, not before:

```python
def mount(self, request, **kwargs):
    self.stream("messages", Message.objects.all()[:50])
```

---

### Server-Push

#### `send_update()`

Trigger a re-render and push the updated HTML to the client immediately (outside of an event handler):

```python
# From a background task or signal handler:
view.send_update()
```

---

### Standard Django Integration

LiveView is a Django class-based view. Use `as_view()` in URL configuration:

```python
from django.urls import path
from myapp.views import MyView

urlpatterns = [
    path("items/", MyView.as_view(), name="items"),
    path("items/<int:pk>/", MyView.as_view(), name="item-detail"),
]
```

Authentication mixins work as normal:

```python
from django.contrib.auth.mixins import LoginRequiredMixin
from djust import LiveView

class ProtectedView(LoginRequiredMixin, LiveView):
    login_url = "/login/"
    template_name = "protected.html"
```

---

## State Conventions

| Pattern       | Meaning                                   |
| ------------- | ----------------------------------------- |
| `self.count`  | Public — available in template context    |
| `self._items` | Private — not serialized, not in template |

Private vars (underscore prefix) are useful for QuerySets and large objects that shouldn't be JIT-serialized.

---

## See Also

- [Decorators API](./decorators.md)
- [Components API](./components.md)
- [Testing API](./testing.md)
- [Core Concepts: LiveView](../core-concepts/liveview.md)
