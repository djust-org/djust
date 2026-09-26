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

#### `mount(via_websocket=True, **params) -> LiveViewTestClient`

Initialize the view. Calls `mount()` on the LiveView. The default
`via_websocket=True` mounts as the WebSocket path does; pass
`via_websocket=False` to exercise the HTTP-prerender branch instead. No
login or permission check runs here.

```python
client = LiveViewTestClient(MyView)
client.mount()                        # No URL params
client.mount(item_id=5)               # With URL kwargs
client.mount(page=2, query="test")    # Multiple params
```

Returns `self` for chaining.

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
- `raise_on_missing` (`bool`) — If `True` (default), raise `NoHandlerFoundError` when no handler exists for `event_name`. Pass `False` to get a `{"success": False, ...}` result instead.
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

`mount()` does not check the view-level `login_required` or `permission_required`, and `@rate_limit` is not applied.

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

The current view state as a dictionary. Useful for assertions not covered by `assert_state()`:

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
| `max_queries` | `int`  | `50`    | Fail if mounting + rendering a view exceeds this many DB queries            |
| `fuzz`        | `bool` | `True`  | Send XSS and type-mismatch payloads to handlers                             |
| `skip_views`  | `list` | `[]`    | View classes to skip                                                        |
| `view_config` | `dict` | `{}`    | Per-view setup: `{ViewClass: {"mount_params": {...}, "user": user}}`        |

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
3. Checks DB query counts stay under `max_queries`
4. If `fuzz=True`, sends malformed payloads and asserts no uncaught exceptions

The fuzzer sends events only to `@event_handler` handlers, the ones dispatch
resolves in every `event_security` mode. Under `"warn"` or `"open"` a client can
still call an undecorated public method, so the smoke test emits one
`UserWarning` per view naming those methods: decorate them, or test them directly.

---

## `@performance_test`

Decorator that enforces performance budgets on test methods.

```python
@performance_test(max_time_ms=50, max_queries=3)
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

## See Also

- [Testing guide](../testing/index.md) — patterns and best practices
- [LiveView API](./liveview.md)
