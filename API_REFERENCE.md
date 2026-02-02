# djust v0.3 API Reference

## Table of Contents
- [LiveView Class](#liveview-class)
- [Mixins](#mixins)
- [Template Directives](#template-directives)
- [LiveForm](#liveform)
- [Testing](#testing)
- [Push API](#push-api)
- [Routing](#routing)

---

## LiveView Class

```python
from djust import LiveView
```

The base class for all reactive views. Composes all mixins automatically.

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `template_name` | `str` | Path to Django template file |
| `template_string` | `str` | Inline template string (alternative to `template_name`) |
| `use_actors` | `bool` | Enable actor-based state management (default: `False`) |
| `temporary_assigns` | `list` | Attributes cleared after each render |

### Methods

#### `mount(request, **kwargs) → None`
Called when the view is first loaded. Initialize state here.

```python
def mount(self, request, **kwargs):
    self.count = 0
    self.items = []
```

**Parameters:**
- `request` — Django `HttpRequest` object
- `**kwargs` — URL keyword arguments

---

#### `get_context_data() → dict`
Return template context. Override to add computed values.

```python
def get_context_data(self):
    ctx = super().get_context_data()
    ctx["total"] = sum(self.items)
    return ctx
```

---

#### `@event_handler`
Decorator marking a method as callable from the client.

```python
from djust.decorators import event_handler

@event_handler
def increment(self, amount=1, **kwargs):
    self.count += amount
```

---

## Mixins

All mixins are automatically included in `LiveView`. Import individually only for type hints or standalone use.

### NavigationMixin

```python
from djust.mixins.navigation import NavigationMixin
```

#### `live_patch(params=None, path=None, replace=False) → None`
Update browser URL without remounting the view.

**Parameters:**
- `params` (`dict | None`) — Query parameters to set. `None` keeps current, `{}` clears.
- `path` (`str | None`) — New URL path. Defaults to current.
- `replace` (`bool`) — Use `replaceState` instead of `pushState`.

```python
@event_handler
def filter(self, category="all", **kwargs):
    self.category = category
    self.live_patch(params={"category": category, "page": 1})
```

---

#### `live_redirect(path, params=None, replace=False) → None`
Navigate to a different LiveView over the existing WebSocket.

**Parameters:**
- `path` (`str`) — URL path to navigate to.
- `params` (`dict | None`) — Optional query parameters.
- `replace` (`bool`) — Use `replaceState`.

```python
@event_handler
def go_to_detail(self, item_id, **kwargs):
    self.live_redirect(f"/items/{item_id}/")
```

---

#### `handle_params(params, uri) → None`
Callback when URL params change (browser back/forward, `live_patch`). Override in your view.

**Parameters:**
- `params` (`dict`) — Current URL query parameters.
- `uri` (`str`) — Current full URI.

```python
def handle_params(self, params, uri):
    self.page = int(params.get("page", 1))
```

---

### StreamingMixin

```python
from djust.streaming import StreamingMixin
```

#### `await stream_to(stream_name, target=None, html=None) → None`
Send a streaming partial update to the client. Async only.

**Parameters:**
- `stream_name` (`str`) — Name of the stream (matches `dj-stream` attribute).
- `target` (`str | None`) — CSS selector to target.
- `html` (`str | None`) — HTML to send directly. If `None`, re-renders the stream.

```python
@event_handler
async def chat(self, message, **kwargs):
    async for token in llm_stream(message):
        self.response += token
        await self.stream_to("output", target="#chat-output")
```

---

#### `await stream_insert(stream_name, html, at="append") → None`
Insert HTML into a stream container.

**Parameters:**
- `stream_name` (`str`) — Stream name.
- `html` (`str`) — HTML content to insert.
- `at` (`str`) — Position: `"append"` or `"prepend"`.

---

#### `await stream_delete(stream_name, selector) → None`
Remove an element from a stream.

**Parameters:**
- `stream_name` (`str`) — Stream name.
- `selector` (`str`) — CSS selector of element to remove.

---

#### `await push_state() → None`
Send a full re-render mid-handler for non-stream state changes.

---

### PresenceMixin

```python
from djust.presence import PresenceMixin
```

**Class attributes:**
- `presence_key` (`str`) — Group identifier, supports `{kwarg}` interpolation.

#### `track_presence(meta=None) → None`
Start tracking this user's presence in the group.

**Parameters:**
- `meta` (`dict | None`) — User metadata (name, color, avatar, etc.)

```python
self.track_presence(meta={"name": request.user.username, "color": "#6c63ff"})
```

---

#### `list_presences() → list[dict]`
Returns list of all active presences in the group.

---

#### `presence_count() → int`
Returns count of active users in the group.

---

#### `handle_presence_join(presence) → None`
Override. Called when a user joins.

---

#### `handle_presence_leave(presence) → None`
Override. Called when a user leaves.

---

### LiveCursorMixin

Extends `PresenceMixin` with real-time cursor position tracking.

#### `handle_cursor_move(x, y) → None`
Override. Called when a client sends cursor position. Auto-broadcasts to group.

---

### UploadMixin

```python
from djust.uploads import UploadMixin
```

#### `allow_upload(name, accept=None, max_entries=1, max_file_size=10_000_000) → None`
Configure an upload slot.

**Parameters:**
- `name` (`str`) — Upload name (referenced in templates).
- `accept` (`str | None`) — Accepted file extensions (e.g. `".jpg,.png"`).
- `max_entries` (`int`) — Maximum number of files.
- `max_file_size` (`int`) — Max file size in bytes.

```python
self.allow_upload('avatar', accept='.jpg,.png,.webp', max_entries=1, max_file_size=5_000_000)
```

---

#### `consume_uploaded_entries(name) → Generator[UploadEntry]`
Process completed uploads. Each `UploadEntry` has:
- `.client_name` — Original filename
- `.client_type` — MIME type
- `.client_size` — File size in bytes
- `.file` — File-like object for storage

```python
for entry in self.consume_uploaded_entries('avatar'):
    path = default_storage.save(f'avatars/{entry.client_name}', entry.file)
```

---

### PushEventMixin

```python
from djust.mixins.push_events import PushEventMixin
```

#### `push_event(event, payload=None) → None`
Push an event to connected client(s).

**Parameters:**
- `event` (`str`) — Event name.
- `payload` (`dict | None`) — Data to send.

```python
self.push_event("flash", {"message": "Saved!", "type": "success"})
self.push_event("scroll_to", {"selector": "#bottom"})
```

Events dispatch to `dj-hook` instances via `handleEvent()` and as `CustomEvent` on `document`.

---

### ModelBindingMixin

```python
from djust.mixins.model_binding import ModelBindingMixin
```

Provides automatic `update_model` handler for `dj-model` bindings.

**Class attributes:**
- `allowed_model_fields` (`set | None`) — Restrict which fields can be bound. `None` = all non-forbidden.

Security: fields starting with `_` and framework internals (`template_name`, `request`, `session`, etc.) are blocked.

---

### FormMixin

```python
from djust.forms import FormMixin
```

Django Forms integration with real-time validation.

**Class attributes:**
- `form_class` (`Type[forms.Form] | None`) — Django Form class.

#### `validate_field(field_name, value) → None`
Validate a single field (for live inline validation).

#### `form_valid(form) → None`
Override. Called when form passes validation.

#### `form_invalid(form) → None`
Override. Called when form fails validation.

---

## Template Directives

### Event Directives

#### `dj-click="handler_name"`
Fire event on click.
```html
<button dj-click="increment">+1</button>
<button dj-click="delete" dj-click:item_id="{{ item.id }}">Delete</button>
```

#### `dj-input="handler_name"`
Fire event on input (every keystroke). Sends `value` parameter.
```html
<input dj-input="search" />
```

#### `dj-change="handler_name"`
Fire event on change (select, checkbox) or blur (text inputs). Sends `value`.
```html
<select dj-change="sort">...</select>
```

#### `dj-submit="handler_name"`
Fire event on form submission. Sends all form fields as parameters.
```html
<form dj-submit="save_contact">
    <input name="name" /><input name="email" />
    <button type="submit">Save</button>
</form>
```

---

### Binding Directives

#### `dj-model="attribute_name"`
Two-way data binding between input and server attribute.
```html
<input dj-model="search_query" />
<select dj-model="sort_by">...</select>
<textarea dj-model="message"></textarea>
```

**Modifiers:**
- `dj-model.lazy` — Sync on blur only.
- `dj-model.debounce-500` — Custom debounce in ms.
- `dj-model.trim` — Trim whitespace.

---

### Behavior Directives

#### `dj-confirm="message"`
Show `confirm()` dialog before `dj-click`. Cancels event if user declines.
```html
<button dj-click="delete" dj-confirm="Are you sure?">Delete</button>
```

#### `dj-debounce="ms"`
Debounce any event directive.
```html
<input dj-input="search" dj-debounce="300" />
<button dj-click="save" dj-debounce="1000" />
```

#### `dj-throttle="ms"`
Throttle any event directive.
```html
<div dj-scroll="load_more" dj-throttle="100" />
```

#### `dj-loading.modifier`
Control element state while an event is processing.
```html
<button dj-click="save" dj-loading.disable>Save</button>
<div dj-loading.class="opacity-50">Content</div>
<span dj-loading.show>Saving...</span>
<div dj-loading.remove>Hidden while loading</div>
```

#### `dj-target="selector"`
Scope DOM updates to a specific element, reducing patch size.
```html
<input dj-input="search" dj-target="#results" />
<div id="results" dj-target>...</div>
```

#### `dj-optimistic="expression"`
Apply client-side updates immediately before server confirmation.
```html
<button dj-click="toggle_like" dj-optimistic="liked:!liked">❤️</button>
<button dj-click="increment" dj-optimistic="count:count+1">+1</button>
<span dj-click="mark_read" dj-optimistic="class:read">Read</span>
```

---

### Navigation Directives

#### `dj-patch="url_or_params"`
Update URL without remounting. Triggers `handle_params`.
```html
<a dj-patch="?category=electronics&page=1">Electronics</a>
```

#### `dj-navigate="path"`
Navigate to a different LiveView over the same WebSocket.
```html
<a dj-navigate="/items/42/">View Item</a>
```

---

### Integration Directives

#### `dj-hook="HookName"`
Attach a client-side JS hook with lifecycle callbacks.
```html
<div dj-hook="Chart" data-values="{{ data|json }}">...</div>
```

#### `dj-upload="upload_name"`
Mark a file input for upload handling.
```html
<input type="file" dj-upload="avatar" accept=".jpg,.png" />
```

#### `dj-stream="stream_name"`
Mark a container for streaming updates.
```html
<div id="chat" dj-stream="messages">...</div>
```

#### `dj-transition="preset_or_name"`
Apply CSS transitions on mount/remove.
```html
<div dj-transition="fade">...</div>
<div dj-transition-enter="opacity-0" dj-transition-enter-to="opacity-100">...</div>
```

Presets: `fade`, `slide-up`, `slide-down`, `scale`.

---

## LiveForm

```python
from djust.forms import LiveForm, live_form_from_model
```

### `LiveForm(schema)`

Standalone form validation without Django Forms.

**Parameters:**
- `schema` (`dict`) — Field name → validation rules.

**Validation rules:**
| Rule | Type | Description |
|------|------|-------------|
| `required` | `bool` | Field must have a value |
| `min_length` | `int` | Minimum string length |
| `max_length` | `int` | Maximum string length |
| `pattern` | `str` | Regex pattern |
| `email` | `bool` | Must be valid email |
| `url` | `bool` | Must be valid URL |
| `min` | `number` | Minimum numeric value |
| `max` | `number` | Maximum numeric value |
| `choices` | `list` | Value must be in list |
| `validators` | `list[callable]` | Custom validators returning error string or `None` |

### Methods

#### `validate_field(field, value) → None`
Validate a single field. Updates `form.errors`.

#### `validate_all() → bool`
Validate all fields. Returns `True` if valid.

#### `set_values(data) → None`
Set multiple field values from a dict.

#### `reset() → None`
Clear all values and errors.

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `errors` | `dict` | Field name → error message |
| `data` | `dict` | Current field values |
| `valid` | `bool` | `True` if no errors |

### Example

```python
form = LiveForm({
    "name": {"required": True, "min_length": 2},
    "email": {"required": True, "email": True},
    "age": {"min": 0, "max": 150},
})

form.validate_field("email", "bad")
# form.errors == {"email": "Enter a valid email address"}

form.set_values({"name": "Jo", "email": "jo@x.com", "age": 25})
form.validate_all()  # True
```

### `live_form_from_model(model, exclude=None, include=None)`
Auto-generate a LiveForm schema from a Django model.

```python
form = live_form_from_model(Contact, exclude=["id", "created_at"])
```

---

## Testing

```python
from djust.testing import LiveViewTestClient, SnapshotTestMixin, performance_test
```

### LiveViewTestClient

#### `__init__(view_class, request_factory=None, user=None)`
Create a test client for a LiveView class.

#### `mount(**params) → LiveViewTestClient`
Mount the view with optional kwargs. Returns self for chaining.

```python
client = LiveViewTestClient(MyView)
client.mount(item_id=42)
```

#### `send_event(event_name, **params) → result`
Send an event to the mounted view.

```python
client.send_event("increment", amount=5)
```

#### `assert_state(**expected)`
Assert view attributes match expected values.

```python
client.assert_state(count=5, items=["a", "b"])
```

#### `render() → str`
Render the current view state to HTML.

### SnapshotTestMixin

Mix into `TestCase` for HTML snapshot assertions.

#### `assert_html_snapshot(name, html)`
Compare rendered HTML against a stored snapshot. Creates snapshot on first run.

### `@performance_test(max_time_ms, max_queries=None)`
Decorator ensuring a handler meets performance thresholds.

```python
@performance_test(max_time_ms=50, max_queries=3)
def test_search(self):
    client = LiveViewTestClient(SearchView)
    client.mount()
    client.send_event("search", query="test")
```

### MockUploadFile

```python
from djust.testing import MockUploadFile  # or from djust.uploads
```

Create mock file uploads for testing `UploadMixin` views.

---

## Push API

### From within a LiveView handler

```python
self.push_event("event_name", {"key": "value"})
```

### From external code (Celery, management commands, etc.)

```python
from djust.push import push_to_view, push_event_to_view
```

#### `push_to_view(view_path, *, state=None, handler=None, payload=None)`
Push state update or trigger handler on all connected clients of a view.

**Parameters:**
- `view_path` (`str`) — Dotted path (e.g. `"myapp.views.DashboardView"`).
- `state` (`dict | None`) — Attributes to set.
- `handler` (`str | None`) — Handler method to call.
- `payload` (`dict | None`) — Kwargs for handler.

```python
push_to_view("myapp.views.DashboardView", state={"alert": "Server restarting"})
```

#### `push_event_to_view(view_path, event, payload=None)`
Push a client-side event to all connected clients of a view.

```python
push_event_to_view("myapp.views.DashboardView", "refresh", {"timestamp": now})
```

---

## Routing

```python
from djust.routing import live_session
```

### `live_session(prefix, patterns, session_name=None) → list[URLPattern]`
Group LiveView URL patterns into a session sharing one WebSocket connection.

**Parameters:**
- `prefix` (`str`) — URL prefix (e.g. `"/app"`).
- `patterns` (`list[URLPattern]`) — Django URL patterns.
- `session_name` (`str | None`) — Optional session group name.

**Returns:** List of URL patterns to spread into `urlpatterns`.

```python
urlpatterns = [
    *live_session("/app", [
        path("", DashboardView.as_view(), name="dashboard"),
        path("settings/", SettingsView.as_view(), name="settings"),
        path("items/<int:id>/", ItemDetailView.as_view(), name="item-detail"),
    ]),
]
```

### Template Tag: `{% djust_route_map %}`
Emits a `<script>` tag with client-side route map for `live_redirect` resolution.

```html
{% load live_tags %}
{% djust_route_map %}
```
