import pytest

pytest.importorskip("allauth")

from djust.auth.accounts.backends.allauth import apply_allauth_defaults  # noqa: E402


class S:  # a bare settings stand-in
    pass


def test_defaults_fill_only_what_is_unset():
    s = S()
    s.ACCOUNT_EMAIL_VERIFICATION = "optional"
    applied = apply_allauth_defaults(s, {})
    assert s.ACCOUNT_EMAIL_VERIFICATION == "optional"  # project wins
    assert s.ACCOUNT_CONFIRM_EMAIL_ON_GET is False and s.ACCOUNT_LOGOUT_ON_GET is False
    assert "ACCOUNT_EMAIL_VERIFICATION" not in applied


def test_link_verification_option(settings):
    s = S()
    apply_allauth_defaults(s, {"verification": "link"})
    assert s.ACCOUNT_EMAIL_VERIFICATION_BY_CODE_ENABLED is False


def test_proxy_count_follows_djust(settings):
    settings.DJUST_TRUSTED_PROXY_COUNT = 2
    s = S()
    apply_allauth_defaults(s, {})
    assert s.ALLAUTH_TRUSTED_PROXY_COUNT == 2


def test_junk_proxy_count_fails_safe_to_zero(settings):
    settings.DJUST_TRUSTED_PROXY_COUNT = "lots"
    s = S()
    apply_allauth_defaults(s, {})
    assert s.ALLAUTH_TRUSTED_PROXY_COUNT == 0
    settings.DJUST_TRUSTED_PROXY_COUNT = -3
    s2 = S()
    apply_allauth_defaults(s2, {})
    assert s2.ALLAUTH_TRUSTED_PROXY_COUNT == 0


def test_ready_applies_defaults_only_for_the_allauth_backend(settings):
    from django.apps import apps

    settings.DJUST_CONFIG = {"ACCOUNTS": {"BACKEND": "django"}}
    if hasattr(settings, "ACCOUNT_LOGOUT_ON_GET"):
        del settings.ACCOUNT_LOGOUT_ON_GET
    apps.get_app_config("djust_auth").ready()
    assert not hasattr(settings, "ACCOUNT_LOGOUT_ON_GET")


def test_ready_applies_defaults_for_the_allauth_backend(settings):
    from django.apps import apps

    settings.DJUST_CONFIG = {"ACCOUNTS": {"BACKEND": "allauth"}}
    if hasattr(settings, "ACCOUNT_LOGOUT_ON_GET"):
        del settings.ACCOUNT_LOGOUT_ON_GET
    apps.get_app_config("djust_auth").ready()
    assert settings.ACCOUNT_LOGOUT_ON_GET is False
