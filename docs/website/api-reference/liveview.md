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

Called on the initial HTTP request and again when the WebSocket connects (unless state is restored from a snapshot). Keep it idempotent and initialize all state here.

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

Always call `super().get_context_data(**kwargs)` so the JIT serialization and change-detection machinery runs:

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

#### `handle_params(params, uri)`

Called after `mount()` on the initial WebSocket connect, on every `live_patch()` (soft navigation without a full page reload), and on browser back/forward navigation.

**Parameters:**

- `params` — `dict` of current URL query parameters
- `uri` — Current path plus query string

```python
def handle_params(self, params, uri):
    self.page = int(params.get("page", 1))
    self.sort = params.get("sort", "name")
    self._refresh()
```

---

#### `handle_info(message)`

Receives out-of-band messages, such as PostgreSQL `NOTIFY` events delivered to a view that subscribed with `self.listen(channel)`. It is called with a single `dict` argument; the default implementation does nothing. Background tasks and `push_to_view()` do not go through this hook.

**Parameters:**

- `message` — `dict` with a `"type"` key (e.g. `"db_notify"`) and the message payload

```python
def handle_info(self, message):
    if message["type"] == "db_notify":
        self.refresh()
```

> **Known issue: #2962.** `self.listen()` called in `mount()` never subscribes, so `handle_info` receives nothing. Workaround: declare the channels at class level with `_listen_channels = {"orders"}`.

---

#### Disconnect cleanup

LiveView has no user-level disconnect hook in 1.2: a `disconnect()`, `unmount()` or `disconnected()` method you define on the view is never called when the WebSocket closes. The framework itself cleans up on disconnect: `start_async` tasks are cancelled, presence is untracked, and uploads and child views are released. Release any other resources you open in `mount()` by other means (for example, a timeout or a periodic sweep).

---

### Navigation Methods

#### `live_patch(params=None, path=None, replace=False)`

Update the browser URL without remounting the view. `params` is merged into the current query string (`{}` clears it), `path` optionally changes the path, and `replace=True` uses `replaceState` instead of `pushState`. Calls `handle_params()`:

```python
@event_handler()
def go_to_page(self, page: int = 1, **kwargs):
    self.live_patch(params={"page": page})
```

#### `live_redirect(path, params=None, replace=False)`

Navigate to a different LiveView over the existing WebSocket. The current view is unmounted and the new one mounted, with no full page reload or reconnection. For a real full-page navigation, use a normal link or an HTTP redirect.

```python
@event_handler()
def logout(self, **kwargs):
    self.live_redirect("/login/")
```

---

### Streaming

#### `stream(name, items, dom_id=None, at=-1, reset=False, limit=None)`

Stream a collection to the template. The items are evaluated immediately (the iterable is turned into a list) and sent as stream inserts; the stream is cleared from server memory after each render:

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

#### `push_to_view()`

LiveView has no `send_update()` method. To update connected clients from outside an event handler (a Celery task, signal handler or management command), use `push_to_view()` (or `await apush_to_view()` from async code). It either sets state or calls a handler on every connected instance of the view:

```python
from djust import push_to_view

# From a background task or signal handler:
push_to_view("myapp.views.DashboardView", state={"alert_count": 5})
push_to_view("myapp.views.DashboardView", handler="handle_refresh", payload={"source": "celery"})
```

The handler must start with `handle_` or be decorated with `@event_handler`. See [Server Push](../advanced/server-push.md).

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
