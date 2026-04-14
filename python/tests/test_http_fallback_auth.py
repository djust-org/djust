"""
Regression tests for authenticated HTTP fallback render (#712).

Verifies that context processors (especially django.contrib.auth.context_processors.auth)
are applied before rendering in the POST (HTTP fallback) path, so that template
conditionals like {% if user.is_authenticated %} work correctly.

See also: #705 (original fix for context processor injection in HTTP fallback)
and #711 (try/finally cleanup).
"""

import json

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory

from djust import LiveView
from djust.decorators import event_handler


# ---------------------------------------------------------------------------
# Test views
# ---------------------------------------------------------------------------


class AuthAwareView(LiveView):
    """View whose template depends on user.is_authenticated."""

    template = """<div dj-root>
    {% if user.is_authenticated %}
        <span class="auth">Welcome, {{ user.username }}</span>
    {% else %}
        <span class="anon">Please log in</span>
    {% endif %}
    <span class="status">{{ status }}</span>
</div>"""

    def mount(self, request, **kwargs):
        self.status = "mounted"

    @event_handler()
    def update_status(self, **kwargs):
        self.status = "updated"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["status"] = self.status
        return ctx


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _add_session(request):
    """Add session middleware to a request."""
    middleware = SessionMiddleware(lambda x: None)
    middleware.process_request(request)
    request.session.save()
    return request


def _make_authenticated_request(path="/test-auth/", method="get", **kwargs):
    """Create a request with an authenticated user."""
    from django.contrib.auth import get_user_model

    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username="testuser",
        defaults={"is_active": True},
    )

    factory = RequestFactory()
    if method == "get":
        request = factory.get(path)
    else:
        request = factory.post(path, **kwargs)
    request.user = user
    return _add_session(request)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestHTTPFallbackAuthContext:
    """HTTP fallback POST must include auth context so templates render correctly."""

    def test_post_renders_authenticated_content(self):
        """POST (HTTP fallback) with authenticated user renders 'Welcome' content.

        Regression test for #705 / #712: context processors (auth) must be
        applied before the render in the POST path so ``user.is_authenticated``
        evaluates to True.
        """
        view = AuthAwareView()

        # Initial GET to establish state
        get_request = _make_authenticated_request()
        view.get(get_request)

        # POST (HTTP fallback event)
        post_request = _make_authenticated_request(
            method="post",
            data='{"event":"update_status","params":{}}',
            content_type="application/json",
        )
        post_request.session = get_request.session
        response = view.post(post_request)

        assert response.status_code == 200
        data = json.loads(response.content.decode("utf-8"))

        # The response should contain authenticated content, not logged-out
        if "html" in data:
            assert "Welcome" in data["html"] or "testuser" in data["html"]
            assert "Please log in" not in data["html"]
        else:
            # Patches mode — verify patches don't contain anonymous content
            assert "patches" in data

    def test_post_anonymous_renders_logged_out_content(self):
        """POST with anonymous user renders 'Please log in' content."""
        view = AuthAwareView()
        factory = RequestFactory()

        # Initial GET with anonymous user
        get_request = factory.get("/test-auth/")
        get_request = _add_session(get_request)
        get_request.user = AnonymousUser()
        view.get(get_request)

        # POST with anonymous user
        post_request = factory.post(
            "/test-auth/",
            data='{"event":"update_status","params":{}}',
            content_type="application/json",
        )
        post_request.session = get_request.session
        post_request.user = AnonymousUser()
        response = view.post(post_request)

        assert response.status_code == 200
        data = json.loads(response.content.decode("utf-8"))

        if "html" in data:
            assert "Please log in" in data["html"]
            assert "Welcome" not in data["html"]

    def test_context_processor_cleanup_after_post(self):
        """Context processor attrs are cleaned up after POST render (#711)."""
        view = AuthAwareView()

        get_request = _make_authenticated_request()
        view.get(get_request)

        post_request = _make_authenticated_request(
            method="post",
            data='{"event":"update_status","params":{}}',
            content_type="application/json",
        )
        post_request.session = get_request.session
        view.post(post_request)

        # After POST, temporary context processor attrs should be cleaned up.
        # 'perms' is injected by auth context processor — it should NOT remain
        # as an instance attribute on the view.
        assert not hasattr(
            view, "perms"
        ), "Context processor attr 'perms' should be cleaned up after render"

    def test_context_processor_cleanup_on_render_error(self):
        """Context processor attrs are cleaned up even if render raises (#711)."""
        from unittest.mock import patch

        view = AuthAwareView()

        get_request = _make_authenticated_request()
        view.get(get_request)

        # Monkey-patch render_with_diff to raise
        def exploding_render(*args, **kwargs):
            raise RuntimeError("simulated render failure")

        post_request = _make_authenticated_request(
            method="post",
            data='{"event":"update_status","params":{}}',
            content_type="application/json",
        )
        post_request.session = get_request.session

        with patch.object(view, "render_with_diff", side_effect=exploding_render):
            try:
                view.post(post_request)
            except Exception:
                pass  # We expect the error to propagate

        # Even after an error, context processor attrs must be cleaned up
        assert not hasattr(
            view, "perms"
        ), "Context processor attr 'perms' should be cleaned up even after render error (#711)"
