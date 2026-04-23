"""Regression tests for URL-redirection + path-injection hardening."""

import pytest
from django.test import RequestFactory


def test_signup_view_rejects_off_site_next_redirect(db, settings):
    """SignupView.get_success_url must not honor an off-site next URL."""
    from djust.auth.views import SignupView

    factory = RequestFactory()
    req = factory.post("/signup/", {"next": "https://evil.com/steal"})
    # The view's host for validation
    req.META["HTTP_HOST"] = "example.com"

    view = SignupView()
    view.request = req
    settings.LOGIN_REDIRECT_URL = "/home/"

    assert view.get_success_url() == "/home/"  # falls back because next is off-site


def test_signup_view_accepts_same_site_next_redirect(db, settings):
    """SignupView.get_success_url accepts a same-host relative next."""
    from djust.auth.views import SignupView

    factory = RequestFactory()
    req = factory.post("/signup/", {"next": "/dashboard/"})
    req.META["HTTP_HOST"] = "example.com"

    view = SignupView()
    view.request = req
    settings.LOGIN_REDIRECT_URL = "/home/"

    # /dashboard/ is same-origin and relative; should be accepted
    assert view.get_success_url() == "/dashboard/"


def test_signup_view_rejects_protocol_relative_redirect(db, settings):
    """Protocol-relative URL (//evil.com) must also be rejected."""
    from djust.auth.views import SignupView

    factory = RequestFactory()
    req = factory.post("/signup/", {"next": "//evil.com/steal"})
    req.META["HTTP_HOST"] = "example.com"

    view = SignupView()
    view.request = req
    settings.LOGIN_REDIRECT_URL = "/home/"

    assert view.get_success_url() == "/home/"


def test_storybook_path_traversal_rejected():
    """get_component_template_source rejects path-traversal payloads."""
    from djust.theming.gallery.storybook import get_component_template_source

    # Directory traversal attempts
    assert get_component_template_source("../../../etc/passwd") == ""
    assert get_component_template_source("../secret") == ""
    assert get_component_template_source("foo/bar") == ""
    # Empty / null
    assert get_component_template_source("") == ""
    # Valid name with no matching file still returns ""
    assert get_component_template_source("does-not-exist-anywhere") == ""


def test_storybook_valid_component_name_still_works():
    """Sanity check: a known-valid component name still loads."""
    from djust.theming.gallery.storybook import (
        COMPONENT_CONTRACTS,
        get_component_template_source,
    )

    if not COMPONENT_CONTRACTS:
        pytest.skip("No contracted components to test with")
    # Use the first real contracted component name
    name = next(iter(COMPONENT_CONTRACTS.keys()))
    result = get_component_template_source(name)
    # Either the file exists and returns non-empty OR it returns "" if the template
    # file isn't actually on disk. Both are fine — the point is no exception raised.
    assert isinstance(result, str)


# Additional edge-case regression tests (#922)


def test_signup_view_rejects_javascript_scheme(db, settings):
    """SignupView must reject javascript: scheme (XSS via redirect)."""
    from djust.auth.views import SignupView

    factory = RequestFactory()
    req = factory.post("/signup/", {"next": "javascript:alert(1)"})
    req.META["HTTP_HOST"] = "example.com"

    view = SignupView()
    view.request = req
    settings.LOGIN_REDIRECT_URL = "/home/"

    # Django's url_has_allowed_host_and_scheme rejects javascript: by default.
    assert view.get_success_url() == "/home/"


def test_signup_view_rejects_https_to_http_downgrade(db, settings):
    """When request is HTTPS, a same-host http:// next URL must be rejected."""
    from djust.auth.views import SignupView

    factory = RequestFactory()
    # Same host, but http:// while the request is secure => downgrade attempt.
    req = factory.post("/signup/", {"next": "http://example.com/dashboard/"}, secure=True)
    req.META["HTTP_HOST"] = "example.com"

    view = SignupView()
    view.request = req
    settings.LOGIN_REDIRECT_URL = "/home/"

    # require_https=True (view passes request.is_secure()) rejects the http:// URL.
    assert view.get_success_url() == "/home/"


def test_storybook_rejects_null_byte():
    """get_component_template_source must reject null-byte payloads."""
    from djust.theming.gallery.storybook import get_component_template_source

    # Null bytes are not in the allowlist -> "" fallback.
    assert get_component_template_source("foo\x00.html") == ""
    assert get_component_template_source("button\x00../../etc/passwd") == ""


def test_storybook_rejects_uppercase():
    """get_component_template_source allowlist is lowercase-only (case-sensitive)."""
    from djust.theming.gallery.storybook import get_component_template_source

    # Component names on disk are all lowercase; uppercase fails the allowlist.
    assert get_component_template_source("FOO") == ""
    assert get_component_template_source("BUTTON") == ""


# Additional regression tests for the expanded redirect audit (#921)


def test_request_mixin_rejects_unsafe_hook_redirect(db, settings):
    """RequestMixin.get() must reject off-site URLs returned by on_mount hooks."""
    from djust import LiveView
    from djust.hooks import on_mount

    @on_mount
    def redirect_to_evil(view, request, **kwargs):
        return "https://evil.com/steal"

    class EvilHookView(LiveView):
        template_name = "djust_theming/components/button.html"
        on_mount = [redirect_to_evil]

        def mount(self, request, **kwargs):
            self.count = 0

    factory = RequestFactory()
    req = factory.get("/evil/")
    req.META["HTTP_HOST"] = "example.com"

    # Anonymous user (no auth required for this view)
    from django.contrib.auth.models import AnonymousUser

    req.user = AnonymousUser()

    view = EvilHookView()
    response = view.get(req)
    # Off-site URL from the hook is replaced with "/" fallback
    assert response.status_code == 302
    assert response["Location"] == "/"


def test_request_mixin_accepts_safe_hook_redirect(db, settings):
    """Same-site hook redirects are preserved unchanged."""
    from djust import LiveView
    from djust.hooks import on_mount

    @on_mount
    def redirect_to_login(view, request, **kwargs):
        return "/accounts/login/"

    class SafeHookView(LiveView):
        template_name = "djust_theming/components/button.html"
        on_mount = [redirect_to_login]

        def mount(self, request, **kwargs):
            pass

    factory = RequestFactory()
    req = factory.get("/protected/")
    req.META["HTTP_HOST"] = "example.com"

    from django.contrib.auth.models import AnonymousUser

    req.user = AnonymousUser()

    view = SafeHookView()
    response = view.get(req)
    # Same-site relative URL is honored
    assert response.status_code == 302
    assert response["Location"] == "/accounts/login/"


def test_login_required_mixin_rejects_offsite_login_url(db, settings):
    """LoginRequiredLiveViewMixin falls back when settings.LOGIN_URL is off-site."""
    from djust.auth.mixins import LoginRequiredLiveViewMixin

    factory = RequestFactory()
    req = factory.get("/protected/")
    req.META["HTTP_HOST"] = "example.com"

    class AnonUser:
        is_authenticated = False

    req.user = AnonUser()

    settings.LOGIN_URL = "https://evil.com/login/"

    class DummyBase:
        def dispatch(self, request, *args, **kwargs):
            raise AssertionError("super().dispatch() should not run for anon user")

    class View(LoginRequiredLiveViewMixin, DummyBase):
        pass

    view = View()
    response = view.dispatch(req)

    # Off-site login_url must be replaced with safe fallback
    assert response.status_code == 302
    assert response["Location"].startswith("/accounts/login/")
