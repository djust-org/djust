# Testing Utilities

djust provides testing utilities for testing LiveViews without a browser or WebSocket connection. Mount views, send events, and assert state changes in isolation.

## Core Concepts

- **`LiveViewTestClient`** — Test client for LiveViews
- **`ComponentTestClient`** — Test client for components
- **`MockUploadFile`** — Mock file uploads
- **`@performance_test`** — Performance assertions
- **`SnapshotTestMixin`** — Snapshot testing

## LiveViewTestClient

Test LiveViews by mounting them, sending events, and asserting state.

### Basic Usage

```python
from djust.testing import LiveViewTestClient

def test_counter_increment():
    client = LiveViewTestClient(CounterView)
    client.mount(initial_count=0)
    
    # Send event
    result = client.send_event('increment')
    assert result['success']
    
    # Assert state
    client.assert_state(count=1)
    
    # Check rendered HTML
    assert 'Count: 1' in client.html

def test_counter_multiple_increments():
    client = LiveViewTestClient(CounterView)
    client.mount()
    
    client.click('increment')
    client.click('increment')
    client.click('increment')
    
    assert client.state['count'] == 3
```

### Constructor

```python
client = LiveViewTestClient(
    view_class,      # The LiveView class to test
    request_factory=None,  # Optional Django RequestFactory
    user=None,       # Optional user for authenticated views
)
```

### Mounting

```python
# Basic mount
client.mount()

# Mount with URL kwargs
client.mount(item_id=123, mode='edit')

# Mount with custom request
from django.test import RequestFactory
request = RequestFactory().get('/items/123/')
request.user = user
client.mount(request=request, item_id=123)
```

### High-Level Interaction Methods

| Method | Description |
|--------|-------------|
| `client.click(event, value=None, **params)` | Simulate `dj-click` |
| `client.input(field, value)` | Set attribute directly |
| `client.submit(event, data=None)` | Simulate `dj-submit` |

```python
# Click handler
client.click('delete_item', id=42)

# Set input value
client.input('search_query', 'test')

# Submit form
client.submit('create_item', {'name': 'New Item', 'price': 99})
```

### State & HTML

| Property/Method | Description |
|-----------------|-------------|
| `client.state` | Dict of current view state |
| `client.html` | Rendered HTML (cached) |
| `client.get_state()` | Get state dict |
| `client.render()` | Force re-render |

```python
# Access state
assert client.state['items'] == ['a', 'b', 'c']
assert client.state['count'] == 5

# Check HTML
assert '<h1>Welcome</h1>' in client.html
assert client.has_element('button', text='Submit')
```

### Assertions

```python
# Assert exact state values
client.assert_state(count=5, name='Test')

# Assert collection contains value
client.assert_state_contains(items='new_item')

# Assert push_event was called
client.assert_push_event('notification', {'message': 'Saved!'})

# Assert redirect
client.assert_redirect('/success/')
```

### HTML Query Helpers

```python
# Check if element exists
assert client.has_element('button')
assert client.has_element('button', text='Submit')
assert client.has_element('.error-message')
assert client.has_element('#submit-btn')
assert client.has_element('input.form-control')

# Count elements
assert client.count_elements('.item') == 5
assert client.count_elements('li.completed') == 3
```

## Testing Event Handlers

```python
from djust.testing import LiveViewTestClient

class TestItemListView:
    def test_add_item(self):
        client = LiveViewTestClient(ItemListView)
        client.mount()
        
        # Initial state
        assert client.state['items'] == []
        
        # Add item
        result = client.click('add_item', name='Test Item')
        assert result['success']
        assert len(client.state['items']) == 1
        assert client.state['items'][0]['name'] == 'Test Item'
        
    def test_remove_item(self):
        client = LiveViewTestClient(ItemListView)
        client.mount()
        
        # Setup
        client.click('add_item', name='Item 1')
        client.click('add_item', name='Item 2')
        assert len(client.state['items']) == 2
        
        # Remove
        client.click('remove_item', index=0)
        assert len(client.state['items']) == 1
        assert client.state['items'][0]['name'] == 'Item 2'
        
    def test_validation_error(self):
        client = LiveViewTestClient(ItemListView)
        client.mount()
        
        # Submit invalid data
        result = client.click('add_item', name='')
        
        # Check error state
        assert 'error' in client.state
        assert 'Name is required' in client.html
```

## Testing with Authentication

```python
from django.contrib.auth.models import User
from djust.testing import LiveViewTestClient

def test_authenticated_view():
    user = User.objects.create_user('testuser', password='password')
    
    client = LiveViewTestClient(ProfileView, user=user)
    client.mount()
    
    assert client.state['username'] == 'testuser'
    assert 'Welcome, testuser' in client.html

def test_permission_denied():
    # Anonymous user
    client = LiveViewTestClient(AdminView)
    client.mount()
    
    result = client.click('admin_action')
    assert not result['success']
    assert 'Permission denied' in result['error']
```

## Testing File Uploads

```python
from djust.testing import LiveViewTestClient, MockUploadFile

def test_file_upload():
    client = LiveViewTestClient(UploadView)
    client.mount()
    
    # Create mock file
    file = MockUploadFile(
        name='test.txt',
        content=b'Hello, World!',
        content_type='text/plain'
    )
    
    # Submit upload
    client.submit('handle_upload', {'file': file})
    
    assert client.state['uploaded_filename'] == 'test.txt'
    assert 'Upload successful' in client.html
```

## Performance Testing

Use `@performance_test` to assert execution time and query count:

```python
from djust.testing import LiveViewTestClient, performance_test

class TestPerformance:
    @performance_test(max_time_ms=50, max_queries=5)
    def test_search_is_fast(self):
        client = LiveViewTestClient(SearchView)
        client.mount()
        
        client.click('search', query='test')
        
        assert len(client.state['results']) > 0

    @performance_test(max_time_ms=100, max_queries=10, track_memory=True, max_memory_bytes=1_000_000)
    def test_bulk_operation(self):
        client = LiveViewTestClient(BulkView)
        client.mount()
        
        client.click('process_all')
```

### Options

| Option | Description |
|--------|-------------|
| `max_time_ms` | Maximum execution time in milliseconds |
| `max_queries` | Maximum database queries |
| `track_memory` | Enable memory tracking |
| `max_memory_bytes` | Maximum memory allocation |

## Snapshot Testing

Compare rendered output against stored snapshots:

```python
from django.test import TestCase
from djust.testing import LiveViewTestClient, SnapshotTestMixin

class TestSnapshots(TestCase, SnapshotTestMixin):
    snapshot_dir = 'snapshots'  # Relative to test file
    
    def test_renders_correctly(self):
        client = LiveViewTestClient(ItemListView)
        client.mount()
        
        # Compare against stored snapshot
        self.assert_html_snapshot('item_list_empty', client.html)
    
    def test_with_items(self):
        client = LiveViewTestClient(ItemListView)
        client.mount()
        client.click('add_item', name='Test')
        
        self.assert_html_snapshot('item_list_one_item', client.html)
```

Update snapshots by setting `UPDATE_SNAPSHOTS=1`:

```bash
UPDATE_SNAPSHOTS=1 pytest tests/test_views.py
```

## ComponentTestClient

Test stateless components:

```python
from djust.testing import ComponentTestClient

def test_badge_component():
    client = ComponentTestClient(Badge, text='New', variant='success')
    
    assert 'New' in client.html
    assert client.has_element('span.badge-success')
    
def test_badge_update():
    client = ComponentTestClient(Badge, text='Draft', variant='warning')
    
    client.update(variant='success')
    
    assert client.has_element('span.badge-success')
    assert not client.has_element('span.badge-warning')
```

## Pytest Fixtures

djust provides pytest fixtures for convenience:

```python
# conftest.py is auto-loaded

def test_counter(live_view_client):
    client = live_view_client(CounterView)
    client.click('increment')
    assert client.state['count'] == 1

def test_badge(component_client):
    client = component_client(Badge, text='Hello')
    assert 'Hello' in client.html

def test_upload(live_view_client, mock_upload):
    client = live_view_client(UploadView)
    file = mock_upload('test.txt', b'content', 'text/plain')
    client.submit('upload', {'file': file})
```

## Helper Functions

```python
from djust.testing import create_test_view, MockRequest

# Quick view creation
view = create_test_view(CounterView, count=5)
assert view.count == 5

# Mock request for manual testing
request = MockRequest(
    user=some_user,
    session={'key': 'value'},
    get_params={'page': '1'},
)
```

## Event History

Track all events sent during a test:

```python
client = LiveViewTestClient(CounterView)
client.mount()

client.click('increment')
client.click('increment')
client.click('decrement')

history = client.get_event_history()
assert len(history) == 4  # mount + 3 events
assert history[1]['name'] == 'increment'
assert history[3]['name'] == 'decrement'
```

## Complete Test Example

```python
import pytest
from django.contrib.auth.models import User
from djust.testing import (
    LiveViewTestClient, 
    MockUploadFile,
    performance_test,
    SnapshotTestMixin
)
from myapp.views import TodoListView

class TestTodoList(SnapshotTestMixin):
    @pytest.fixture
    def user(self, db):
        return User.objects.create_user('test', password='test')
    
    @pytest.fixture
    def client(self, user):
        client = LiveViewTestClient(TodoListView, user=user)
        client.mount()
        return client
    
    def test_initial_state(self, client):
        assert client.state['todos'] == []
        assert client.has_element('.empty-state')
    
    def test_add_todo(self, client):
        client.submit('add_todo', {'title': 'Buy milk'})
        
        assert len(client.state['todos']) == 1
        assert client.state['todos'][0]['title'] == 'Buy milk'
        assert client.has_element('.todo-item', text='Buy milk')
    
    def test_complete_todo(self, client):
        client.submit('add_todo', {'title': 'Test todo'})
        
        client.click('toggle_complete', id=client.state['todos'][0]['id'])
        
        assert client.state['todos'][0]['completed'] is True
        assert client.has_element('.todo-item.completed')
    
    def test_delete_todo(self, client):
        client.submit('add_todo', {'title': 'Delete me'})
        todo_id = client.state['todos'][0]['id']
        
        client.click('delete_todo', id=todo_id)
        
        assert len(client.state['todos']) == 0
        assert client.has_element('.empty-state')
    
    @performance_test(max_time_ms=100, max_queries=5)
    def test_bulk_add_performance(self, client):
        for i in range(10):
            client.submit('add_todo', {'title': f'Todo {i}'})
        
        assert len(client.state['todos']) == 10
    
    def test_renders_correctly(self, client):
        client.submit('add_todo', {'title': 'First'})
        client.submit('add_todo', {'title': 'Second'})
        
        self.assert_html_snapshot('todo_list_two_items', client.html)
```

## API Reference

### LiveViewTestClient

| Method | Description |
|--------|-------------|
| `mount(**params)` | Initialize view |
| `send_event(name, **params)` | Low-level event send |
| `click(event, value, **params)` | Simulate dj-click |
| `input(field, value)` | Set attribute |
| `submit(event, data)` | Simulate dj-submit |
| `get_state()` | Get state dict |
| `render()` | Render HTML |
| `assert_state(**expected)` | Assert state values |
| `assert_state_contains(**expected)` | Assert contains |
| `assert_push_event(name, payload)` | Assert push_event |
| `assert_redirect(url)` | Assert redirect |
| `has_element(selector, text)` | Check element exists |
| `count_elements(selector)` | Count matching elements |
| `get_event_history()` | Get event log |

### Properties

| Property | Description |
|----------|-------------|
| `client.state` | Current view state |
| `client.html` | Rendered HTML |
| `client.push_events` | List of push_events fired |
