import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, override_settings
from django.urls import reverse

from djust.auth.accounts import reset_account_backend
from djust.auth.signals import user_signed_up

pytestmark = [pytest.mark.django_db, pytest.mark.urls("djust.tests.accounts.urls_accounts")]
PW = "Very-long-pw-9x"


@pytest.fixture(autouse=True)
def _reset():
    reset_account_backend()
    yield
    reset_account_backend()


def test_stable_names_resolve():
    for name in ("login", "logout", "signup", "password_reset"):
        assert reverse(f"djust_auth:{name}")


def test_login_page_is_the_kit():
    html = Client().get(reverse("djust_auth:login")).content.decode()
    assert "dj-auth-card" in html and 'name="username"' in html and "Create an account" in html


def test_signup_signs_in_and_sends_the_signal():
    got = []
    user_signed_up.connect(
        lambda **kw: got.append(kw["user"].username), weak=False, dispatch_uid="t1"
    )
    try:
        r = Client().post(
            reverse("djust_auth:signup"),
            {"username": "ann", "email": "a@x.io", "password1": PW, "password2": PW},
        )
    finally:
        user_signed_up.disconnect(dispatch_uid="t1")
    assert r.status_code == 302 and got == ["ann"]


def no_example(request, data):
    if data.get("email", "").endswith("@example.com"):
        raise ValidationError("Use a real email address.")


def test_signup_validators_can_block():
    with override_settings(
        DJUST_CONFIG={
            "ACCOUNTS": {"BACKEND": "django", "OPTIONS": {"signup_validators": [no_example]}}
        }
    ):
        r = Client().post(
            reverse("djust_auth:signup"),
            {"username": "b", "email": "b@example.com", "password1": PW, "password2": PW},
        )
    assert r.status_code == 200 and "Use a real email address." in r.content.decode()
    assert not get_user_model().objects.filter(username="b").exists()


def test_logout_needs_post():
    assert Client().get(reverse("djust_auth:logout")).status_code == 405


def test_off_site_next_is_ignored_after_login():
    get_user_model().objects.create_user("c", "c@x.io", PW)
    r = Client().post(
        reverse("djust_auth:login") + "?next=https://evil.example/",
        {"username": "c", "password": PW},
    )
    assert r.status_code == 302 and "evil.example" not in r["Location"]


def test_password_reset_flow_pages_render():
    assert "dj-auth-card" in Client().get(reverse("djust_auth:password_reset")).content.decode()


@pytest.mark.urls("djust.auth.urls")
def test_legacy_djust_auth_urls_now_render():
    # Before ADR-039 these raised TemplateDoesNotExist (djust_auth/login.html never shipped).
    assert Client().get("/login/").status_code == 200
    assert Client().get("/signup/").status_code == 200
