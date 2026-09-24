import pytest
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.test import RequestFactory, override_settings

from djust.auth.accounts import (
    FEATURES,
    AccountBackend,
    Provider,
    get_account_backend,
    reset_account_backend,
)
from djust.auth.accounts.providers import icon_for, label_for


class Dummy(AccountBackend):
    name = "dummy"
    features = frozenset({"login", "logout"})

    def urlpatterns(self):
        return []


@pytest.fixture(autouse=True)
def _reset():
    reset_account_backend()
    yield
    reset_account_backend()


def test_features_vocabulary():
    assert FEATURES == {
        "login",
        "logout",
        "signup",
        "verify_email",
        "password_reset",
        "social",
        "remember_me",
    }


@pytest.mark.xfail(strict=True, reason="DjangoBackend lands in Task 5")
def test_default_backend_is_django():
    assert get_account_backend().name == "django"


@override_settings(
    DJUST_CONFIG={
        "ACCOUNTS": {
            "BACKEND": "djust.tests.accounts.test_contract_base.Dummy",
            "OPTIONS": {"x": 1},
        }
    }
)
def test_dotted_path_backend_with_options():
    b = get_account_backend()
    assert isinstance(b, Dummy) and b.options == {"x": 1}
    assert b.supports("login") and not b.supports("signup")


@override_settings(DJUST_CONFIG={"ACCOUNTS": {"BACKEND": "nope.Missing"}})
def test_unimportable_backend_is_a_clear_error():
    with pytest.raises(ImproperlyConfigured, match="nope.Missing"):
        get_account_backend()


@override_settings(DJUST_CONFIG={"ACCOUNTS": {"BACKEND": "djust.auth.accounts.Provider"}})
def test_non_backend_class_is_rejected():
    with pytest.raises(ImproperlyConfigured, match="AccountBackend"):
        get_account_backend()


def test_settings_change_resets_the_cached_backend():
    with override_settings(
        DJUST_CONFIG={"ACCOUNTS": {"BACKEND": "djust.tests.accounts.test_contract_base.Dummy"}}
    ):
        assert isinstance(get_account_backend(), Dummy)
    with override_settings(
        DJUST_CONFIG={
            "ACCOUNTS": {
                "BACKEND": "djust.tests.accounts.test_contract_base.Dummy",
                "OPTIONS": {"y": 2},
            }
        }
    ):
        assert get_account_backend().options == {"y": 2}


def test_unknown_features_are_rejected_at_init():
    class Bad(Dummy):
        features = frozenset({"login", "teleport"})

    with pytest.raises(ImproperlyConfigured, match="teleport"):
        Bad()


def test_signup_validators_run_in_order_and_raise():
    seen = []

    def ok(request, data):
        seen.append("ok")

    def no(request, data):
        raise ValidationError("blocked")

    b = Dummy()
    b.signup_validators = [ok, no]
    with pytest.raises(ValidationError, match="blocked"):
        b.run_signup_validators(RequestFactory().post("/"), {})
    assert seen == ["ok"]


def test_options_signup_validators_are_appended():
    def v(request, data):
        pass

    assert Dummy({"signup_validators": [v]}).signup_validators == [v]


def test_known_and_unknown_provider_metadata():
    assert "<svg" in icon_for("github") and 'class="' not in icon_for("github")
    assert label_for("github", "x") == "GitHub"
    assert icon_for("unknown-idp") == ""
    assert label_for("unknown-idp", "Acme SSO") == "Acme SSO"


def test_provider_is_plain_data():
    p = Provider("github", "GitHub", "/accounts/github/login/")
    assert p.icon == ""
