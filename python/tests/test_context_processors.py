"""
Test for LiveView context processor support.

Ensures that Django context processors are applied to LiveView templates,
making variables like GOOGLE_ANALYTICS_ID, user, messages, etc. available.
"""

import pytest
from unittest.mock import patch, MagicMock
from djust import LiveView
from django.test import RequestFactory, override_settings
from django.contrib.sessions.middleware import SessionMiddleware


class AnalyticsView(LiveView):
    """Test view that uses context processor variables."""

    template = """<div data-liveview-root>
    {% if GOOGLE_ANALYTICS_ID %}
    <script>gtag('config', '{{ GOOGLE_ANALYTICS_ID }}')</script>
    {% endif %}
    <p>Count: {{ count }}</p>
</div>"""

    def mount(self, request, **kwargs):
        self.count = 0


def dummy_analytics_processor(request):
    """Test context processor that provides GOOGLE_ANALYTICS_ID."""
    return {'GOOGLE_ANALYTICS_ID': 'G-TEST12345'}


def dummy_user_processor(request):
    """Test context processor that provides user info."""
    return {'current_user_name': 'TestUser'}


@pytest.fixture
def request_factory():
    return RequestFactory()


@pytest.fixture
def request_with_session(request_factory):
    """Create a request with session support."""
    request = request_factory.get('/')
    middleware = SessionMiddleware(lambda r: None)
    middleware.process_request(request)
    request.session.save()
    return request


TEMPLATES_WITH_CONTEXT_PROCESSORS = [
    {
        'BACKEND': 'djust.template_backend.DjustTemplateBackend',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'tests.test_context_processors.dummy_analytics_processor',
                'tests.test_context_processors.dummy_user_processor',
            ],
        },
    },
]


class TestContextProcessors:
    """Test context processor support in LiveView."""

    @override_settings(TEMPLATES=TEMPLATES_WITH_CONTEXT_PROCESSORS)
    def test_context_processors_applied_in_get_context(self, request_with_session):
        """Test that context processors are applied when building context."""
        view = AnalyticsView()
        view.setup(request_with_session)
        view._initialize_temporary_assigns()
        view.mount(request_with_session)

        # Get context and apply context processors
        context = view.get_context_data()
        context = view._apply_context_processors(context, request_with_session)

        # Verify context processor variables are present
        assert 'GOOGLE_ANALYTICS_ID' in context
        assert context['GOOGLE_ANALYTICS_ID'] == 'G-TEST12345'
        assert 'current_user_name' in context
        assert context['current_user_name'] == 'TestUser'

        # Verify view's own context is still present
        assert 'count' in context
        assert context['count'] == 0

    @override_settings(TEMPLATES=TEMPLATES_WITH_CONTEXT_PROCESSORS)
    def test_context_processors_not_applied_without_request(self, request_with_session):
        """Test that context processors are skipped when request is None."""
        view = AnalyticsView()
        view.setup(request_with_session)
        view._initialize_temporary_assigns()
        view.mount(request_with_session)

        context = view.get_context_data()
        # Apply with request=None
        context = view._apply_context_processors(context, None)

        # Context processor variables should NOT be present
        assert 'GOOGLE_ANALYTICS_ID' not in context
        assert 'current_user_name' not in context

    @override_settings(TEMPLATES=[])
    def test_context_processors_with_no_djust_backend(self, request_with_session):
        """Test graceful handling when DjustTemplateBackend is not configured."""
        view = AnalyticsView()
        view.setup(request_with_session)
        view._initialize_temporary_assigns()
        view.mount(request_with_session)

        context = view.get_context_data()
        # Should not raise, just return context unchanged
        context = view._apply_context_processors(context, request_with_session)

        # Context processor variables should NOT be present
        assert 'GOOGLE_ANALYTICS_ID' not in context

    @override_settings(TEMPLATES=TEMPLATES_WITH_CONTEXT_PROCESSORS)
    def test_context_processors_handle_processor_errors(self, request_with_session):
        """Test that errors in context processors are handled gracefully."""
        view = AnalyticsView()
        view.setup(request_with_session)
        view._initialize_temporary_assigns()
        view.mount(request_with_session)

        # Mock import_string to raise an error for one processor
        with patch('djust.live_view.import_string') as mock_import:
            def side_effect(path):
                if 'analytics' in path:
                    raise ImportError("Test error")
                return dummy_user_processor

            mock_import.side_effect = side_effect

            context = view.get_context_data()
            # Should not raise, just log warning and continue
            context = view._apply_context_processors(context, request_with_session)

            # The working processor should still be applied
            # (Note: depends on order, but at least shouldn't crash)
            assert 'count' in context  # View's own context preserved
