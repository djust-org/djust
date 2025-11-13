"""
Pytest configuration and fixtures for Django Rust Live tests.
"""

import sys
from pathlib import Path
import pytest
from django.test import RequestFactory
from django.contrib.sessions.middleware import SessionMiddleware

# Add demo_project to Python path for Django settings
demo_project_path = Path(__file__).parent.parent / "examples" / "demo_project"
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
    request = request_factory.get('/')

    # Add session support
    middleware = SessionMiddleware(lambda x: None)
    middleware.process_request(request)
    request.session.save()

    return request


@pytest.fixture
def post_request(request_factory):
    """Create a basic POST request with session support."""
    request = request_factory.post('/', content_type='application/json')

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
def cleanup_session_cache():
    """Clean up session cache after each test."""
    # Setup: Ensure we use in-memory backend for tests
    from djust.state_backend import set_backend, InMemoryStateBackend

    backend = InMemoryStateBackend()
    set_backend(backend)

    yield

    # Cleanup: Clear all sessions from backend
    backend.cleanup_expired(ttl=0)  # TTL=0 expires everything
