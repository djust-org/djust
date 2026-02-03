# Testing

djust provides testing utilities to test LiveViews without a browser or WebSocket connection.

## LiveViewTestClient

Test LiveViews by simulating events and asserting state:

```python
from djust.testing import LiveViewTestClient

def test_counter_increments():
    client = LiveViewTestClient(CounterView)
    client.mount()
    
    client.click("increment")
    assert client.state["count"] == 1
    
    client.click("increment")
    assert client.state["count"] == 2
```

### Setup

```python
from django.test import TestCase
from djust.testing import LiveViewTestClient

class TestMyView(TestCase):
    def test_basic(self):
        client = LiveViewTestClient(MyView)
        client.mount(some_param="value")
        # ... test logic
```

### Mounting

```python
# Simple mount
client.mount()

# Mount with URL kwargs
client.mount(item_id=123, mode="edit")

# Mount with authenticated user
from django.contrib.auth.models import User
user = User.objects.create_user("testuser")
client = LiveViewTestClient(MyView, user=user)
client.mount()
```

### Sending Events

```python
# Click events
client.click("handler_name")
client.click("delete", id=123)

# Input events
client.input("search_query", "test")

# Submit events
client.submit("save_form", {"name": "John", "email": "john@example.com"})

# Low-level event
result = client.send_event("custom_event", param1="value")
```

### State Assertions

```python
# Assert specific values
client.assert_state(count=5, name="John")

# Check state contains value
client.assert_state_contains(items="new_item")

# Direct state access
assert client.state["count"] == 5
assert "item" in client.state["items"]
```

### HTML Assertions

```python
# Check rendered HTML contains text
assert "Welcome" in client.html

# Check element exists
assert client.has_element("button", text="Submit")
assert client.has_element(".error-message")
assert client.has_element("#user-list")

# Count elements
assert client.count_elements(".item") == 5
assert client.count_elements("tr") > 0
```

### Push Event Assertions

```python
# Assert push_event was called
client.assert_push_event("flash")
client.assert_push_event("flash", {"message": "Saved!", "type": "success"})

# Access all push events
events = client.push_events
assert len(events) == 2
```

### Event Result

`send_event` returns a result dict:

```python
result = client.send_event("my_handler", param="value")

assert result["success"] == True
assert result["error"] is None
assert result["duration_ms"] < 100
assert result["state_before"] != result["state_after"]
```

## Testing Forms

```python
from djust.testing import LiveViewTestClient

def test_form_validation():
    client = LiveViewTestClient(ContactFormView)
    client.mount()
    
    # Submit empty form
    client.submit("submit_form", {})
    
    # Check for errors
    assert "email" in client.state["form"].errors
    assert client.has_element(".error", text="required")
    
    # Submit valid form
    client.submit("submit_form", {
        "name": "John",
        "email": "john@example.com",
        "message": "Hello!",
    })
    
    assert client.state["form"].valid
    assert client.state["success"] == True
```

## Testing Uploads

```python
from djust.testing import LiveViewTestClient, MockUploadFile

def test_file_upload():
    client = LiveViewTestClient(UploadView)
    client.mount()
    
    # Create mock file
    file = MockUploadFile(
        "test.jpg",
        b"\xff\xd8\xff\xe0...",  # JPEG bytes
        "image/jpeg"
    )
    
    # Simulate upload and processing
    client.submit("process_upload", file=file)
    
    assert client.state["upload_complete"]
    assert client.has_element(".success", text="Uploaded")
```

## ComponentTestClient

Test components in isolation:

```python
from djust.testing import ComponentTestClient

def test_badge_component():
    client = ComponentTestClient(
        Badge,
        text="New",
        variant="success"
    )
    
    assert "New" in client.html
    assert client.has_element("span.badge-success")

def test_badge_update():
    client = ComponentTestClient(Badge, text="Old", variant="info")
    
    client.update(text="New", variant="warning")
    
    assert "New" in client.html
    assert client.has_element(".badge-warning")
```

## Snapshot Testing

Compare rendered output against stored snapshots:

```python
from django.test import TestCase
from djust.testing import LiveViewTestClient, SnapshotTestMixin

class TestViews(TestCase, SnapshotTestMixin):
    def test_dashboard_renders_correctly(self):
        client = LiveViewTestClient(DashboardView)
        client.mount()
        
        # Compare against stored snapshot
        self.assert_html_snapshot("dashboard_default", client.html)
    
    def test_with_data(self):
        client = LiveViewTestClient(ItemListView)
        client.mount()
        
        # Snapshot after loading data
        client.send_event("load_items")
        self.assert_html_snapshot("item_list_loaded", client.html)
```

Update snapshots:
```bash
UPDATE_SNAPSHOTS=1 pytest tests/test_views.py
```

## Performance Testing

Ensure handlers meet performance requirements:

```python
from djust.testing import LiveViewTestClient, performance_test

class TestPerformance(TestCase):
    @performance_test(max_time_ms=50, max_queries=5)
    def test_fast_search(self):
        client = LiveViewTestClient(SearchView)
        client.mount()
        
        client.send_event("search", query="test")
        
        # Test fails if > 50ms or > 5 queries

    @performance_test(max_time_ms=100, max_queries=10, track_memory=True, max_memory_bytes=1_000_000)
    def test_memory_efficient(self):
        client = LiveViewTestClient(LargeListView)
        client.mount()
        
        client.send_event("load_all")
```

## Pytest Fixtures

Use the built-in fixtures for cleaner tests:

```python
# conftest.py - fixtures are auto-registered

def test_counter(live_view_client):
    client = live_view_client(CounterView)
    client.click("increment")
    assert client.state["count"] == 1

def test_badge(component_client):
    client = component_client(Badge, text="Hi", variant="info")
    assert "Hi" in client.html

def test_upload(live_view_client, mock_upload):
    client = live_view_client(UploadView)
    file = mock_upload("test.txt", b"hello", "text/plain")
    client.submit("upload", file=file)
```

## Async Handler Testing

Test async handlers with pytest-asyncio:

```python
import pytest
from djust.testing import LiveViewTestClient

@pytest.mark.asyncio
async def test_async_handler():
    client = LiveViewTestClient(AsyncView)
    client.mount()
    
    # Async handlers work the same way
    result = client.send_event("async_fetch")
    
    assert result["success"]
    assert client.state["data"] is not None
```

## Integration Testing with Playwright

For end-to-end testing with a real browser:

```python
# tests/e2e/test_counter.py
import pytest
from playwright.sync_api import Page

def test_counter_increments(page: Page, live_server):
    page.goto(f"{live_server.url}/counter/")
    
    # Click increment button
    page.click("button:has-text('+')")
    
    # Assert count updated
    assert page.locator("h1").inner_text() == "Count: 1"
    
    # Click again
    page.click("button:has-text('+')")
    assert page.locator("h1").inner_text() == "Count: 2"
```

## Testing Patterns

### Test State Transitions

```python
def test_wizard_flow():
    client = LiveViewTestClient(WizardView)
    client.mount()
    
    assert client.state["step"] == 1
    
    client.click("next")
    assert client.state["step"] == 2
    
    client.click("back")
    assert client.state["step"] == 1
```

### Test Validation

```python
def test_validation_errors():
    client = LiveViewTestClient(FormView)
    client.mount()
    
    client.submit("submit", {"email": "invalid"})
    
    assert not client.state["form"].valid
    assert "email" in client.state["form"].errors
    assert client.has_element(".error", text="valid email")
```

### Test Authorization

```python
def test_requires_auth():
    # Anonymous user
    client = LiveViewTestClient(ProtectedView)
    client.mount()
    
    assert client.state.get("redirect") == "/login/"
    
    # Authenticated user
    user = User.objects.create_user("test")
    client = LiveViewTestClient(ProtectedView, user=user)
    client.mount()
    
    assert "redirect" not in client.state
```

### Test Loading States

```python
def test_loading_state():
    client = LiveViewTestClient(AsyncView)
    client.mount()
    
    # Before load
    assert client.state["loading"] == False
    assert client.state["data"] is None
    
    # After load
    client.click("load_data")
    
    assert client.state["loading"] == False
    assert client.state["data"] is not None
```

## Best Practices

1. **Test behavior, not implementation**: Focus on what the user sees and does.

2. **Use fixtures**: The pytest fixtures reduce boilerplate.

3. **Isolate tests**: Each test should mount a fresh client.

4. **Test error cases**: Ensure errors are handled gracefully.

5. **Performance testing**: Add `@performance_test` for critical handlers.

6. **Snapshot testing**: Use for complex rendered output.

7. **Run tests in CI**: Include djust tests in your CI pipeline.
