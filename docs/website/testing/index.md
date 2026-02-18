# Testing

djust provides a testing toolkit that lets you test LiveViews without a browser or WebSocket connection.

## Running Tests

```bash
make test              # All tests (Python + Rust + JavaScript)
make test-python       # Python only
make test-rust         # Rust only

pytest python/                              # All Python tests
pytest python/djust/tests/test_foo.py      # Single file
pytest -k "test_increment"                  # By name pattern
pytest -x                                  # Stop on first failure
pytest --lf                                # Re-run last failures
```

## LiveViewTestClient

The primary tool for unit-testing LiveViews. Send events and assert state changes without a server:

```python
from django.test import TestCase
from djust.testing import LiveViewTestClient
from myapp.views import CounterView


class TestCounterView(TestCase):
    def test_increment(self):
        client = LiveViewTestClient(CounterView)
        client.mount()

        client.send_event("increment")
        client.assert_state(count=1)

        client.send_event("increment")
        client.assert_state(count=2)

    def test_decrement_does_not_go_negative(self):
        client = LiveViewTestClient(CounterView)
        client.mount()

        client.send_event("decrement")
        client.assert_state(count=0)  # Assuming count is floored at 0
```

### Testing with Authentication

```python
from django.contrib.auth.models import User

class TestProtectedView(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("testuser", password="pass")

    def test_requires_login(self):
        client = LiveViewTestClient(MyProtectedView)
        # Without user — should redirect
        with self.assertRaises(PermissionError):
            client.mount()

    def test_authenticated(self):
        client = LiveViewTestClient(MyProtectedView, user=self.user)
        client.mount()
        client.assert_state(user=self.user)
```

### Testing with URL Parameters

```python
def test_item_detail(self):
    item = Item.objects.create(name="Test Item")
    client = LiveViewTestClient(ItemDetailView)
    client.mount(item_id=item.pk)
    client.assert_state(item=item)
```

### Asserting Rendered HTML

```python
def test_renders_item_list(self):
    client = LiveViewTestClient(ItemListView)
    client.mount()
    client.send_event("search", value="laptop")

    html = client.render()
    self.assertIn("laptop", html)
    self.assertNotIn("phone", html)
```

### Sending Events with Data

```python
def test_delete_item(self):
    client = LiveViewTestClient(ItemListView)
    client.mount()

    # Simulate dj-click="delete" data-item-id="5"
    client.send_event("delete_item", item_id=5)
    client.assert_state(items=[])  # or whatever the expected state is
```

## SnapshotTestMixin

Compare rendered HTML against stored snapshots. Useful for regression testing UI changes:

```python
from django.test import TestCase
from djust.testing import LiveViewTestClient, SnapshotTestMixin
from myapp.views import ProductCardView


class TestProductCard(TestCase, SnapshotTestMixin):
    def test_renders_product(self):
        client = LiveViewTestClient(ProductCardView)
        client.mount(product_id=1)

        self.assert_html_snapshot("product_card_1", client.render())
```

On first run, the snapshot is saved to `tests/snapshots/product_card_1.html`. Subsequent runs compare against it. To update snapshots after an intentional change:

```bash
pytest --update-snapshots
```

## LiveViewSmokeTest

Auto-discover all LiveViews in an app and run basic smoke tests — verifies they mount without crashing and don't hit excessive queries:

```python
from django.test import TestCase
from djust.testing import LiveViewSmokeTest


class TestAllViews(TestCase, LiveViewSmokeTest):
    app_label = "myapp"    # Only test views in this app
    max_queries = 20       # Fail if mount() exceeds this many DB queries
    fuzz = True            # Send XSS/type-mismatch payloads to handlers
```

`LiveViewSmokeTest` auto-discovers all `LiveView` subclasses in `app_label` and:
1. Mounts each view
2. Asserts it renders without raising an exception
3. Checks DB query counts stay under `max_queries`
4. If `fuzz=True`, sends malformed event payloads to handlers and asserts no 500 errors

## Performance Testing

Ensure handlers meet response-time and query-count budgets:

```python
from djust.testing import LiveViewTestClient, performance_test
from django.test import TestCase


class TestSearchPerformance(TestCase):
    @performance_test(max_time_ms=50, max_queries=3)
    def test_search_is_fast(self):
        client = LiveViewTestClient(SearchView)
        client.mount()
        client.send_event("search", value="test")
```

The test fails if `search` takes more than 50ms or executes more than 3 queries.

## Testing Forms

```python
from django.test import TestCase
from djust.testing import LiveViewTestClient
from myapp.views import ContactFormView


class TestContactForm(TestCase):
    def test_valid_submission(self):
        client = LiveViewTestClient(ContactFormView)
        client.mount()

        client.send_event("submit_form", name="Alice", email="alice@example.com", message="Hi")
        client.assert_state(is_valid=True)
        self.assertIn("success_message", client.state)

    def test_invalid_email(self):
        client = LiveViewTestClient(ContactFormView)
        client.mount()

        client.send_event("submit_form", name="Bob", email="not-an-email", message="Hi")
        client.assert_state(is_valid=False)
        # Field errors should be set
        state = client.state
        self.assertIn("email", state["field_errors"])

    def test_realtime_validation(self):
        client = LiveViewTestClient(ContactFormView)
        client.mount()

        # Simulate dj-change="validate_field" on email field
        client.send_event("validate_field", field_name="email", value="invalid")
        state = client.state
        self.assertIn("email", state["field_errors"])
```

## JavaScript Tests

JavaScript tests live in `tests/js/` and run with Node.js:

```bash
make test-js
# or
cd tests/js && npm test
```

Each JS feature file in `static/djust/src/` should have a corresponding test in `tests/js/`. See [JavaScript Testing](../../testing/TESTING_JAVASCRIPT.md) for details.

## pytest Configuration

Recommended `pyproject.toml` settings:

```toml
[tool.pytest.ini_options]
DJANGO_SETTINGS_MODULE = "myproject.settings"
python_files = ["test_*.py", "*_test.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
markers = [
    "slow: marks tests as slow (deselect with '-m not slow')",
    "asyncio: marks async tests",
]
```

## Test File Conventions

- Tests live in `tests/` at the project root or in `myapp/tests/`
- One test file per view or module: `test_counter_view.py`
- Use `@pytest.mark.django_db` for tests that need database access
- Use `TestCase` subclasses for tests that need transactions

## Best Practices

- Test `mount()` separately from event handlers
- Test one event per test method for clarity
- Use `client.assert_state()` to check specific state without inspecting all of it
- Use `client.render()` only when you need to assert HTML content
- For DB-heavy views, check query counts with `django.test.utils.override_settings` or `assertNumQueries`
