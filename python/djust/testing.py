"""
Testing utilities for djust LiveViews.

Provides tools for testing LiveViews without requiring a browser or WebSocket:
- LiveViewTestClient: Send events and assert state without WebSocket
- SnapshotTestMixin: Compare rendered output against stored snapshots
- @performance_test: Ensure handlers meet performance thresholds

Example usage:
    from djust.testing import LiveViewTestClient, SnapshotTestMixin, performance_test

    class TestCounterView(TestCase, SnapshotTestMixin):
        def test_increment(self):
            client = LiveViewTestClient(CounterView)
            client.mount()

            client.send_event('increment')
            client.assert_state(count=1)

            client.send_event('increment')
            client.assert_state(count=2)

        def test_renders_correctly(self):
            client = LiveViewTestClient(CounterView)
            client.mount(count=5)

            self.assert_html_snapshot('counter_5', client.render())

        @performance_test(max_time_ms=50, max_queries=3)
        def test_fast_handler(self):
            client = LiveViewTestClient(ItemListView)
            client.mount()
            client.send_event('search', query='test')
"""

import functools
import inspect
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Type

from django.test import RequestFactory


class LiveViewTestClient:
    """
    Test LiveViews without a browser or WebSocket connection.

    Provides a simple API for mounting views, sending events, and asserting
    state changes without the complexity of a full WebSocket test setup.

    Usage:
        client = LiveViewTestClient(MyLiveView)
        client.mount(initial_param='value')

        result = client.send_event('my_handler', param1='value')

        client.assert_state(expected_var=expected_value)
        html = client.render()
    """

    def __init__(
        self,
        view_class: Type,
        request_factory: Optional[RequestFactory] = None,
        user: Optional[Any] = None,
    ):
        """
        Initialize the test client.

        Args:
            view_class: The LiveView class to test
            request_factory: Optional Django RequestFactory (creates one if not provided)
            user: Optional user to attach to requests (for authenticated views)
        """
        self.view_class = view_class
        self.request_factory = request_factory or RequestFactory()
        self.user = user
        self.view_instance: Optional[Any] = None
        self.events: List[Dict[str, Any]] = []
        self.patches: List[Any] = []
        self._mounted = False

    def mount(self, **params: Any) -> "LiveViewTestClient":
        """
        Initialize the view with optional params.

        This simulates the WebSocket mount process, calling the view's mount()
        method with a mock request.

        Args:
            **params: Parameters to pass to mount() (like URL kwargs)

        Returns:
            self for method chaining

        Example:
            client.mount(item_id=123, mode='edit')
        """
        # Create the view instance
        self.view_instance = self.view_class()

        # Create a mock request
        request = self.request_factory.get("/")
        if self.user:
            request.user = self.user

        # Initialize session
        from django.contrib.sessions.backends.db import SessionStore

        request.session = SessionStore()

        # Initialize temporary assigns if the method exists
        if hasattr(self.view_instance, "_initialize_temporary_assigns"):
            self.view_instance._initialize_temporary_assigns()

        # Call mount
        self.view_instance.mount(request, **params)

        self._mounted = True
        self.events.append(
            {
                "type": "mount",
                "params": params,
                "timestamp": time.time(),
            }
        )

        return self

    def send_event(self, event_name: str, **params: Any) -> Dict[str, Any]:
        """
        Send an event and return the result.

        This calls the event handler method directly, similar to how the
        WebSocket consumer would call it.

        Args:
            event_name: Name of the event handler method
            **params: Parameters to pass to the handler

        Returns:
            Dict with:
                - 'success': bool
                - 'error': Optional error message
                - 'state_before': State snapshot before event
                - 'state_after': State snapshot after event
                - 'duration_ms': Handler execution time

        Raises:
            RuntimeError: If view not mounted

        Example:
            result = client.send_event('search', query='test', page=1)
            assert result['success']
        """
        if not self._mounted or not self.view_instance:
            raise RuntimeError("View not mounted. Call client.mount() first.")

        # Capture state before
        state_before = self.get_state()

        # Get the handler
        handler = getattr(self.view_instance, event_name, None)
        if not handler or not callable(handler):
            return {
                "success": False,
                "error": f"No handler found for event: {event_name}",
                "state_before": state_before,
                "state_after": state_before,
                "duration_ms": 0,
            }

        # Apply type coercion if available
        from .validation import validate_handler_params

        validation = validate_handler_params(handler, params, event_name)
        if not validation["valid"]:
            return {
                "success": False,
                "error": validation["error"],
                "state_before": state_before,
                "state_after": state_before,
                "duration_ms": 0,
            }

        coerced_params = validation["coerced_params"]

        # Execute handler
        start_time = time.perf_counter()
        error = None
        try:
            if coerced_params:
                handler(**coerced_params)
            else:
                handler()
        except Exception as e:
            error = str(e)

        duration_ms = (time.perf_counter() - start_time) * 1000

        # Capture state after
        state_after = self.get_state()

        # Record event
        event_record = {
            "type": "event",
            "name": event_name,
            "params": params,
            "coerced_params": coerced_params,
            "timestamp": time.time(),
            "duration_ms": duration_ms,
            "error": error,
        }
        self.events.append(event_record)

        return {
            "success": error is None,
            "error": error,
            "state_before": state_before,
            "state_after": state_after,
            "duration_ms": duration_ms,
        }

    def get_state(self) -> Dict[str, Any]:
        """
        Get current view state (public variables).

        Returns only instance variables that don't start with underscore,
        similar to what would be available in the template context.

        Returns:
            Dict of variable names to values
        """
        if not self.view_instance:
            return {}

        state = {}
        for name in dir(self.view_instance):
            # Skip private/magic attributes
            if name.startswith("_"):
                continue

            # Skip methods and properties from the class
            cls_attr = getattr(type(self.view_instance), name, None)
            if callable(cls_attr) or isinstance(cls_attr, property):
                continue

            try:
                value = getattr(self.view_instance, name)
                # Skip callables (methods)
                if callable(value):
                    continue
                state[name] = value
            except AttributeError:
                # Property may raise AttributeError if dependencies aren't set
                pass

        return state

    def render(self) -> str:
        """
        Get current rendered HTML.

        Returns:
            The rendered HTML string

        Raises:
            RuntimeError: If view not mounted
        """
        if not self._mounted or not self.view_instance:
            raise RuntimeError("View not mounted. Call client.mount() first.")

        # Get context data
        context = self.view_instance.get_context_data()

        # Get template
        from django.template.loader import get_template

        template_name = getattr(self.view_instance, "template_name", None)
        if not template_name:
            raise RuntimeError(f"View {self.view_class.__name__} has no template_name")

        template = get_template(template_name)
        return template.render(context)

    def assert_state(self, **expected: Any) -> None:
        """
        Assert state variables match expected values.

        Args:
            **expected: Expected variable names and values

        Raises:
            AssertionError: If any value doesn't match

        Example:
            client.assert_state(count=5, items=['a', 'b', 'c'])
        """
        actual_state = self.get_state()

        for name, expected_value in expected.items():
            if name not in actual_state:
                raise AssertionError(
                    f"State variable '{name}' not found. " f"Available: {list(actual_state.keys())}"
                )

            actual_value = actual_state[name]
            if actual_value != expected_value:
                raise AssertionError(
                    f"State variable '{name}': expected {expected_value!r}, "
                    f"got {actual_value!r}"
                )

    def assert_state_contains(self, **expected: Any) -> None:
        """
        Assert state variables contain expected values (for collections).

        Args:
            **expected: Variable names and values that should be contained

        Raises:
            AssertionError: If any value is not contained

        Example:
            client.assert_state_contains(items='new_item')
        """
        actual_state = self.get_state()

        for name, expected_value in expected.items():
            if name not in actual_state:
                raise AssertionError(
                    f"State variable '{name}' not found. " f"Available: {list(actual_state.keys())}"
                )

            actual_value = actual_state[name]
            if expected_value not in actual_value:
                raise AssertionError(
                    f"State variable '{name}': expected to contain {expected_value!r}, "
                    f"got {actual_value!r}"
                )

    def get_event_history(self) -> List[Dict[str, Any]]:
        """
        Get the list of events that were sent during this test.

        Returns:
            List of event records with type, name, params, timing, etc.
        """
        return self.events.copy()


class SnapshotTestMixin:
    """
    Mixin for snapshot testing rendered output.

    Stores snapshots in a 'snapshots' directory relative to the test file.
    Set update_snapshots=True or use --update-snapshots pytest flag to
    update stored snapshots.

    Usage:
        class TestMyView(TestCase, SnapshotTestMixin):
            snapshot_dir = 'snapshots'  # Default

            def test_renders_correctly(self):
                html = render_view()
                self.assert_html_snapshot('my_view_default', html)
    """

    snapshot_dir: str = "snapshots"
    update_snapshots: bool = False

    def _get_snapshot_path(self, name: str) -> Path:
        """Get the path to a snapshot file."""
        # Get the test file's directory
        test_file = inspect.getfile(self.__class__)
        test_dir = Path(test_file).parent

        # Create snapshots directory if needed
        snapshot_path = test_dir / self.snapshot_dir
        snapshot_path.mkdir(parents=True, exist_ok=True)

        return snapshot_path / f"{name}.snapshot"

    def assert_snapshot(self, name: str, content: str):
        """
        Compare content against stored snapshot.

        Args:
            name: Unique name for this snapshot
            content: Content to compare/store

        Raises:
            AssertionError: If content doesn't match stored snapshot
        """
        snapshot_path = self._get_snapshot_path(name)

        # Check if we should update
        should_update = self.update_snapshots or os.environ.get("UPDATE_SNAPSHOTS", "").lower() in (
            "1",
            "true",
        )

        if should_update or not snapshot_path.exists():
            # Write new snapshot
            snapshot_path.write_text(content, encoding="utf-8")
            if not should_update:
                # First time creating - just pass
                return
        else:
            # Compare with existing
            expected = snapshot_path.read_text(encoding="utf-8")
            if content != expected:
                # Generate diff-like output
                raise AssertionError(
                    f"Snapshot '{name}' doesn't match.\n"
                    f"Expected ({len(expected)} chars):\n{expected[:500]}{'...' if len(expected) > 500 else ''}\n\n"
                    f"Got ({len(content)} chars):\n{content[:500]}{'...' if len(content) > 500 else ''}\n\n"
                    f"Run with UPDATE_SNAPSHOTS=1 to update."
                )

    def assert_html_snapshot(self, name: str, html: str):
        """
        Compare HTML with normalization (whitespace, etc.).

        Normalizes HTML before comparison:
        - Collapses multiple whitespace to single space
        - Removes leading/trailing whitespace per line
        - Removes HTML comments

        Args:
            name: Unique name for this snapshot
            html: HTML content to compare/store
        """
        normalized = self._normalize_html(html)
        self.assert_snapshot(f"{name}.html", normalized)

    def _normalize_html(self, html: str) -> str:
        """Normalize HTML for consistent comparison."""
        # Remove HTML comments
        html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)

        # Collapse whitespace
        html = re.sub(r"\s+", " ", html)

        # Clean up around tags
        html = re.sub(r">\s+<", ">\n<", html)

        # Strip lines
        lines = [line.strip() for line in html.split("\n")]
        return "\n".join(line for line in lines if line)


def performance_test(
    max_time_ms: float = 100,
    max_queries: int = 10,
    track_memory: bool = False,
    max_memory_bytes: Optional[int] = None,
):
    """
    Decorator for performance testing event handlers.

    Fails the test if execution time or query count exceeds thresholds.

    Args:
        max_time_ms: Maximum execution time in milliseconds
        max_queries: Maximum number of database queries
        track_memory: Whether to track memory usage (slower)
        max_memory_bytes: Maximum memory allocation in bytes (requires track_memory=True)

    Usage:
        @performance_test(max_time_ms=50, max_queries=5)
        def test_fast_search(self):
            client = LiveViewTestClient(SearchView)
            client.mount()
            result = client.send_event('search', query='test')
            assert result['success']

    Note:
        Query tracking requires Django's database connection to be configured.
        Memory tracking requires the `tracemalloc` module.
    """

    def decorator(test_func: Callable) -> Callable:
        @functools.wraps(test_func)
        def wrapper(*args, **kwargs):
            from django.db import connection, reset_queries
            from django.conf import settings

            # Enable query logging temporarily
            old_debug = settings.DEBUG
            settings.DEBUG = True
            reset_queries()

            # Track memory if requested
            memory_before = None
            if track_memory:
                import tracemalloc

                tracemalloc.start()
                memory_before = tracemalloc.get_traced_memory()[0]

            # Run the test
            start_time = time.perf_counter()
            try:
                result = test_func(*args, **kwargs)
            finally:
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                query_count = len(connection.queries)

                # Track memory
                memory_used = None
                if track_memory:
                    import tracemalloc

                    memory_after = tracemalloc.get_traced_memory()[0]
                    memory_used = memory_after - memory_before
                    tracemalloc.stop()

                # Restore settings
                settings.DEBUG = old_debug

            # Check thresholds
            errors = []

            if elapsed_ms > max_time_ms:
                errors.append(f"Execution time {elapsed_ms:.2f}ms exceeded max {max_time_ms}ms")

            if query_count > max_queries:
                # Include query details for debugging
                query_summary = []
                for q in connection.queries[:5]:
                    sql = q.get("sql", "")[:80]
                    query_summary.append(f"  - {sql}...")
                errors.append(
                    f"Query count {query_count} exceeded max {max_queries}.\n"
                    f"First {min(5, query_count)} queries:\n" + "\n".join(query_summary)
                )

            if max_memory_bytes is not None and memory_used is not None:
                if memory_used > max_memory_bytes:
                    errors.append(
                        f"Memory usage {memory_used:,} bytes exceeded max {max_memory_bytes:,} bytes"
                    )

            if errors:
                raise AssertionError("Performance test failed:\n" + "\n".join(errors))

            return result

        return wrapper

    return decorator


class MockRequest:
    """
    Simple mock request for testing LiveViews.

    Provides the minimum interface needed by most LiveViews:
    - user (AnonymousUser by default)
    - session (empty dict by default)
    - GET, POST dicts
    - path, method attributes
    """

    def __init__(
        self,
        user: Optional[Any] = None,
        session: Optional[Dict] = None,
        get_params: Optional[Dict] = None,
        post_params: Optional[Dict] = None,
        path: str = "/",
    ):
        from django.contrib.auth.models import AnonymousUser

        self.user = user or AnonymousUser()
        self.session = session or {}
        self.GET = get_params or {}
        self.POST = post_params or {}
        self.path = path
        self.method = "GET"


def create_test_view(view_class: Type, user: Optional[Any] = None, **mount_params: Any) -> Any:
    """
    Helper to quickly create and mount a view for testing.

    Args:
        view_class: The LiveView class
        user: Optional user for authenticated views
        **mount_params: Parameters to pass to mount()

    Returns:
        The mounted view instance

    Example:
        view = create_test_view(CounterView, count=5)
        assert view.count == 5
    """
    client = LiveViewTestClient(view_class, user=user)
    client.mount(**mount_params)
    return client.view_instance
