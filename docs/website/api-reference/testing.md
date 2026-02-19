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

#### `mount(**params) -> LiveViewTestClient`

Initialize the view. Calls `mount()` on the LiveView.

```python
client = LiveViewTestClient(MyView)
client.mount()                        # No URL params
client.mount(item_id=5)               # With URL kwargs
client.mount(page=2, query="test")    # Multiple params
```

Returns `self` for chaining.

---

#### `send_event(event_name, **kwargs) -> dict`

Send an event to the view. Calls the named event handler.

```python
result = client.send_event("increment")
result = client.send_event("search", value="laptop")
result = client.send_event("delete_item", item_id=5)
```

**Parameters:**

- `event_name` (`str`) — The handler method name
- `**kwargs` — Event parameters passed to the handler

**Returns:** Dict with the handler result.

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

#### `state -> dict`

The current view state as a dictionary. Useful for assertions not covered by `assert_state()`:

```python
state = client.state
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
        client.assert_state(user=user)
```

---

## `class SnapshotTestMixin`

Compare rendered HTML against stored snapshot files.

### Methods

#### `assert_html_snapshot(name, html, update=False)`

Compare `html` against a stored snapshot at `tests/snapshots/{name}.html`.

On first run, saves the snapshot. Subsequent runs compare against it.

**Parameters:**

- `name` (`str`) — Snapshot file name (no extension)
- `html` (`str`) — HTML to compare
- `update` (`bool`) — If `True`, overwrites the stored snapshot

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
pytest --update-snapshots
```

---

## `class LiveViewSmokeTest`

Auto-discover all `LiveView` subclasses in an app and run smoke tests.

### Class Attributes

| Attribute     | Type   | Default | Description                                     |
| ------------- | ------ | ------- | ----------------------------------------------- |
| `app_label`   | `str`  | `None`  | Only test views in this Django app              |
| `max_queries` | `int`  | `20`    | Fail if `mount()` exceeds this many DB queries  |
| `fuzz`        | `bool` | `False` | Send XSS and type-mismatch payloads to handlers |

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

---

## `@performance_test`

Decorator that enforces performance budgets on test methods.

```python
@performance_test(max_time_ms=50, max_queries=3)
```

**Parameters:**

- `max_time_ms` (`int`) — Maximum milliseconds the test body may take
- `max_queries` (`int`) — Maximum DB queries allowed

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

Fails if `search` takes more than 50ms or runs more than 3 queries.

---

## See Also

- [Testing guide](../testing/index.md) — patterns and best practices
- [LiveView API](./liveview.md)
