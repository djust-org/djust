"""
Tests for djust testing utilities.

Tests cover:
- LiveViewTestClient mounting and events
- State assertions
- SnapshotTestMixin
- @performance_test decorator
"""

import os
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

import pytest

# Setup Django settings before importing djust modules
import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=True,
        DATABASES={
            'default': {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': ':memory:',
            }
        },
        INSTALLED_APPS=[
            'django.contrib.contenttypes',
            'django.contrib.auth',
            'django.contrib.sessions',
        ],
        TEMPLATES=[{
            'BACKEND': 'django.template.backends.django.DjangoTemplates',
            'DIRS': [],
            'APP_DIRS': True,
        }],
        SECRET_KEY='test-secret-key-for-testing-only',
        DEFAULT_AUTO_FIELD='django.db.models.BigAutoField',
    )
    django.setup()

from djust.testing import (
    LiveViewTestClient,
    SnapshotTestMixin,
    performance_test,
    MockRequest,
    create_test_view,
)


# Mock LiveView for testing
class MockLiveView:
    """Mock LiveView class for testing the test utilities."""

    template_name = 'mock_template.html'

    def __init__(self):
        self.count = 0
        self.items = []
        self.message = ""
        self._private_var = "hidden"

    def _initialize_temporary_assigns(self):
        """Mock initialization method."""
        pass

    def mount(self, request, **kwargs):
        """Mock mount method."""
        self.count = kwargs.get('initial_count', 0)
        self.items = kwargs.get('initial_items', [])

    def increment(self):
        """Increment counter."""
        self.count += 1

    def decrement(self):
        """Decrement counter."""
        self.count -= 1

    def add_item(self, item: str):
        """Add an item to the list."""
        self.items.append(item)

    def set_message(self, message: str = ""):
        """Set a message."""
        self.message = message

    def get_context_data(self):
        """Get template context."""
        return {
            'count': self.count,
            'items': self.items,
            'message': self.message,
        }


class MockSlowView:
    """Mock view with slow handler for performance testing."""

    template_name = 'mock_template.html'

    def __init__(self):
        self.result = None

    def _initialize_temporary_assigns(self):
        pass

    def mount(self, request, **kwargs):
        pass

    def slow_handler(self):
        """Intentionally slow handler."""
        import time
        time.sleep(0.2)  # 200ms
        self.result = "done"

    def get_context_data(self):
        return {'result': self.result}


class TestLiveViewTestClient:
    """Test LiveViewTestClient functionality."""

    def test_mount_creates_view_instance(self):
        """Test that mount() creates a view instance."""
        client = LiveViewTestClient(MockLiveView)
        client.mount()

        assert client.view_instance is not None
        assert isinstance(client.view_instance, MockLiveView)

    def test_mount_with_params(self):
        """Test that mount() passes params to view.mount()."""
        client = LiveViewTestClient(MockLiveView)
        client.mount(initial_count=10, initial_items=['a', 'b'])

        assert client.view_instance.count == 10
        assert client.view_instance.items == ['a', 'b']

    def test_mount_returns_self_for_chaining(self):
        """Test that mount() returns self for method chaining."""
        client = LiveViewTestClient(MockLiveView)
        result = client.mount()

        assert result is client

    def test_send_event_calls_handler(self):
        """Test that send_event() calls the handler."""
        client = LiveViewTestClient(MockLiveView)
        client.mount()

        result = client.send_event('increment')

        assert result['success'] is True
        assert client.view_instance.count == 1

    def test_send_event_with_params(self):
        """Test that send_event() passes params to handler."""
        client = LiveViewTestClient(MockLiveView)
        client.mount()

        result = client.send_event('add_item', item='test_item')

        assert result['success'] is True
        assert 'test_item' in client.view_instance.items

    def test_send_event_coerces_types(self):
        """Test that send_event() coerces string params to expected types."""
        client = LiveViewTestClient(MockLiveView)
        client.mount()

        # String should be passed as-is for str param
        result = client.send_event('set_message', message='hello')

        assert result['success'] is True
        assert client.view_instance.message == 'hello'

    def test_send_event_returns_state_snapshots(self):
        """Test that send_event() returns before/after state."""
        client = LiveViewTestClient(MockLiveView)
        client.mount(initial_count=5)

        result = client.send_event('increment')

        assert result['state_before']['count'] == 5
        assert result['state_after']['count'] == 6

    def test_send_event_returns_duration(self):
        """Test that send_event() returns execution duration."""
        client = LiveViewTestClient(MockLiveView)
        client.mount()

        result = client.send_event('increment')

        assert 'duration_ms' in result
        assert result['duration_ms'] >= 0

    def test_send_event_unknown_handler(self):
        """Test that send_event() handles unknown handlers."""
        client = LiveViewTestClient(MockLiveView)
        client.mount()

        result = client.send_event('nonexistent_handler')

        assert result['success'] is False
        assert 'No handler found' in result['error']

    def test_send_event_without_mount_raises(self):
        """Test that send_event() raises if view not mounted."""
        client = LiveViewTestClient(MockLiveView)

        with pytest.raises(RuntimeError, match="View not mounted"):
            client.send_event('increment')

    def test_get_state_returns_public_vars(self):
        """Test that get_state() returns only public variables."""
        client = LiveViewTestClient(MockLiveView)
        client.mount(initial_count=5)

        state = client.get_state()

        assert 'count' in state
        assert state['count'] == 5
        assert '_private_var' not in state

    def test_get_state_excludes_methods(self):
        """Test that get_state() excludes methods."""
        client = LiveViewTestClient(MockLiveView)
        client.mount()

        state = client.get_state()

        assert 'increment' not in state
        assert 'mount' not in state

    def test_assert_state_passes_on_match(self):
        """Test that assert_state() passes when values match."""
        client = LiveViewTestClient(MockLiveView)
        client.mount(initial_count=5)

        # Should not raise
        client.assert_state(count=5)

    def test_assert_state_fails_on_mismatch(self):
        """Test that assert_state() fails when values don't match."""
        client = LiveViewTestClient(MockLiveView)
        client.mount(initial_count=5)

        with pytest.raises(AssertionError, match="expected 10"):
            client.assert_state(count=10)

    def test_assert_state_fails_on_missing_var(self):
        """Test that assert_state() fails when var doesn't exist."""
        client = LiveViewTestClient(MockLiveView)
        client.mount()

        with pytest.raises(AssertionError, match="not found"):
            client.assert_state(nonexistent_var='value')

    def test_assert_state_contains(self):
        """Test that assert_state_contains() checks for containment."""
        client = LiveViewTestClient(MockLiveView)
        client.mount(initial_items=['a', 'b', 'c'])

        client.assert_state_contains(items='b')

    def test_assert_state_contains_fails(self):
        """Test that assert_state_contains() fails when not contained."""
        client = LiveViewTestClient(MockLiveView)
        client.mount(initial_items=['a', 'b', 'c'])

        with pytest.raises(AssertionError, match="expected to contain"):
            client.assert_state_contains(items='z')

    def test_get_event_history(self):
        """Test that get_event_history() returns all events."""
        client = LiveViewTestClient(MockLiveView)
        client.mount()
        client.send_event('increment')
        client.send_event('increment')

        history = client.get_event_history()

        assert len(history) == 3  # mount + 2 increments
        assert history[0]['type'] == 'mount'
        assert history[1]['type'] == 'event'
        assert history[1]['name'] == 'increment'


class TestSnapshotTestMixin(SnapshotTestMixin):
    """Test SnapshotTestMixin functionality."""

    def setup_method(self, method):
        """Create a temporary directory for snapshots (pytest compatible)."""
        self.temp_dir = tempfile.mkdtemp()
        self.original_snapshot_dir = self.snapshot_dir
        self.snapshot_dir = self.temp_dir

    def teardown_method(self, method):
        """Clean up temporary directory (pytest compatible)."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        self.snapshot_dir = self.original_snapshot_dir

    def _get_snapshot_path(self, name: str) -> Path:
        """Override to use temp directory."""
        return Path(self.temp_dir) / f"{name}.snapshot"

    def test_assert_snapshot_creates_new(self):
        """Test that assert_snapshot creates new snapshot file."""
        self.assert_snapshot('new_snapshot', 'test content')

        snapshot_path = self._get_snapshot_path('new_snapshot')
        assert snapshot_path.exists()
        assert snapshot_path.read_text() == 'test content'

    def test_assert_snapshot_matches_existing(self):
        """Test that assert_snapshot passes when content matches."""
        # Create existing snapshot
        snapshot_path = self._get_snapshot_path('existing')
        snapshot_path.write_text('expected content')

        # Should not raise
        self.assert_snapshot('existing', 'expected content')

    def test_assert_snapshot_fails_on_mismatch(self):
        """Test that assert_snapshot fails when content differs."""
        # Create existing snapshot
        snapshot_path = self._get_snapshot_path('mismatch')
        snapshot_path.write_text('expected content')

        with pytest.raises(AssertionError, match="doesn't match"):
            self.assert_snapshot('mismatch', 'different content')

    def test_assert_html_snapshot_normalizes(self):
        """Test that assert_html_snapshot normalizes whitespace."""
        # Test that excessive whitespace is collapsed
        html = '<div>    \n\n    <span>hello</span>    \n</div>'
        normalized = self._normalize_html(html)

        # Whitespace should be collapsed and tags separated by newlines
        assert '    ' not in normalized  # No excessive whitespace
        assert '\n\n' not in normalized  # No double newlines
        assert 'hello' in normalized  # Content preserved

    def test_assert_html_snapshot_removes_comments(self):
        """Test that HTML comments are removed."""
        html = '<div><!-- comment --><span>text</span></div>'
        normalized = self._normalize_html(html)

        assert '<!-- comment -->' not in normalized
        assert 'text' in normalized


class TestPerformanceDecorator:
    """Test @performance_test decorator."""

    def test_performance_test_passes_under_threshold(self):
        """Test that fast operations pass."""
        @performance_test(max_time_ms=1000, max_queries=100)
        def fast_test():
            return 42

        result = fast_test()
        assert result == 42

    def test_performance_test_fails_over_time_threshold(self):
        """Test that slow operations fail."""
        @performance_test(max_time_ms=10, max_queries=100)
        def slow_test():
            import time
            time.sleep(0.1)  # 100ms
            return 42

        with pytest.raises(AssertionError, match="Execution time"):
            slow_test()

    def test_performance_test_preserves_return_value(self):
        """Test that decorator preserves function return value."""
        @performance_test(max_time_ms=1000, max_queries=100)
        def returns_value():
            return {'key': 'value'}

        result = returns_value()
        assert result == {'key': 'value'}


class TestMockRequest:
    """Test MockRequest utility."""

    def test_mock_request_defaults(self):
        """Test MockRequest default values."""
        request = MockRequest()

        assert request.path == '/'
        assert request.method == 'GET'
        assert request.GET == {}
        assert request.POST == {}

    def test_mock_request_with_user(self):
        """Test MockRequest with custom user."""
        mock_user = MagicMock()
        mock_user.username = 'testuser'

        request = MockRequest(user=mock_user)

        assert request.user.username == 'testuser'

    def test_mock_request_with_params(self):
        """Test MockRequest with GET/POST params."""
        request = MockRequest(
            get_params={'q': 'search'},
            post_params={'data': 'value'},
        )

        assert request.GET['q'] == 'search'
        assert request.POST['data'] == 'value'


class TestCreateTestView:
    """Test create_test_view helper."""

    def test_create_test_view_returns_mounted_instance(self):
        """Test that create_test_view returns a mounted view."""
        view = create_test_view(MockLiveView, initial_count=5)

        assert view is not None
        assert view.count == 5

    def test_create_test_view_with_user(self):
        """Test create_test_view with authenticated user."""
        mock_user = MagicMock()
        view = create_test_view(MockLiveView, user=mock_user)

        assert view is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
