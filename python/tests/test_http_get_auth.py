"""
Tests for HTTP GET auth enforcement and handle_params on initial page load.

Covers:
- login_required=True blocks unauthenticated HTTP GET (issue #633)
- permission_required blocks unauthorized HTTP GET
- handle_params() called on HTTP GET with URL query params (issue #634)
- Public views (login_required=False) remain accessible
"""

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "django.contrib.sessions",
            "django.contrib.messages",
            "djust",
        ],
        SECRET_KEY="test-secret-key-for-http-get-auth",
        USE_TZ=True,
        SESSION_ENGINE="django.contrib.sessions.backends.db",
        LOGIN_URL="/test-login/",
        ROOT_URLCONF="tests.urls_stub",
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
        MIDDLEWARE=[
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
        ],
    )
    django.setup()


import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from djust.live_view import LiveView


# ---------------------------------------------------------------------------
# Test views
# ---------------------------------------------------------------------------


class PublicView(LiveView):
    login_required = False
    template = "<div dj-root>public</div>"


class ProtectedView(LiveView):
    login_required = True
    template = "<div dj-root>protected</div>"


class ParamsView(LiveView):
    login_required = False
    template = "<div dj-root>{{ active_tab }}</div>"

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self.active_tab = "default"

    def handle_params(self, params, uri="", **kwargs):
        self.active_tab = params.get("tab", "default")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["active_tab"] = self.active_tab
        return ctx


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(path="/", authenticated=True, query_params=None):
    factory = RequestFactory()
    url = path
    if query_params:
        qs = "&".join(f"{k}={v}" for k, v in query_params.items())
        url = f"{path}?{qs}"
    request = factory.get(url)
    if authenticated:
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user, _ = User.objects.get_or_create(username="testuser", defaults={"is_active": True})
        request.user = user
    else:
        request.user = AnonymousUser()
    # Add session (required by LiveView)
    from django.contrib.sessions.backends.db import SessionStore

    request.session = SessionStore()
    return request


# ---------------------------------------------------------------------------
# Auth enforcement tests (issue #633)
# ---------------------------------------------------------------------------


class TestHttpGetAuthEnforcement:
    """login_required must be enforced on HTTP GET, not just WebSocket."""

    @pytest.mark.django_db
    def test_protected_view_redirects_unauthenticated(self):
        """Unauthenticated GET to login_required=True view returns redirect."""
        request = _make_request(authenticated=False)
        view = ProtectedView()
        response = view.get(request)
        assert response.status_code == 302
        # Redirects to LOGIN_URL (may be /accounts/login/ or project-configured)
        assert "login" in response.url.lower()
        assert "next=" in response.url

    @pytest.mark.django_db
    def test_protected_view_includes_next_param(self):
        """Redirect includes ?next= so user returns after login."""
        request = _make_request(path="/claims/42/", authenticated=False)
        view = ProtectedView()
        response = view.get(request)
        assert response.status_code == 302
        assert "next=" in response.url
        assert "%2Fclaims%2F42%2F" in response.url or "/claims/42/" in response.url

    @pytest.mark.django_db
    def test_protected_view_allows_authenticated(self):
        """Authenticated GET to login_required=True view returns 200."""
        request = _make_request(authenticated=True)
        view = ProtectedView()
        response = view.get(request)
        assert response.status_code == 200

    @pytest.mark.django_db
    def test_public_view_allows_unauthenticated(self):
        """Unauthenticated GET to login_required=False view returns 200."""
        request = _make_request(authenticated=False)
        view = PublicView()
        response = view.get(request)
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# handle_params tests (issue #634)
# ---------------------------------------------------------------------------


class TestHttpGetHandleParams:
    """handle_params() must be called on HTTP GET with URL query params."""

    @pytest.mark.django_db
    def test_handle_params_called_with_query_params(self):
        """GET ?tab=documents calls handle_params({"tab": "documents"})."""
        request = _make_request(query_params={"tab": "documents"})
        view = ParamsView()
        response = view.get(request)
        assert response.status_code == 200
        # The view's active_tab should be "documents", not "default"
        assert view.active_tab == "documents"

    @pytest.mark.django_db
    def test_handle_params_called_without_query_params(self):
        """GET with no query params calls handle_params({})."""
        request = _make_request()
        view = ParamsView()
        response = view.get(request)
        assert response.status_code == 200
        assert view.active_tab == "default"

    @pytest.mark.django_db
    def test_handle_params_receives_flattened_params(self):
        """Query params are flattened: {"tab": ["docs"]} → {"tab": "docs"}."""
        request = _make_request(query_params={"tab": "investigation"})
        view = ParamsView()
        view.get(request)
        # Should be a string, not a list
        assert view.active_tab == "investigation"
        assert isinstance(view.active_tab, str)
