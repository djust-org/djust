"""
Pytest configuration and fixtures for Django Rust Live tests.
"""

import pytest
from django.test import RequestFactory
from django.contrib.sessions.middleware import SessionMiddleware


@pytest.fixture
def request_factory():
    """Provide Django RequestFactory for creating test requests."""
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
    yield

    # Clean up the global cache
    from djust.live_view import _rust_view_cache
    _rust_view_cache.clear()
