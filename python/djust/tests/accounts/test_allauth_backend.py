import re

import pytest

pytest.importorskip("allauth")

from django.contrib.auth import get_user_model  # noqa: E402
from django.core import mail  # noqa: E402
from django.core.exceptions import ValidationError  # noqa: E402
from django.test import Client  # noqa: E402
from django.urls import reverse  # noqa: E402

from djust.auth.signals import email_verified  # noqa: E402
from djust.tests.accounts.conftest import remount_accounts  # noqa: E402

PW = "Very-long-pw-9x"
pytestmark = [pytest.mark.django_db, pytest.mark.urls("djust.tests.accounts.urls_accounts_allauth")]


def _mount(settings, options=None):
    settings.DJUST_CONFIG = {"ACCOUNTS": {"BACKEND": "allauth", "OPTIONS": options or {}}}
    from djust.auth.accounts.backends.allauth import apply_allauth_defaults

    apply_allauth_defaults(settings, options or {})
    remount_accounts()


@pytest.fixture(autouse=True)
def _allauth(settings):
    _mount(settings)


def test_stable_names_and_allauth_names_both_resolve():
    assert reverse("djust_auth:login") == reverse("account_login")
    assert reverse("djust_auth:signup") == reverse("account_signup")


def test_login_is_skinned_by_the_kit():
    html = Client().get(reverse("account_login")).content.decode()
    assert "dj-auth-card" in html and "djust_auth/auth.css" in html
    assert 'name="login"' in html and 'name="remember"' in html  # remember-me offered


def test_signup_has_no_confirm_password():
    html = Client().get(reverse("account_signup")).content.decode()
    assert 'name="password1"' in html and 'name="password2"' not in html


def test_signup_then_code_verification_sends_email_verified():
    got = []
    email_verified.connect(lambda **kw: got.append(kw["email"]), weak=False, dispatch_uid="t2")
    try:
        c = Client()
        r = c.post(
            reverse("account_signup"), {"email": "dee@x.io", "username": "dee", "password1": PW}
        )
        assert r.status_code == 302, r.content.decode()[:500]
        code = re.search(r"\b([A-Z0-9]{4}-[A-Z0-9]{4})\b", mail.outbox[-1].body).group(1)
        r = c.post(r["Location"], {"code": code})
    finally:
        email_verified.disconnect(dispatch_uid="t2")
    assert got == ["dee@x.io"]


def test_signup_validators_block_through_allauth(settings):
    def nope(request, data):
        raise ValidationError("Signups are paused.")

    _mount(settings, {"signup_validators": [nope]})
    r = Client().post(
        reverse("account_signup"), {"email": "e@x.io", "username": "e", "password1": PW}
    )
    assert "Signups are paused." in r.content.decode()
    assert not get_user_model().objects.filter(username="e").exists()


def test_verification_link_get_does_not_verify(settings):
    # allauth only routes confirm-email/<key>/ when verification is by link.
    settings.ACCOUNT_EMAIL_VERIFICATION_BY_CODE_ENABLED = False
    remount_accounts()
    from allauth.account.models import EmailAddress, EmailConfirmationHMAC

    u = get_user_model().objects.create_user("f", "f@x.io", PW)
    addr = EmailAddress.objects.create(user=u, email="f@x.io", primary=True, verified=False)
    key = EmailConfirmationHMAC(addr).key
    Client().get(reverse("account_confirm_email", args=[key]))
    addr.refresh_from_db()
    assert addr.verified is False  # a mail-scanner prefetch can't verify


def test_logout_get_does_not_log_out():
    u = get_user_model().objects.create_user("g", "g@x.io", PW)
    c = Client()
    c.force_login(u)
    c.get(reverse("account_logout"))
    assert "_auth_user_id" in c.session


def _signed_in_redirect(next_url, settings, options=None):
    if options is not None:
        _mount(settings, options)
    from allauth.account.models import EmailAddress

    u = get_user_model().objects.create_user("h", "h@x.io", PW)
    EmailAddress.objects.create(user=u, email="h@x.io", primary=True, verified=True)
    r = Client().post(
        reverse("account_login") + "?next=" + next_url, {"login": "h", "password": PW}
    )
    return r.get("Location", "")


def test_wildcard_allowed_hosts_does_not_open_redirects(settings):
    # allauth's own is_safe_url trusts every ALLOWED_HOSTS match; djust's adapter doesn't.
    settings.ALLOWED_HOSTS = [".tenant-apps.example", "testserver"]
    assert "attacker.tenant-apps.example" not in _signed_in_redirect(
        "https://attacker.tenant-apps.example/", settings
    )


def test_redirect_hosts_option_allows_listed_hosts(settings):
    loc = _signed_in_redirect(
        "https://docs.example.org/after", settings, {"redirect_hosts": ["docs.example.org"]}
    )
    assert loc == "https://docs.example.org/after"


def test_code_page_confirm_is_the_primary_button():
    c = Client()
    r = c.post(reverse("account_signup"), {"email": "p@x.io", "username": "p", "password1": PW})
    html = c.get(r["Location"]).content.decode()
    button = re.search(r"<button([^>]*)>\s*Confirm\s*</button>", html)
    assert button and 'class="dj-auth-submit"' in button.group(1)
