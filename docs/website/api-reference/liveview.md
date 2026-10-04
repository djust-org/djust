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

### `PersistentLiveView`

A `LiveView` subclass whose only difference is `enable_state_snapshot = True`, so a project can opt a group of legacy-exposure views in to the session-backed state restore described in [Scaling djust](../guides/scaling.md). A subclass can set the flag back to `False`. Views with `exposure_policy = "explicit"` ignore the flag and persist only their `state(..., persist=...)` fields. [`djust.C304`](../guides/error-codes.md) still checks each subclass for PII-like attribute names. The flag sends the view's public state to the browser as a signed, not encrypted, blob, so don't use this class for views whose public attributes hold credentials, PII or other users' data.

```python
from djust import PersistentLiveView

class Dashboard(PersistentLiveView):
    template_name = "dashboard.html"
```

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

Always call `super().get_context_data(**kwargs)` so the JIT serialization and change-detection machinery runs. It also matters on the HTTP page-POST fallback: there a view on the default state policy is rebuilt from the dict this method returned on the previous request, so an attribute your override leaves out is unset when the next event handler runs (see [HTTP-Only Mode](../guides/http-only-mode.md#what-the-page-post-fallback-keeps-between-events)). Set state in `mount()`, and add to the result instead of replacing it:

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

---

#### `connected()` and `disconnected()`

Server-side hooks for a view's live connection. They are unrelated to the `connected()` / `disconnected()` callbacks of a client-side `dj-hook` object.

```python
def connected(self):
    self.seat = Seat.claim(self.user)  # only on the live connection, never on the HTTP render

def disconnected(self):
    Seat.release(self.seat)
```

Both take no arguments, are optional, and are regular methods (an `async def` hook fails the mount, or is logged for `disconnected()`). They run on a worker thread, so they can use the ORM like `mount()`. A view that defines neither pays nothing.

##### Lifecycle contract

What happens, in order, when a view goes live (WebSocket or SSE):

1. The view's auth checks, then its `on_mount` hooks. A view that is refused here gets none of the hooks below.
2. `mount()`, or a state restore that replaces it (see [Scaling djust](../guides/scaling.md)).
3. The object-permission check, then `handle_params(params, uri)`.
4. `connected()`. The first render comes after it, so state it sets is in the first frame.
5. Events, `handle_info()`, ticks and background work, until the view's live mount ends.
6. `disconnected()`, then the framework's own cleanup (below).

The HTTP render (`GET`) and the HTTP POST fallback have no live connection, so neither hook is called on them. `mount()` runs again for the live connection.

**What djust guarantees.**

- `connected()` runs once per live mount, after `mount()` (or the restore) and `handle_params()` and before the first render, whether or not the state was restored. A view that gets a restore does not run `mount()` again, but still runs `connected()`.
- If `connected()` raises, the mount fails as it does when `mount()` raises: the client gets the error frame and the view is never rendered.
- `disconnected()` runs at most once for a view, and only for a view that reached step 4 (a view the mount refused, or whose `mount()` or `handle_params()` raised, never did; one whose `connected()` raised did, so it can undo what it claimed). It runs whether or not the view defines `connected()`.
- It runs whenever that view's live mount ends, over WebSocket or SSE: the socket or stream closes (the user left, the network dropped, a rate-limit or authorization close); a `live_redirect` or a second `mount` frame replaces the view (the old view's `disconnected()` runs before the new view's `connected()`); an `unmount` frame removes a view mounted beside the page view; or the view's authorization is revoked. A reconnect is a new live mount: the old view's `disconnected()` ran, and the new view gets `connected()` (and `mount()`, unless its state is restored).
- Every view the socket holds gets its own pair of calls: the page view and each view mounted with a `target_id` (lazy hydration, `mount_batch`).
- An exception in `disconnected()` is logged and does not stop the rest of the teardown, or the teardown of the socket's other views.
- `disconnected()` runs under the tenant the view mounted with, as its event handlers do.

**What djust does not guarantee.**

- That `disconnected()` runs. It is not called when the server process is killed, crashes or loses power, or when the event loop stops before it finishes (a deploy, a worker restart). Anything that must be released even then needs an expiry of its own, such as a timeout or a periodic sweep.
- That the framework's cleanup has not started. Over WebSocket `disconnected()` runs just before the view is released, after the view left its channel groups, its presence was untracked, its tick stopped and its latest state saved. Over an SSE close it runs on a worker thread beside the release (as the presence untrack does). Do not rely on `start_async` tasks, `wait_for_event` waiters, uploads, child views or live handles still being in place.
- That anything `disconnected()` does reaches the client or the saved state. The view is already being dropped: do not rely on events or pushes it queues being sent, or on a change it makes to `self` being rendered or saved.
- A time limit. A `disconnected()` that blocks holds up the release of that view, so keep it short.
- Hooks for views a template embeds with `{% live_render %}`, sticky or not: only the views the transport mounts get them. The framework tears an embedded child down with its parent.
- A hook for a navigation as such. `live_redirect` ends the old view's live mount, so `disconnected()` runs for it although the socket stays open; there is no separate "unmount" hook and no argument that says why the view went.
- Exclusive access to the view. Background work started with `start_async` may still be running while `disconnected()` runs.

Existing views are unaffected unless they already define a callable `connected` or `disconnected`: those are now called. A state attribute of that name (`self.connected = False`) is not callable and is ignored.

#### Disconnect cleanup

The framework cleans up on disconnect by itself: `start_async` tasks are cancelled, presence is untracked, child views are released, and uploads are aborted (an incomplete `ResumableUploadWriter` upload is suspended so the client can resume it after reconnecting). `disconnect()` and `unmount()` methods you define on the view are not called; use `disconnected()` above, and release what it cannot reach (a process that dies runs nothing) with a timeout or a periodic sweep.

**Telling the live mount from the HTTP render.** `mount()` runs twice for a page: once for the HTTP response and again when the WebSocket (or SSE) connection mounts the view. On the live mount the framework sets `self._websocket_session_id` before `mount()` runs; on the HTTP render it is absent. This is the check djust's own presence tracking uses:

```python
def mount(self, request, **kwargs):
    if getattr(self, "_websocket_session_id", None):
        self.claim_seat()  # only on the live connection
```

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

Stream a collection to the template. The items are evaluated immediately (the iterable is turned into a list) and rendered through `streams.<name>`. The stream keeps its items between renders; they are cleared after a render only when the view also declares `temporary_assigns`:

```python
def mount(self, request, **kwargs):
    self.stream("messages", Message.objects.all()[:50])
```

With `limit=N` the stream keeps at most `N` items after the insert, dropping from the edge opposite `at` (appending drops the oldest, prepending drops the newest), and the next render removes those rows from the page. `stream_prune(name, limit, edge="top")` applies the same cap on its own.

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

#### `cancel_async_all()`

Cancel every task this view has scheduled or running. Tasks that have not started are dropped; running tasks are marked cancelled, so their re-render is skipped when they finish (a synchronous callback cannot be interrupted mid-run). Unlike calling `cancel_async()` for each name, a task started later under the same name is not cancelled in advance. The default sticky-child unmount calls it.

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

The handler must start with `handle_` or be decorated with `@event_handler`.

To reach only some sessions (one room of a multi-room view), set `self.push_scope = room` in `mount()` and push with `scope=`: `push_to_view("games.views.RoomView", handler="handle_refresh", scope=room)`. A push without `scope` still reaches every session. See [Server Push](../advanced/server-push.md#scoped-push-one-room-not-every-room).

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
