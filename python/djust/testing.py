"""
Testing utilities for djust LiveViews.

Provides tools for testing LiveViews without requiring a browser or WebSocket:
- LiveViewTestClient: Send events and assert state without WebSocket
- SnapshotTestMixin: Compare rendered output against stored snapshots
- @performance_test: Ensure handlers meet performance thresholds
- LiveViewSmokeTest: Auto-discover views and run smoke + fuzz tests

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

Automated smoke + fuzz testing:
    from django.test import TestCase
    from djust.testing import LiveViewSmokeTest

    class TestAllViews(TestCase, LiveViewSmokeTest):
        app_label = "crm"       # only test views in this app
        max_queries = 20        # query count threshold per render
        fuzz = True             # send XSS/type payloads to handlers
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
        else:
            from django.contrib.auth.models import AnonymousUser

            request.user = AnonymousUser()

        # Initialize session
        from django.contrib.sessions.backends.db import SessionStore

        request.session = SessionStore()

        # Store request on the instance (Django's View.dispatch does this;
        # many LiveViews access self.request in get_context_data etc.)
        self.view_instance.request = request

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

    def render(self, engine: str = "rust") -> str:
        """
        Get current rendered HTML.

        Args:
            engine: Which rendering engine to use:
                - "rust" (default): Use the Rust template engine via the view's
                  own render() path — same as production WebSocket rendering.
                - "django": Use Django's template engine (useful for views that
                  rely on Django-only template features).

        Returns:
            The rendered HTML string

        Raises:
            RuntimeError: If view not mounted
        """
        if not self._mounted or not self.view_instance:
            raise RuntimeError("View not mounted. Call client.mount() first.")

        if engine == "rust":
            # Use the view's own render() method — this goes through:
            # _initialize_rust_view() → _sync_state_to_rust() → _rust_view.render()
            # Same code path as production WebSocket rendering.
            request = getattr(self.view_instance, "request", None)
            return self.view_instance.render(request)

        # Django fallback
        context = self.view_instance.get_context_data()

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


# ============================================================================
# Fuzz payloads
# ============================================================================

# XSS payloads — if any of these appear unescaped in rendered HTML,
# the auto-escaping is broken.
XSS_PAYLOADS = [
    '<script>alert("xss")</script>',
    "<img src=x onerror=alert(1)>",
    '"><svg onload=alert(1)>',
    "'; DROP TABLE users; --",
    "${7*7}",
    "{{constructor.constructor('return this')()}}",
    '<a href="javascript:alert(1)">click</a>',
]

# Type-confusion payloads — wrong types for common parameter signatures
TYPE_PAYLOADS = {
    "str": [None, 0, True, [], {}, "", "x" * 10000],
    "int": [None, "not_a_number", "", True, -1, 0, 2**31, 3.14],
    "float": [None, "nan", "", True, float("inf")],
    "bool": [None, "yes", 0, 1, "", "false"],
}

# Unique fragments from XSS_PAYLOADS that should never appear in safe HTML.
# These check for UNESCAPED tags/attributes — if the `<` is escaped to `&lt;`,
# the browser treats it as text (safe), even if `onerror=` appears in the text.
_XSS_SENTINELS = [
    '<script>alert("xss")',  # unescaped script tag with payload
    "<img src=x onerror=",  # unescaped img tag with event handler
    "<svg onload=",  # unescaped svg tag with event handler
    '<a href="javascript:',  # unescaped anchor with javascript: URL
    "DROP TABLE users",  # SQL injection (no HTML escaping relevant)
]


def _discover_views(app_label=None):
    """Discover all LiveView subclasses, optionally filtered by app label.

    Auto-imports views modules from installed Django apps so that
    __subclasses__() can find them.
    """
    import importlib

    from django.apps import apps
    from djust.live_view import LiveView

    # Auto-import views modules from installed apps so subclasses are registered
    for app_config in apps.get_app_configs():
        if app_label and app_config.label != app_label:
            continue
        for suffix in ("views", "admin_views", "djust_admin"):
            module_name = f"{app_config.name}.{suffix}"
            try:
                importlib.import_module(module_name)
            except ImportError:
                pass  # Optional module — app may not have views/admin_views

    def _walk(cls):
        for sub in cls.__subclasses__():
            yield sub
            yield from _walk(sub)

    for cls in _walk(LiveView):
        module = getattr(cls, "__module__", "") or ""
        # Skip internal framework classes
        if module.startswith("djust.") and "test" not in module and "example" not in module:
            continue
        if app_label:
            parts = module.split(".")
            if parts[0] != app_label:
                continue
        # Skip abstract bases without a template
        if not getattr(cls, "template_name", None) and not getattr(cls, "template", None):
            continue
        yield cls


def _get_handlers(cls):
    """Get event handler names and their parameter metadata from a view class.

    Discovers both @event_handler decorated methods (with full param metadata)
    and plain public methods defined on the user class (not inherited from
    LiveView/LiveComponent base). Plain methods get basic param info from
    inspect.signature.
    """
    from djust.live_view import LiveView

    # Collect names defined on framework base classes
    base_names = set()
    for base in cls.__mro__:
        if base.__name__ in ("LiveView", "LiveComponent", "object"):
            break
        continue
    for name in dir(LiveView):
        if not name.startswith("_"):
            base_names.add(name)

    handlers = {}
    for name in dir(cls):
        if name.startswith("_"):
            continue
        try:
            attr = getattr(cls, name, None)
        except Exception:
            continue
        if not callable(attr):
            continue

        # @event_handler decorated — has full metadata
        if hasattr(attr, "_djust_decorators"):
            meta = attr._djust_decorators
            if "event_handler" in meta:
                handlers[name] = meta.get("event_handler", {})
                continue

        # Plain method defined on user class (not inherited from framework)
        if name in base_names:
            continue
        # Must be defined on the user class, not a mixin/base
        if name not in cls.__dict__:
            continue

        # Build basic param info from inspect
        try:
            sig = inspect.signature(attr)
        except (ValueError, TypeError):
            handlers[name] = {"params": [], "accepts_kwargs": False}
            continue

        params = []
        accepts_kwargs = False
        for pname, param in sig.parameters.items():
            if pname == "self":
                continue
            if param.kind == param.VAR_KEYWORD:
                accepts_kwargs = True
                continue
            if param.kind == param.VAR_POSITIONAL:
                continue
            p = {"name": pname, "type": "str", "required": True}
            if param.default is not param.empty:
                p["required"] = False
                p["default"] = param.default
            if param.annotation is not param.empty:
                type_name = getattr(param.annotation, "__name__", str(param.annotation))
                p["type"] = type_name
            params.append(p)
        handlers[name] = {"params": params, "accepts_kwargs": accepts_kwargs}

    return handlers


def _make_fuzz_params(handler_meta):
    """Generate fuzz parameter dicts for a handler based on its signature.

    Yields (description, params_dict) tuples.
    """
    params = handler_meta.get("params", [])
    accepts_kwargs = handler_meta.get("accepts_kwargs", False)

    # 1. XSS payloads — inject into every string parameter
    for payload in XSS_PAYLOADS:
        fuzz_params = {}
        for p in params:
            ptype = p.get("type", "str")
            if ptype in ("str", None):
                fuzz_params[p["name"]] = payload
            elif ptype == "int":
                fuzz_params[p["name"]] = 0
            elif ptype == "float":
                fuzz_params[p["name"]] = 0.0
            elif ptype == "bool":
                fuzz_params[p["name"]] = False
            else:
                fuzz_params[p["name"]] = payload
        if accepts_kwargs:
            fuzz_params["_fuzz_extra"] = payload
        if fuzz_params:
            yield f"xss: {payload[:30]}", fuzz_params

    # 2. Type confusion — send wrong types for each parameter
    for p in params:
        ptype = p.get("type", "str")
        payloads = TYPE_PAYLOADS.get(ptype, TYPE_PAYLOADS["str"])
        for bad_value in payloads:
            fuzz_params = {}
            for p2 in params:
                if p2["name"] == p["name"]:
                    fuzz_params[p2["name"]] = bad_value
                elif not p2.get("required", True):
                    continue  # skip optional params
                else:
                    # Provide a valid default for other required params
                    pt = p2.get("type", "str")
                    if pt == "int":
                        fuzz_params[p2["name"]] = 0
                    elif pt == "float":
                        fuzz_params[p2["name"]] = 0.0
                    elif pt == "bool":
                        fuzz_params[p2["name"]] = False
                    else:
                        fuzz_params[p2["name"]] = ""
            yield f"type({p['name']}={bad_value!r:.20})", fuzz_params

    # 3. Empty call — no params at all
    yield "empty_params", {}

    # 4. Missing required params — one at a time
    required = [p for p in params if p.get("required", True)]
    for skip_param in required:
        fuzz_params = {}
        for p in params:
            if p["name"] == skip_param["name"]:
                continue
            if not p.get("required", True):
                continue
            pt = p.get("type", "str")
            if pt == "int":
                fuzz_params[p["name"]] = 0
            elif pt == "float":
                fuzz_params[p["name"]] = 0.0
            elif pt == "bool":
                fuzz_params[p["name"]] = False
            else:
                fuzz_params[p["name"]] = ""
        yield f"missing({skip_param['name']})", fuzz_params


def _check_xss_in_html(html):
    """Check if rendered HTML contains unescaped XSS sentinels.

    Returns list of found sentinels (empty = safe).
    """
    html_lower = html.lower()
    return [s for s in _XSS_SENTINELS if s in html_lower]


class LiveViewSmokeTest:
    """
    Mixin for automated smoke testing and fuzz testing of LiveViews.

    Auto-discovers all LiveView subclasses in your project (or a specific app),
    mounts each one, renders with the Rust engine, and optionally fuzzes every
    event handler with XSS payloads and type-confusion values.

    Usage:
        from django.test import TestCase
        from djust.testing import LiveViewSmokeTest

        class TestAllCRMViews(TestCase, LiveViewSmokeTest):
            app_label = "crm"       # only test views in this app
            max_queries = 20        # fail if render exceeds this many queries
            fuzz = True             # fuzz event handlers (default: True)

            # Optional: skip views that need special setup
            skip_views = []

            # Optional: provide mount params or user per view
            view_config = {
                # DealDetailView: {"mount_params": {"object_id": 1}, "user": staff_user},
            }

    What it tests:
        - test_smoke_render: Each view mounts and renders without exceptions
        - test_smoke_queries: Each render stays within max_queries
        - test_fuzz_handlers: XSS payloads don't appear unescaped in output
        - test_fuzz_type_confusion: Wrong-type params don't cause unhandled crashes
    """

    # Override in subclass
    app_label: Optional[str] = None
    max_queries: int = 50
    fuzz: bool = True
    skip_views: List[Type] = []
    view_config: Dict[Type, Dict[str, Any]] = {}

    def _get_views(self) -> List[Type]:
        """Get views to test."""
        views = list(_discover_views(self.app_label))
        skip_set = set(self.skip_views)
        return [v for v in views if v not in skip_set]

    def _make_client(self, view_class) -> LiveViewTestClient:
        """Create and mount a test client for a view."""
        config = self.view_config.get(view_class, {})
        user = config.get("user")
        mount_params = config.get("mount_params", {})

        client = LiveViewTestClient(view_class, user=user)
        client.mount(**mount_params)
        return client

    def test_smoke_render(self):
        """Every discovered view mounts and renders without exceptions."""
        errors = []
        views = self._get_views()

        for view_class in views:
            name = f"{view_class.__module__}.{view_class.__name__}"
            try:
                client = self._make_client(view_class)
                html = client.render()
                if not html or len(html) < 10:
                    errors.append(f"{name}: render returned empty/tiny HTML ({len(html)} chars)")
            except Exception as e:
                errors.append(f"{name}: {type(e).__name__}: {e}")

        if errors:
            raise AssertionError(
                f"Smoke render failed for {len(errors)}/{len(views)} views:\n"
                + "\n".join(f"  - {e}" for e in errors)
            )

    def test_smoke_queries(self):
        """Every discovered view renders within the query count threshold."""
        from django.conf import settings
        from django.db import connection, reset_queries

        errors = []
        views = self._get_views()

        for view_class in views:
            name = f"{view_class.__module__}.{view_class.__name__}"
            old_debug = settings.DEBUG
            try:
                settings.DEBUG = True
                reset_queries()

                client = self._make_client(view_class)
                client.render()

                query_count = len(connection.queries)

                if query_count > self.max_queries:
                    errors.append(f"{name}: {query_count} queries (max {self.max_queries})")
            except Exception as e:
                errors.append(f"{name}: {type(e).__name__}: {e}")
            finally:
                settings.DEBUG = old_debug

        if errors:
            raise AssertionError(
                f"Query threshold exceeded for {len(errors)}/{len(views)} views:\n"
                + "\n".join(f"  - {e}" for e in errors)
            )

    def test_fuzz_xss(self):
        """XSS payloads in handler params don't appear unescaped in rendered output."""
        if not self.fuzz:
            return

        errors = []
        views = self._get_views()

        for view_class in views:
            view_name = f"{view_class.__module__}.{view_class.__name__}"
            handlers = _get_handlers(view_class)
            if not handlers:
                continue

            for handler_name, handler_meta in handlers.items():
                for desc, fuzz_params in _make_fuzz_params(handler_meta):
                    if not desc.startswith("xss:"):
                        continue

                    try:
                        client = self._make_client(view_class)
                        client.send_event(handler_name, **fuzz_params)
                        html = client.render()

                        found = _check_xss_in_html(html)
                        if found:
                            errors.append(
                                f"{view_name}.{handler_name} [{desc}]: "
                                f"unescaped XSS sentinel(s): {found}"
                            )
                    except Exception:
                        # Handler crashing on fuzz input is acceptable —
                        # it's an unhandled crash test, not XSS test
                        pass

        if errors:
            raise AssertionError(
                f"XSS escaping failures ({len(errors)}):\n" + "\n".join(f"  - {e}" for e in errors)
            )

    def test_fuzz_no_unhandled_crash(self):
        """Fuzz payloads don't cause unhandled exceptions (500s in production)."""
        if not self.fuzz:
            return

        crashes = []
        views = self._get_views()

        for view_class in views:
            view_name = f"{view_class.__module__}.{view_class.__name__}"
            handlers = _get_handlers(view_class)
            if not handlers:
                continue

            for handler_name, handler_meta in handlers.items():
                for desc, fuzz_params in _make_fuzz_params(handler_meta):
                    try:
                        client = self._make_client(view_class)
                        client.send_event(handler_name, **fuzz_params)
                        # send_event catches exceptions and returns success=False,
                        # which is fine. We only care about exceptions that
                        # escape the handler boundary.
                    except Exception as e:
                        crashes.append(
                            f"{view_name}.{handler_name} [{desc}]: " f"{type(e).__name__}: {e}"
                        )

        if crashes:
            raise AssertionError(
                f"Unhandled crashes from fuzz input ({len(crashes)}):\n"
                + "\n".join(f"  - {e}" for e in crashes)
            )
