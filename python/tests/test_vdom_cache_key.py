"""
Test for VDOM cache key with path and query string support.

Ensures that different URLs get different VDOM caches, preventing
render corruption when navigating between views with different
template structures (e.g., /emails/ vs /emails/?sender=1).
"""

import pytest
from djust import LiveView
from django.test import RequestFactory
from django.contrib.sessions.middleware import SessionMiddleware


class InboxView(LiveView):
    """
    View that can render in two different structural modes:
    - Grouped mode (default): shows sender groups with nested emails
    - Flat mode (when sender_id is set): shows flat list of emails from one sender
    """

    template = """<div data-djust-root data-djust-view="InboxView">
    <main class="content">
        {% if filter_mode == 'sender' %}
        <!-- FLAT MODE: Single sender's emails -->
        <div class="sender-filter-header">
            <div class="sender-name">{{ sender_name }}</div>
            <button @click="clear_filter">Back to Inbox</button>
        </div>
        <div class="email-list-flat">
            {% for email in emails %}
            <div class="email-item">{{ email.subject }}</div>
            {% endfor %}
        </div>
        {% else %}
        <!-- GROUPED MODE: Emails grouped by sender -->
        <div class="sender-groups">
            {% for sender in senders %}
            <div class="sender-group">
                <span class="sender-name">{{ sender.name }}</span>
                <a href="?sender={{ sender.id }}">View all</a>
            </div>
            {% endfor %}
        </div>
        {% endif %}
    </main>
</div>"""

    def mount(self, request, sender_id=None):
        if sender_id is None and hasattr(request, "GET"):
            sender_id = request.GET.get("sender")
            if sender_id:
                sender_id = int(sender_id)
        self.sender_id = sender_id
        if sender_id:
            self.filter_mode = "sender"
            self.sender_name = "John Doe"
            self.emails = [{"subject": "Email 1"}, {"subject": "Email 2"}]
            self.senders = []
        else:
            self.filter_mode = "grouped"
            self.sender_name = ""
            self.emails = []
            self.senders = [
                {"id": 1, "name": "John Doe"},
                {"id": 2, "name": "Alice Smith"},
            ]


def add_session_to_request(request):
    """Helper to add session to request"""
    middleware = SessionMiddleware(lambda x: None)
    middleware.process_request(request)
    request.session.save()
    return request


@pytest.mark.django_db
def test_cache_key_includes_class_name_and_path():
    """
    Test that the cache key includes both class name and path.
    """
    factory = RequestFactory()

    view = InboxView()
    request = factory.get("/emails/")
    request = add_session_to_request(request)

    # Simulate WebSocket session with path info
    view._websocket_session_id = "test-session-123"
    view._websocket_path = "/emails/"
    view._websocket_query_string = ""

    # Initialize the Rust view
    view._rust_view = None
    view._cache_key = None
    view._initialize_rust_view(request)

    cache_key = view._cache_key

    # Cache key should include view class name AND path
    assert "InboxView" in cache_key, f"Cache key should include class name: {cache_key}"
    assert "/emails/" in cache_key, f"Cache key should include path: {cache_key}"


@pytest.mark.django_db
def test_cache_key_differs_for_different_query_params():
    """
    Test that URLs with different query params get different cache keys.

    This ensures /emails/ and /emails/?sender=1 have separate VDOM caches,
    preventing render corruption when switching between grouped and flat views.
    """
    factory = RequestFactory()

    # View 1: No query params (grouped mode)
    view1 = InboxView()
    request1 = factory.get("/emails/")
    request1 = add_session_to_request(request1)
    view1._websocket_session_id = "test-session-123"
    view1._websocket_path = "/emails/"
    view1._websocket_query_string = ""
    view1._rust_view = None
    view1._cache_key = None
    view1._initialize_rust_view(request1)
    cache_key1 = view1._cache_key

    # View 2: With sender query param (flat mode)
    view2 = InboxView()
    request2 = factory.get("/emails/?sender=1")
    request2 = add_session_to_request(request2)
    view2._websocket_session_id = "test-session-123"  # Same session!
    view2._websocket_path = "/emails/"
    view2._websocket_query_string = "sender=1"
    view2._rust_view = None
    view2._cache_key = None
    view2._initialize_rust_view(request2)
    cache_key2 = view2._cache_key

    # View 3: With different sender query param
    view3 = InboxView()
    request3 = factory.get("/emails/?sender=2")
    request3 = add_session_to_request(request3)
    view3._websocket_session_id = "test-session-123"  # Same session!
    view3._websocket_path = "/emails/"
    view3._websocket_query_string = "sender=2"
    view3._rust_view = None
    view3._cache_key = None
    view3._initialize_rust_view(request3)
    cache_key3 = view3._cache_key

    # All three should have different cache keys
    assert cache_key1 != cache_key2, (
        f"Grouped and flat views should have different cache keys: " f"{cache_key1} vs {cache_key2}"
    )
    assert cache_key2 != cache_key3, (
        f"Different sender filters should have different cache keys: "
        f"{cache_key2} vs {cache_key3}"
    )


@pytest.mark.django_db
def test_cache_key_query_param_order_independent():
    """
    Test that query param ordering doesn't affect cache key.

    ?a=1&b=2 and ?b=2&a=1 should produce the same cache key.
    """
    factory = RequestFactory()

    # View with params in one order
    view1 = InboxView()
    request1 = factory.get("/emails/?a=1&b=2")
    request1 = add_session_to_request(request1)
    view1._websocket_session_id = "test-session-123"
    view1._websocket_path = "/emails/"
    view1._websocket_query_string = "a=1&b=2"
    view1._rust_view = None
    view1._cache_key = None
    view1._initialize_rust_view(request1)
    cache_key1 = view1._cache_key

    # View with params in reverse order
    view2 = InboxView()
    request2 = factory.get("/emails/?b=2&a=1")
    request2 = add_session_to_request(request2)
    view2._websocket_session_id = "test-session-123"
    view2._websocket_path = "/emails/"
    view2._websocket_query_string = "b=2&a=1"
    view2._rust_view = None
    view2._cache_key = None
    view2._initialize_rust_view(request2)
    cache_key2 = view2._cache_key

    # Same params in different order should produce same cache key
    assert cache_key1 == cache_key2, (
        f"Query param order should not affect cache key: " f"{cache_key1} vs {cache_key2}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
