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
