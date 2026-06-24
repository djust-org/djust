"""
Pytest configuration and fixtures for Django Rust Live tests.
"""

import sys
from pathlib import Path
import pytest
from django.test import RequestFactory
from django.contrib.sessions.middleware import SessionMiddleware

# Add demo_project to Python path for Django settings.
# Use .resolve() so symlinks (e.g. Dropbox-backed worktrees) are resolved to the
# real path before inserting into sys.path, making the fixture portable across
# different invocation directories.
demo_project_path = Path(__file__).resolve().parent.parent / "examples" / "demo_project"
if str(demo_project_path) not in sys.path:
    sys.path.insert(0, str(demo_project_path))


@pytest.fixture
def request_factory():
    """Provide Django RequestFactory for creating test requests."""
    return RequestFactory()


@pytest.fixture
def rf():
    """Alias for request_factory (pytest-django convention)."""
    return RequestFactory()


@pytest.fixture
def get_request(request_factory):
    """Create a basic GET request with session support."""
    request = request_factory.get("/")

    # Add session support
    middleware = SessionMiddleware(lambda x: None)
    middleware.process_request(request)
    request.session.save()

    return request


@pytest.fixture
def post_request(request_factory):
    """Create a basic POST request with session support."""
    request = request_factory.post("/", content_type="application/json")

    # Add session support
    middleware = SessionMiddleware(lambda x: None)
    middleware.process_request(request)
    request.session.save()

    return request


@pytest.fixture
def sample_template():
    """Provide a sample template for testing."""
    return """
    <div class="container">
        <h1>Hello {{ name }}!</h1>
        <p>Count: {{ count }}</p>
    </div>
    """


@pytest.fixture(autouse=True)
def _reset_djust_globals():
    """Reset djust process-global mutable state BEFORE each test (#1883).

    Systemic cure for the shared-process-global flaky-test class: one autouse
    fixture clears djust's leak-prone process-globals (the Channels layer
    manager, Django's URLconf caches, djust's route-map cache, and the
    module-level id counters) so every test in an xdist worker starts from a
    clean slate. Retires the whack-a-mole class that produced #1862 (PR #1874),
    #1875 (PR #1881), and #1882 — see ``djust.test_isolation`` for the full
    inventory and the conservative-inclusion rationale.

    Pre-yield (resets before the test runs) so tests that set up their own
    global state in their body still work.
    """
    from djust.test_isolation import reset_djust_globals

    reset_djust_globals()
    yield


@pytest.fixture(autouse=True)
def cleanup_session_cache():
    """Clean up session cache after each test."""
    # Setup: Ensure we use in-memory backend for tests
    from djust.state_backend import set_backend, InMemoryStateBackend

    backend = InMemoryStateBackend()
    set_backend(backend)

    yield

    # Cleanup: Clear all sessions from backend unconditionally.
    # delete_all() has unambiguous semantics; cleanup_expired(ttl=0) previously
    # meant "expire everything" but that changed in #395.
    backend.delete_all()
