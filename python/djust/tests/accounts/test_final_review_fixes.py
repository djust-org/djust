"""Final-review fix pass for ADR-039 (each test reproduces one review finding)."""

import pathlib
import re
import subprocess
import sys
import textwrap

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from djust.tests.accounts.conftest import remount_accounts

PW = "Very-long-pw-9x"
WT = pathlib.Path(__file__).parents[4]


# ── C-1: a non-allauth backend must start even when allauth is installed but not configured ──


@pytest.mark.parametrize(
    "backend", ["django", "djust.tests.accounts.magic_backend.MagicLinkBackend"]
)
def test_setup_does_not_import_allauth_models_for_other_backends(tmp_path, backend):
    pytest.importorskip("allauth")
    (tmp_path / "c1_settings.py").write_text(
        textwrap.dedent(
            f"""
            SECRET_KEY = "x"
            INSTALLED_APPS = ["django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions",
                              "djust", "djust.theming", "djust.auth"]
            DATABASES = {{"default": {{"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}}}
            DJUST_CONFIG = {{"ACCOUNTS": {{"BACKEND": "{backend}"}}}}
            """
        )
    )
    env = {
        "PYTHONPATH": f"{tmp_path}:{WT / 'python'}",
        "DJANGO_SETTINGS_MODULE": "c1_settings",
        "PATH": "/usr/bin:/bin",
    }
    proc = subprocess.run(
        [sys.executable, "-c", "import django; django.setup(); print('STARTED')"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert "STARTED" in proc.stdout, proc.stderr[-1500:]


# ── C-2: the django backend's password reset works end to end ──


@pytest.mark.django_db
@pytest.mark.urls("djust.tests.accounts.urls_accounts")
def test_django_backend_password_reset_end_to_end(settings):
    settings.DJUST_CONFIG = {"ACCOUNTS": {"BACKEND": "django"}}
    remount_accounts()
    get_user_model().objects.create_user("rs", "rs@x.io", PW)
    c = Client()
    r = c.post(reverse("djust_auth:password_reset"), {"email": "rs@x.io"})
    assert r.status_code == 302, r.status_code
    link = re.search(r"https?://[^\s]+/password/reset/[^\s]+", mail.outbox[-1].body).group(0)
    r = c.get(link, follow=True)
    new = "Another-long-pw-7q"
    r = c.post(
        r.redirect_chain[-1][0] if r.redirect_chain else link,
        {"new_password1": new, "new_password2": new},
    )
    assert r.status_code == 302
    assert Client().login(username="rs", password=new)


# ── I-1: signup validators also guard social signup (manual form and auto-signup) ──


def _refuse(request, data):
    raise ValidationError("Signups from here are blocked.")


@pytest.mark.django_db
def test_social_signup_form_runs_signup_validators(settings):
    pytest.importorskip("allauth")
    from allauth.socialaccount.models import SocialAccount, SocialLogin

    from djust.auth.accounts.backends.allauth import apply_allauth_defaults

    settings.DJUST_CONFIG = {
        "ACCOUNTS": {"BACKEND": "allauth", "OPTIONS": {"signup_validators": [_refuse]}}
    }
    apply_allauth_defaults(settings, {"signup_validators": [_refuse]})
    remount_accounts()
    from djust.auth.accounts.backends.allauth_integration import DjustSocialSignupForm

    sl = SocialLogin(
        user=get_user_model()(email="bot@mailinator.com"),
        account=SocialAccount(provider="github", uid="1"),
    )
    form = DjustSocialSignupForm(
        data={"email": "bot@mailinator.com", "username": "bot"}, sociallogin=sl
    )
    assert not form.is_valid()
    assert "Signups from here are blocked." in str(form.errors)


@pytest.mark.django_db
def test_social_auto_signup_falls_back_to_the_form_when_validators_refuse(settings, rf):
    pytest.importorskip("allauth")
    from allauth.socialaccount.models import SocialAccount, SocialLogin

    from djust.auth.accounts.backends.allauth import apply_allauth_defaults
    from djust.auth.accounts.backends.allauth_integration import DjustSocialAccountAdapter

    seen = []

    def record(request, data):
        seen.append(data.get("source"))
        raise ValidationError("no")

    settings.DJUST_CONFIG = {
        "ACCOUNTS": {"BACKEND": "allauth", "OPTIONS": {"signup_validators": [record]}}
    }
    apply_allauth_defaults(settings, {"signup_validators": [record]})
    remount_accounts()
    sl = SocialLogin(
        user=get_user_model()(email="bot@mailinator.com"),
        account=SocialAccount(provider="github", uid="1"),
    )
    assert DjustSocialAccountAdapter().is_auto_signup_allowed(rf.get("/"), sl) is False
    assert seen == ["social"]


def test_social_signup_defaults_point_at_djust():
    pytest.importorskip("allauth")
    from djust.auth.accounts.backends.allauth import SECURE_DEFAULTS

    assert SECURE_DEFAULTS["SOCIALACCOUNT_ADAPTER"].endswith("DjustSocialAccountAdapter")
    assert SECURE_DEFAULTS["SOCIALACCOUNT_FORMS"]["signup"].endswith("DjustSocialSignupForm")


# ── I-2: a closed or unsupported signup refuses accounts, not just hides the link ──


@pytest.mark.django_db
@pytest.mark.urls("djust.tests.accounts.urls_accounts")
def test_closed_signup_creates_no_account(settings):
    settings.DJUST_CONFIG = {
        "ACCOUNTS": {"BACKEND": "djust.tests.accounts.test_final_review_fixes.Closed"}
    }
    remount_accounts()
    r = Client().post(
        reverse("djust_auth:signup"),
        {"username": "z", "email": "z@x.io", "password1": PW, "password2": PW},
    )
    assert r.status_code == 403
    assert not get_user_model().objects.filter(username="z").exists()


@pytest.mark.django_db
@pytest.mark.urls("djust.tests.accounts.urls_accounts")
def test_unsupported_signup_is_not_mounted(settings):
    settings.DJUST_CONFIG = {
        "ACCOUNTS": {"BACKEND": "djust.tests.accounts.test_final_review_fixes.NoSignup"}
    }
    remount_accounts()
    assert Client().post("/accounts/signup/", {"username": "y"}).status_code == 404


# ── I-3: the allauth aliases never re-open routes allauth removed ──


@pytest.mark.django_db
@pytest.mark.urls("djust.tests.accounts.urls_accounts_allauth")
def test_socialaccount_only_has_no_local_signup(settings):
    pytest.importorskip("allauth")
    from djust.auth.accounts import get_account_backend
    from djust.auth.accounts.backends.allauth import apply_allauth_defaults

    settings.SOCIALACCOUNT_ONLY = True
    settings.DJUST_CONFIG = {"ACCOUNTS": {"BACKEND": "allauth"}}
    apply_allauth_defaults(settings, {})
    remount_accounts()
    assert Client().get("/accounts/signup/").status_code == 404
    assert Client().get("/accounts/password/reset/").status_code == 404
    assert not {"signup", "password_reset", "verify_email"} & set(get_account_backend().features)


# ── I-4: existing (legacy-spelled) allauth configurations are left alone ──


class S:
    pass


def test_legacy_allauth_settings_keep_their_meaning():
    pytest.importorskip("allauth")
    from djust.auth.accounts.backends.allauth import apply_allauth_defaults

    s = S()
    s.ACCOUNT_AUTHENTICATION_METHOD = "email"
    s.ACCOUNT_USERNAME_REQUIRED = False
    applied = apply_allauth_defaults(s, {})
    assert "ACCOUNT_LOGIN_METHODS" not in applied and "ACCOUNT_SIGNUP_FIELDS" not in applied


def test_no_username_field_user_model_is_respected():
    pytest.importorskip("allauth")
    from djust.auth.accounts.backends.allauth import apply_allauth_defaults

    s = S()
    s.ACCOUNT_USER_MODEL_USERNAME_FIELD = None
    apply_allauth_defaults(s, {})
    assert "username*" not in s.ACCOUNT_SIGNUP_FIELDS and s.ACCOUNT_LOGIN_METHODS == {"email"}


# ── I-5: email sign-in needs allauth's authentication backend ──


def test_a101_requires_allauth_authentication_backend(settings):
    pytest.importorskip("allauth")
    from djust.checks.accounts import check_accounts

    settings.DJUST_CONFIG = {"ACCOUNTS": {"BACKEND": "allauth"}}
    settings.AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]
    assert "djust.A101" in [m.id for m in check_accounts(None)]


def test_quick_start_lists_the_allauth_authentication_backend():
    guide = (WT / "docs/website/guides/accounts.md").read_text()
    quick = guide.split("## Quick start", 1)[1].split("\n## ", 1)[0]
    assert "allauth.account.auth_backends.AuthenticationBackend" in quick


# ── I-6: a project's own adapter or forms can't silently drop djust's protections ──


def test_a107_warns_when_the_adapter_is_not_djusts(settings):
    pytest.importorskip("allauth")
    from djust.checks.accounts import check_accounts

    settings.DJUST_CONFIG = {"ACCOUNTS": {"BACKEND": "allauth"}}
    settings.ACCOUNT_ADAPTER = "allauth.account.adapter.DefaultAccountAdapter"
    assert "djust.A107" in [m.id for m in check_accounts(None)]
    settings.ACCOUNT_ADAPTER = (
        "djust.auth.accounts.backends.allauth_integration.DjustAccountAdapter"
    )
    assert "djust.A107" not in [m.id for m in check_accounts(None)]


def test_project_account_forms_are_merged_not_replaced():
    pytest.importorskip("allauth")
    from djust.auth.accounts.backends.allauth import apply_allauth_defaults

    s = S()
    s.ACCOUNT_FORMS = {"login": "myproject.forms.LoginForm"}
    apply_allauth_defaults(s, {})
    assert s.ACCOUNT_FORMS["login"] == "myproject.forms.LoginForm"
    assert s.ACCOUNT_FORMS["signup"].endswith("DjustSignupForm")


# ── M-3 (re-graded): an infinite proxy count must not crash startup ──


@pytest.mark.parametrize("value", ["inf", float("inf"), "-inf", "nan"])
def test_infinite_proxy_count_fails_safe(settings, value):
    from djust._client_ip import _trusted_proxy_count

    settings.DJUST_TRUSTED_PROXY_COUNT = value
    assert _trusted_proxy_count() == 0


# ── M-7 (re-graded): the guide says when settings are read, truthfully ──


def test_guide_describes_when_options_are_read():
    guide = (WT / "docs/website/guides/accounts.md").read_text()
    assert "read on every request" not in guide
    assert "Changing `BACKEND` or `verification` needs a restart" in guide


def _make(name, **attrs):
    from djust.auth.accounts.backends.django import DjangoBackend

    return type(name, (DjangoBackend,), attrs)


Closed = _make("Closed", is_open_for_signup=lambda self, request: False)
NoSignup = _make("NoSignup", features=frozenset({"login", "logout", "password_reset"}))
