import pytest
from django.test import override_settings

from djust.checks.accounts import check_accounts


def ids():
    return sorted(m.id for m in check_accounts(None))


def test_silent_when_accounts_is_not_configured(settings):
    settings.DJUST_CONFIG = {}
    assert ids() == []


def test_silent_for_a_healthy_django_backend(settings):
    settings.DJUST_CONFIG = {"ACCOUNTS": {"BACKEND": "django"}}
    assert ids() == []


def test_a100_bad_backend(settings):
    settings.DJUST_CONFIG = {"ACCOUNTS": {"BACKEND": "nope.Missing"}}
    assert "djust.A100" in ids()


def test_a100_not_a_backend(settings):
    settings.DJUST_CONFIG = {"ACCOUNTS": {"BACKEND": "djust.auth.accounts.Provider"}}
    assert "djust.A100" in ids()


def test_a101_allauth_missing_app(settings):
    pytest.importorskip("allauth")
    settings.DJUST_CONFIG = {"ACCOUNTS": {"BACKEND": "allauth"}}
    settings.INSTALLED_APPS = [a for a in settings.INSTALLED_APPS if not a.startswith("allauth")]
    assert "djust.A101" in ids()


def test_a101_allauth_missing_middleware(settings):
    pytest.importorskip("allauth")
    settings.DJUST_CONFIG = {"ACCOUNTS": {"BACKEND": "allauth"}}
    settings.MIDDLEWARE = [m for m in settings.MIDDLEWARE if "allauth" not in m]
    assert "djust.A101" in ids()


def test_a102_proxy_without_trusted_count(settings):
    pytest.importorskip("allauth")
    settings.DJUST_CONFIG = {"ACCOUNTS": {"BACKEND": "allauth"}}
    settings.USE_X_FORWARDED_HOST = True
    settings.DJUST_TRUSTED_PROXY_COUNT = 0
    assert "djust.A102" in ids()
    settings.DJUST_TRUSTED_PROXY_COUNT = 1
    assert "djust.A102" not in ids()


def test_a102_trusted_client_ip_header_counts_as_configured(settings):
    # #3068: allauth's ALLAUTH_TRUSTED_CLIENT_IP_HEADER (e.g. ingress-nginx's
    # X-Real-IP) gives the real client IP without a proxy count.
    pytest.importorskip("allauth")
    settings.DJUST_CONFIG = {"ACCOUNTS": {"BACKEND": "allauth"}}
    settings.USE_X_FORWARDED_HOST = True
    settings.DJUST_TRUSTED_PROXY_COUNT = 0
    settings.ALLAUTH_TRUSTED_PROXY_COUNT = 0
    settings.ALLAUTH_TRUSTED_CLIENT_IP_HEADER = "X-Real-IP"
    assert "djust.A102" not in ids()
    # An empty or blank header is not a configuration.
    for blank in ("", "   ", None):
        settings.ALLAUTH_TRUSTED_CLIENT_IP_HEADER = blank
        assert "djust.A102" in ids()


def test_a102_adapter_that_supplies_the_client_ip_counts_as_configured(settings):
    # A project adapter overriding get_client_ip (e.g. reading X-Real-IP with a
    # fallback, so requests without the header still work) is a complete
    # configuration; allauth's rate limits call it.
    pytest.importorskip("allauth")
    settings.DJUST_CONFIG = {"ACCOUNTS": {"BACKEND": "allauth"}}
    settings.USE_X_FORWARDED_HOST = True
    settings.DJUST_TRUSTED_PROXY_COUNT = 0
    settings.ALLAUTH_TRUSTED_PROXY_COUNT = 0
    settings.ACCOUNT_ADAPTER = "djust.tests.accounts.adapters.ClientIpAdapter"
    assert "djust.A102" not in ids()
    # Subclassing without overriding get_client_ip is not a configuration.
    settings.ACCOUNT_ADAPTER = "djust.tests.accounts.adapters.PlainAdapter"
    assert "djust.A102" in ids()
    # An adapter that can't be imported is A107's problem; A102 still warns.
    settings.ACCOUNT_ADAPTER = "djust.tests.accounts.adapters.Missing"
    assert "djust.A102" in ids()


def test_a103_no_verification_in_production(settings):
    pytest.importorskip("allauth")
    settings.DJUST_CONFIG = {"ACCOUNTS": {"BACKEND": "allauth"}}
    settings.DEBUG = False
    settings.ACCOUNT_EMAIL_VERIFICATION = "none"
    assert "djust.A103" in ids()
    settings.DEBUG = True
    assert "djust.A103" not in ids()


def test_a104_double_include(settings):
    pytest.importorskip("allauth")
    import importlib

    from djust.tests.accounts.conftest import remount_accounts

    settings.DJUST_CONFIG = {"ACCOUNTS": {"BACKEND": "allauth"}}
    remount_accounts()  # the accounts include now mounts allauth.urls itself
    import djust.tests.accounts.urls_double_include as double

    importlib.reload(double)
    with override_settings(ROOT_URLCONF="djust.tests.accounts.urls_double_include"):
        assert "djust.A104" in ids()
    with override_settings(ROOT_URLCONF="djust.tests.accounts.urls_accounts_allauth"):
        assert "djust.A104" not in ids()  # the normal single include is fine


def test_a105_stale_project_override(tmp_path, settings):
    (tmp_path / "account").mkdir()
    (tmp_path / "account" / "login.html").write_text('{% extends "account/base_entrance.html" %}')
    settings.DJUST_CONFIG = {"ACCOUNTS": {"BACKEND": "django"}}
    settings.TEMPLATES = [{**settings.TEMPLATES[0], "DIRS": [str(tmp_path)]}]
    assert "djust.A105" in ids()


def test_a106_djust_auth_missing(settings):
    settings.DJUST_CONFIG = {"ACCOUNTS": {"BACKEND": "django"}}
    settings.INSTALLED_APPS = [a for a in settings.INSTALLED_APPS if a != "djust.auth"]
    assert "djust.A106" in ids()


def test_a106_djust_auth_after_allauth(settings):
    pytest.importorskip("allauth")
    settings.DJUST_CONFIG = {"ACCOUNTS": {"BACKEND": "allauth"}}
    apps = [a for a in settings.INSTALLED_APPS if a != "djust.auth"]
    settings.INSTALLED_APPS = apps + ["djust.auth"]
    assert "djust.A106" in ids()


def test_a106_theming_missing(settings):
    settings.DJUST_CONFIG = {"ACCOUNTS": {"BACKEND": "django"}}
    settings.INSTALLED_APPS = [a for a in settings.INSTALLED_APPS if a != "djust.theming"]
    assert "djust.A106" in ids()


def test_checks_can_be_suppressed(settings):
    settings.DJUST_CONFIG = {"ACCOUNTS": {"BACKEND": "nope.Missing"}, "suppress_checks": ["A100"]}
    assert "djust.A100" not in ids()


def test_project_dirs_override_still_wins(tmp_path, settings):
    # Review focus 1: DIRS precede app dirs, so a project's element override beats djust's skin.
    (tmp_path / "allauth" / "elements").mkdir(parents=True)
    (tmp_path / "allauth" / "elements" / "h1.html").write_text("<h1 class=project>x</h1>")
    settings.TEMPLATES = [{**settings.TEMPLATES[0], "DIRS": [str(tmp_path)]}]
    from django.template.loader import get_template

    assert "class=project" in get_template("allauth/elements/h1.html").template.source
