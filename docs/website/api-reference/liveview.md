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
| `on_mount`          | `list` | `[]`    | List of hook functions to run before `mount()` (see [on_mount Hooks](../guides/on-mount-hooks.md)) |

Either `template_name` or `template` is required.

### Lifecycle Methods

#### `on_mount` hooks

Cross-cutting functions that run before `mount()` on every mount and reconnect. Declare hooks with the `@on_mount` decorator and attach them via the `on_mount` class attribute.

**Hook signature:** `def hook(view, request, **kwargs) -> Optional[str]`

Return `None` to continue, or a redirect URL string to halt mounting.

```python
from djust.hooks import on_mount

@on_mount
def require_verified_email(view, request, **kwargs):
    if not request.user.email_verified:
        return '/verify-email/'

class ProfileView(LiveView):
    on_mount = [require_verified_email]
```

Hooks are inherited via MRO (parent-first, deduplicated). See [on_mount Hooks Guide](../guides/on-mount-hooks.md) for full details.

---

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

### Background Work

#### `start_async(callback, *args, name=None, **kwargs)`

Schedule a callback to run in a background thread after flushing the current view state to the client. The view automatically re-renders when the callback completes.

**Parameters:**

- `callback` — Method to run in background (receives view instance as `self`)
- `*args` — Positional arguments forwarded to callback
- `name` (`str`, optional) — Task name for tracking and cancellation
- `**kwargs` — Keyword arguments forwarded to callback

**Usage:**

```python
@event_handler()
def generate_report(self, **kwargs):
    self.generating = True  # Sent to client immediately
    self.start_async(self._do_generate, name="report")

def _do_generate(self):
    self.report = call_slow_api()  # Runs in background
    self.generating = False  # View re-renders when this returns
```

See [Loading States & Background Work](../guides/loading-states.md) for detailed examples.

---

#### `cancel_async(name)`

Cancel a pending or running async task by name.

**Parameters:**

- `name` (`str`) — Name of the task to cancel

**Usage:**

```python
@event_handler()
def cancel_export(self, **kwargs):
    self.cancel_async("export")
    self.exporting = False
```

---

#### `handle_async_result(name, result=None, error=None)`

Optional callback invoked when an async task completes or fails. Override this method to handle completion/errors.

**Parameters:**

- `name` (`str`) — Name of the completed task
- `result` — Return value from the callback (if any)
- `error` (`Exception`, optional) — Exception raised by the callback

**Usage:**

```python
def handle_async_result(self, name: str, result=None, error=None):
    if error:
        self.error_message = f"Task {name} failed: {error}"
    elif name == "export":
        self.status = "Export complete"
```

---

### Flash Messages

#### `put_flash(level, message)`

Queue a flash message to be sent to the connected client. The message is rendered into the `#dj-flash-container` element (inserted by the `{% dj_flash %}` template tag).

**Parameters:**

- `level` (`str`) -- Severity/category string. Common values: `"info"`, `"success"`, `"warning"`, `"error"`. Any string is accepted -- it becomes a CSS class `dj-flash-{level}`.
- `message` (`str`) -- Human-readable message text.

```python
@event_handler()
def save(self, **kwargs):
    save_item(self.name)
    self.put_flash("success", "Item saved!")
```

---

#### `clear_flash(level=None)`

Queue a command to clear flash messages on the client.

**Parameters:**

- `level` (`str`, optional) -- If provided, only clear messages with this level. If `None`, clear all flash messages.

```python
@event_handler()
def dismiss_errors(self, **kwargs):
    self.clear_flash("error")   # clear only errors
    self.clear_flash()          # clear all
```

See [Flash Messages Guide](../guides/flash-messages.md) for detailed examples and CSS styling.

---

### Document Metadata

#### `page_title` (property)

Get or set the browser tab title. Setting this property queues a side-channel WebSocket message that updates `document.title` on the client without a VDOM diff.

```python
def mount(self, request, **kwargs):
    self.page_title = "Dashboard"

@event_handler()
def select_tab(self, tab: str = "", **kwargs):
    self.page_title = f"Dashboard - {tab.title()}"
```

---

#### `page_meta` (property)

Get or set document `<meta>` tags. Setting this property to a dict queues side-channel messages that update or create `<meta>` tags in the document `<head>`. Tags starting with `og:` or `twitter:` use the `property` attribute; all others use `name`.

```python
@event_handler()
def select_article(self, article_id: int = 0, **kwargs):
    article = Article.objects.get(pk=article_id)
    self.page_meta = {
        "description": article.summary,
        "og:title": article.title,
        "og:image": article.image_url,
    }
```

See [Document Metadata Guide](../guides/document-metadata.md) for detailed examples.

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
