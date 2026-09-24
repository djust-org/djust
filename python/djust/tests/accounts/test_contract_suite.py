"""The account backend contract: every backend passes the same suite (ADR-039)."""

import pytest
from django.test import Client
from django.urls import NoReverseMatch, reverse

from djust.auth.accounts import get_account_backend
from djust.tests.accounts.conftest import remount_accounts

BACKENDS = ["django", "djust.tests.accounts.magic_backend.MagicLinkBackend"]
try:
    import allauth  # noqa: F401

    BACKENDS.append("allauth")
except ImportError:
    pass

pytestmark = [pytest.mark.django_db, pytest.mark.urls("djust.tests.accounts.urls_accounts")]


@pytest.fixture(params=BACKENDS)
def backend(request, settings):
    settings.DJUST_CONFIG = {"ACCOUNTS": {"BACKEND": request.param}}
    if request.param == "allauth":
        from djust.auth.accounts.backends.allauth import apply_allauth_defaults

        apply_allauth_defaults(settings, {})
    remount_accounts()
    return get_account_backend()


def test_login_resolves_and_renders_the_kit(backend):
    html = Client().get(reverse("djust_auth:login")).content.decode()
    assert "dj-auth-card" in html and "djust_auth/auth.css" in html


def test_supported_features_resolve(backend):
    for feature, name in (
        ("login", "login"),
        ("logout", "logout"),
        ("signup", "signup"),
        ("password_reset", "password_reset"),
    ):
        if backend.supports(feature):
            assert reverse(f"djust_auth:{name}")


def test_unsupported_features_have_no_url_and_no_link(backend):
    if backend.supports("signup"):
        pytest.skip("backend supports signup")
    with pytest.raises(NoReverseMatch):
        reverse("djust_auth:signup")
    assert "Create an account" not in Client().get(reverse("djust_auth:login")).content.decode()


def test_off_site_next_is_never_followed(backend):
    # The security property: after signing in, an off-site ``next`` is ignored.
    # (allauth echoes the raw value into its hidden redirect field, but
    # host-checks it before redirecting; the kit's own components never render it.)
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.create_user("nx", "nx@x.io", "Very-long-pw-9x")
    data = {"username": "nx", "login": "nx", "password": "Very-long-pw-9x", "email": "nx@x.io"}
    if backend.name == "allauth":
        from allauth.account.models import EmailAddress

        EmailAddress.objects.create(user=user, email="nx@x.io", primary=True, verified=True)
    r = Client().post(reverse("djust_auth:login") + "?next=https://evil.example/", data)
    assert "evil.example" not in r.get("Location", "")
    if backend.supports("signup") and backend.name != "magic-link":
        assert r.status_code == 302  # really signed in, so the redirect decision was exercised


def test_kit_components_never_render_off_site_next():
    from django.test import RequestFactory

    from djust.auth.accounts import Provider
    from djust.auth.accounts.context import build_auth
    from django.template import Context, Template

    req = RequestFactory().get("/accounts/login/", {"next": "https://evil.example/"})
    auth = build_auth(req)
    auth.providers = [Provider("github", "GitHub", "/g/")]
    html = Template("{% load djust_auth %}{% auth_providers auth %}{% auth_links auth %}").render(
        Context({"auth": auth})
    )
    assert "evil" not in html


def test_logout_get_never_logs_out(backend):
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.create_user("lo", "lo@x.io", "Very-long-pw-9x")
    c = Client()
    c.force_login(user)
    c.get(reverse("djust_auth:logout"))
    assert "_auth_user_id" in c.session
