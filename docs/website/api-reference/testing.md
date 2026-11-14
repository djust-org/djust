# Testing API Reference

```python
from djust.testing import LiveViewTestClient, SnapshotTestMixin, LiveViewSmokeTest, performance_test
```

---

## `class LiveViewTestClient`

Test LiveViews without a browser or WebSocket connection.

```python
LiveViewTestClient(view_class, request_factory=None, user=None)
```

**Parameters:**

- `view_class` — The `LiveView` subclass to test
- `request_factory` — Optional Django `RequestFactory`. Creates one if not provided.
- `user` — Optional authenticated user to attach to requests.

### Methods

#### `mount(via_websocket=True, *, query_params=None, route_kwargs=None, **params) -> LiveViewTestClient`

Initialize the view. Calls `mount()` on the LiveView, then `handle_params(params, uri)`,
the order the HTTP GET and the socket mount run them in, with an empty dict when there is
no query string. A view that declares `params_form` therefore gets its form here too. The
default `via_websocket=True` mounts as the WebSocket path does; pass
`via_websocket=False` to exercise the HTTP-prerender branch instead.

Before `mount()` it runs the pre-mount sequence every transport runs
(`login_required`, `permission_required`, `check_permissions()` and the tenant
resolve), then the view's `on_mount` hooks with the mount kwargs: a user the view
refuses raises `PermissionDenied`, and an anonymous user on a `login_required` view, or
a hook that returns a URL, raises `djust.testing.MountRedirect` (its `url` is that URL;
the HTTP GET answers a 302 there and the socket a `navigate` frame). In both cases
`mount()` does not run. An `async def check_permissions()` is awaited for you from a
sync test; from an async test, call `await sync_to_async(client.mount)()`. Pass `user=` to mount as a user the view admits. After `mount()`
and `handle_params()` the object-permission check every transport runs follows: a view
whose `get_object()` / `has_object_permission()` denies raises `PermissionDenied` (a
transport answers 403 or a `permission_denied` frame).

```python
client = LiveViewTestClient(MyView)
client.mount()                        # No URL params
client.mount(item_id=5)               # With URL kwargs
client.mount(page=2, query="test")    # Multiple params
client.mount(query_params={"page": "2", "tag": ["a", "b"]})  # the query string
client.mount(query_params="page=2&tag=a&tag=b")                  # the same, as text
```

`query_params` is the URL's query string (`request.GET` and `handle_params()` see it); the
other keyword arguments are `mount()`'s URL kwargs. A mapping's list value is a repeated
key. `route_kwargs` are the kwargs the view's URL route resolves to: a view that looks its
object up from the route ([`ModelFormMixin`](../guides/forms.md#editing-one-record-with-modelformmixin))
reads them from there and never from `mount()`'s kwargs, so mount a routed edit view with
`client.mount(route_kwargs={"pk": article.pk})`; without them its object check denies it.
They are bound before `mount()` and not passed to it. Returns `self` for chaining.

---

#### `url_change(query_params=None) -> LiveViewTestClient`

Send the `url_change` frame a `live_patch` or a back/forward produces: calls
`handle_params(params, uri)` on the mounted view with the new query string, through the same
`params_form` decision as the real transports. Raises `RuntimeError` if the view is not
mounted. Returns `self` for chaining.

---

#### `send_event(event_name, raise_on_missing=True, **params) -> dict`

Send an event to the view. Calls the named event handler.

```python
result = client.send_event("increment")
result = client.send_event("search", value="laptop")
result = client.send_event("delete_item", item_id=5)
```

**Parameters:**

- `event_name` (`str`) — The handler method name
- `raise_on_missing` (`bool`) — If `True` (default), raise `NoHandlerFoundError` when no handler exists for `event_name`, and `EventRejectedError` (a subclass) when the transports refuse the event before any handler runs: a method without `@event_handler`, a server-push or server-only method, a private name. Pass `False` to get a `{"success": False, ...}` result instead (`code="event_rejected"` for a refusal, with the transports' answer in `error` and the reason in `detail`).
- `**params` — Event parameters passed to the handler

**Returns:** a dict with the keys `success`, `error`, `state_before`, `state_after` and `duration_ms`. The handler's own return value is discarded.

An exception raised inside the handler is captured and reported as `success=False` with the message in `error`; it is not raised. Assert `result["success"]` so a crashing handler fails your test:

```python
result = client.send_event("search", value="laptop")
assert result["success"], result["error"]
```

`send_event` runs the same authorization gates as the WebSocket consumer before it calls the handler. A handler with `@permission_required` is refused when the mounted user lacks the permission, and a view that overrides `get_object` has `has_object_permission` re-checked on every event. A refused event does not run the handler. It returns `success=False` with `code="permission_denied"`, so a test of a gated handler fails if the gate is removed:

```python
client = LiveViewTestClient(BoardView, user=reader)  # lacks app.add_decision
client.mount()
result = client.send_event("decide", verdict="go")
assert result["code"] == "permission_denied"
```

`@rate_limit` is not applied.

---

#### `assert_state(**expected)`

Assert that the view's current state matches the expected values.

```python
client.assert_state(count=3)
client.assert_state(is_valid=True, success_message="Saved!")
```

Raises `AssertionError` if any attribute doesn't match.

---

#### `render() -> str`

Render the current state to HTML and return it.

```python
html = client.render()
assert "laptop" in html
assert "<li>" in html
```

---

#### `get_state() -> dict`

The current view state as a dictionary: the declared `state()` fields and the public attributes the view's own code sets (in `mount()`, a handler) or defines on its own classes. Names the framework defines are not the view's state and are left out: `request`, the attributes `LiveView.__init__` sets, and class attributes of djust's classes such as `template` or `login_required`, even when your code sets one (`page_title`, a `FormMixin` attribute); assert on those through `render()` or the attribute itself. `state_before` and `state_after` in a `send_event()` result are this dictionary. Useful for assertions not covered by `assert_state()`:

```python
state = client.get_state()
assert "email" in state["field_errors"]
assert len(state["items"]) == 3
```

---

### Example: Full Test

```python
from django.test import TestCase
from djust.testing import LiveViewTestClient
from myapp.views import CounterView


class TestCounterView(TestCase):
    def test_initial_state(self):
        client = LiveViewTestClient(CounterView)
        client.mount()
        client.assert_state(count=0)

    def test_increment(self):
        client = LiveViewTestClient(CounterView)
        client.mount()

        client.send_event("increment")
        client.assert_state(count=1)

        client.send_event("increment")
        client.assert_state(count=2)

    def test_renders_count(self):
        client = LiveViewTestClient(CounterView)
        client.mount()
        client.send_event("increment")

        html = client.render()
        self.assertIn("1", html)

    def test_authenticated(self):
        from django.contrib.auth.models import User
        user = User.objects.create_user("test", password="pass")
        client = LiveViewTestClient(ProtectedView, user=user)
        client.mount()
        # `user` is not view state; check the request the view was mounted with
        self.assertEqual(client.view_instance.request.user, user)
```

---

## `class SnapshotTestMixin`

Compare rendered HTML against stored snapshot files.

### Methods

#### `assert_html_snapshot(name, html)`

Normalize `html` (whitespace, comments) and compare it against a stored snapshot at `<test file's directory>/snapshots/{name}.html.snapshot`.

On first run, saves the snapshot. Subsequent runs compare against it.

**Parameters:**

- `name` (`str`) — Snapshot name (no extension)
- `html` (`str`) — HTML to compare

**Class attributes:**

- `snapshot_dir` (`str`, default `"snapshots"`) — directory, relative to the test file, that holds snapshots
- `update_snapshots` (`bool`, default `False`) — if `True`, overwrite stored snapshots instead of comparing

```python
from django.test import TestCase
from djust.testing import LiveViewTestClient, SnapshotTestMixin
from myapp.views import ProductCardView


class TestProductCard(TestCase, SnapshotTestMixin):
    def test_renders_product(self):
        client = LiveViewTestClient(ProductCardView)
        client.mount(product_id=1)

        self.assert_html_snapshot("product_card_default", client.render())
```

To update all snapshots after an intentional UI change:

```bash
UPDATE_SNAPSHOTS=1 pytest
```

---

## `class LiveViewSmokeTest`

Auto-discover all `LiveView` subclasses in an app and run smoke tests.

### Class Attributes

| Attribute     | Type   | Default | Description                                                                 |
| ------------- | ------ | ------- | --------------------------------------------------------------------------- |
| `app_label`   | `str`  | `None`  | Only test views in this Django app                                          |
| `max_queries` | `int`  | `50`    | Fail if mounting + rendering a view exceeds this many DB queries (a view's own `query_budget` or `view_config` `max_queries` takes precedence) |
| `fuzz`        | `bool` | `True`  | Send XSS and type-mismatch payloads to handlers                             |
| `skip_views`  | `list` | `[]`    | View classes to skip                                                        |
| `view_config` | `dict` | `{}`    | Per-view setup: `{ViewClass: {"mount_params": {...}, "route_kwargs": {"pk": 1}, "user": user, "max_queries": 8, "event_params": {"handler": {...}}}}` |

### Example

```python
from django.test import TestCase
from djust.testing import LiveViewSmokeTest


class TestAllMyAppViews(TestCase, LiveViewSmokeTest):
    app_label = "myapp"
    max_queries = 15
    fuzz = True
```

This auto-discovers all `LiveView` subclasses in `myapp` and:

1. Mounts each view
2. Asserts it renders without raising
3. Checks DB query counts stay under each view's budget: `view_config[View]["max_queries"]`, else the view's [`query_budget`](liveview.md#class-attributes), else `max_queries`; `test_smoke_event_queries` checks the event budgets (`query_budget["event"]` / `["events"]`) of the handlers that declare one
4. If `fuzz=True`, sends malformed payloads and asserts no uncaught exceptions

The fuzzer sends events to every `@event_handler` handler, which is everything a
client event can call.

---

## `@performance_test`

Decorator that enforces performance budgets on test methods.

```python
@performance_test(max_time_ms=50, max_queries=3)
def test_search_page_renders():
    client = LiveViewTestClient(SearchView)
    client.mount()
    assert "Search" in client.render()
```

**Parameters:**

- `max_time_ms` (`float`, default `100`) — Maximum milliseconds the whole test method may take
- `max_queries` (`int`, default `10`) — Maximum DB queries allowed across the whole test method
- `track_memory` (`bool`, default `False`) — Track memory allocated during the test with `tracemalloc`
- `max_memory_bytes` (`int`, optional) — Maximum bytes allocated when `track_memory=True`

```python
from django.test import TestCase
from djust.testing import LiveViewTestClient, performance_test


class TestSearchPerformance(TestCase):
    @performance_test(max_time_ms=50, max_queries=3)
    def test_search_is_fast(self):
        client = LiveViewTestClient(SearchView)
        client.mount()
        client.send_event("search", value="laptop")
```

Fails if the whole test method — including client setup and `mount()`, not just `search` — takes more than 50 ms or runs more than 3 queries.

---

## Rendering through the Rust engine

### `render_template_string(source, context=None) -> str`

Renders a template string through the engine a LiveView renders through (no `dj-id` attributes, no VDOM). Component tags (`{% call %}`, `{% slot %}`) are available:

```python
from djust.testing import render_template_string

assert render_template_string("{{ n|floatformat:2 }}", {"n": 1.5}) == "1.50"
```

### `render_component(name, content="", **assigns) -> str`

Renders a registered function component the way `{% call "name" ... %}{% endcall %}` does on a page, with its assigns validated. Raises `LookupError` when no component has that name:

```python
from djust.testing import render_component

html = render_component("status_badge", status="done")
```

To look up the registered function itself, use `djust.get_function_component(name)` (returns `None` when missing).

## View inventory: `djust.introspection`

Lists the project's views and the authorization each declares, as `manage.py djust_audit` reads them:

```python
from djust.introspection import live_views, view_auth_info

def test_every_view_declares_access():
    for view in live_views(app_label="crm"):
        assert view_auth_info(view), view.__qualname__
```

- `live_views(app_label=None)` / `live_components(app_label=None)`: the project's `LiveView` / `LiveComponent` subclasses (framework classes left out), sorted by dotted path. A view's module must already be imported.
- `view_auth_info(cls)`: a dict with `login_required`, `permission_required`, `permission_required_any` (one list per `any_of` group), `custom_check` (a `check_permissions` override) or `dispatch_mixin`, each present only when it applies. `{}` means the class declares nothing. Authorization added outside the class (URL decorators, middleware) is not seen.
- `walk_subclasses(cls)`, `is_user_class(cls)`, `app_label_for_class(cls)`: the building blocks.

## See Also

- [Testing guide](../testing/index.md) — patterns and best practices
- [LiveView API](./liveview.md)
