"""The ``allauth`` account backend: django-allauth's flows, skinned by the djust kit (ADR-039).

allauth keeps its own views and page templates (they carry real logic:
code flows, reauthentication, conditional fields). djust restyles every one
of them through allauth's supported override points,
``allauth/layouts/*.html`` and ``allauth/elements/*.html``, shipped in
``djust.auth``'s templates, so ``djust.auth`` must come before ``allauth`` in
``INSTALLED_APPS``.

This module never imports allauth at load time: the adapter and forms djust
plugs into allauth live in ``allauth_integration``, referenced only by
allauth's settings strings. A project on another backend can have allauth
installed without configuring it.
"""

from __future__ import annotations

import logging
from typing import Any

from django.http import HttpRequest
from django.urls import include, path

from djust._client_ip import _trusted_proxy_count

from ..base import AccountBackend, Provider
from ..providers import label_for

logger = logging.getLogger(__name__)

_INTEGRATION = "djust.auth.accounts.backends.allauth_integration"

#: allauth settings djust sets when the project hasn't (docs: "Security defaults").
SECURE_DEFAULTS: dict[str, Any] = {
    "ACCOUNT_EMAIL_VERIFICATION": "mandatory",
    "ACCOUNT_EMAIL_VERIFICATION_BY_CODE_ENABLED": True,
    "ACCOUNT_CONFIRM_EMAIL_ON_GET": False,
    "ACCOUNT_PASSWORD_RESET_BY_CODE_ENABLED": True,
    "ACCOUNT_LOGOUT_ON_GET": False,
    "ACCOUNT_SESSION_REMEMBER": None,
    "ACCOUNT_LOGIN_METHODS": {"email", "username"},
    "ACCOUNT_SIGNUP_FIELDS": ["email*", "username*", "password1*"],
    "ACCOUNT_ADAPTER": f"{_INTEGRATION}.DjustAccountAdapter",
    "ACCOUNT_FORMS": {"signup": f"{_INTEGRATION}.DjustSignupForm"},
    "SOCIALACCOUNT_ADAPTER": f"{_INTEGRATION}.DjustSocialAccountAdapter",
    "SOCIALACCOUNT_FORMS": {"signup": f"{_INTEGRATION}.DjustSocialSignupForm"},
}

#: allauth's older spellings of login/sign-up configuration. When a project
#: uses any of them, djust leaves ACCOUNT_LOGIN_METHODS / ACCOUNT_SIGNUP_FIELDS
#: alone, because allauth prefers the new names and would silently override them.
LEGACY_LOGIN_SETTINGS = (
    "ACCOUNT_AUTHENTICATION_METHOD",
    "ACCOUNT_USERNAME_REQUIRED",
    "ACCOUNT_EMAIL_REQUIRED",
    "ACCOUNT_SIGNUP_PASSWORD_ENTER_TWICE",
    "ACCOUNT_SIGNUP_EMAIL_ENTER_TWICE",
)
_LOGIN_SHAPE = ("ACCOUNT_LOGIN_METHODS", "ACCOUNT_SIGNUP_FIELDS")
_MERGED_DICTS = ("ACCOUNT_FORMS", "SOCIALACCOUNT_FORMS")


def apply_allauth_defaults(settings: Any, options: dict | None = None) -> list[str]:
    """Set each secure default the project hasn't set itself; return the names applied.

    - ``options["verification"] == "link"`` keeps verification by link.
    - Projects on allauth's legacy login settings keep them.
    - A user model without a username field signs in and up by email only.
    - ``ACCOUNT_FORMS`` / ``SOCIALACCOUNT_FORMS`` are merged: a project's own
      entries win, and djust fills in only a missing ``signup`` form.
    """
    if options is None:
        from djust.config import get_djust_config

        spec = get_djust_config().get("ACCOUNTS") or {}
        options = (spec.get("OPTIONS") if isinstance(spec, dict) else None) or {}
    defaults = dict(SECURE_DEFAULTS)
    if options.get("verification") == "link":
        defaults["ACCOUNT_EMAIL_VERIFICATION_BY_CODE_ENABLED"] = False
    if getattr(settings, "ACCOUNT_USER_MODEL_USERNAME_FIELD", "username") is None:
        defaults["ACCOUNT_LOGIN_METHODS"] = {"email"}
        defaults["ACCOUNT_SIGNUP_FIELDS"] = ["email*", "password1*"]
    legacy = any(hasattr(settings, name) for name in LEGACY_LOGIN_SETTINGS)
    applied = []
    for name, value in defaults.items():
        if legacy and name in _LOGIN_SHAPE:
            continue
        if name in _MERGED_DICTS and hasattr(settings, name):
            current = dict(getattr(settings, name) or {})
            if "signup" not in current:
                current["signup"] = value["signup"]
                setattr(settings, name, current)
                applied.append(f"{name}[signup]")
            continue
        if not hasattr(settings, name):
            setattr(settings, name, value)
            applied.append(name)
    if not hasattr(settings, "ALLAUTH_TRUSTED_PROXY_COUNT"):
        # _trusted_proxy_count() coerces junk fail-safe (toward 0) and warns once.
        settings.ALLAUTH_TRUSTED_PROXY_COUNT = _trusted_proxy_count()
        applied.append("ALLAUTH_TRUSTED_PROXY_COUNT")
    return applied


def _allauth_modes() -> tuple[bool, bool]:
    """(socialaccount_only, headless_only) as allauth itself reads them."""
    from allauth import app_settings as allauth_settings

    return (
        bool(getattr(allauth_settings, "SOCIALACCOUNT_ONLY", False)),
        bool(getattr(allauth_settings, "HEADLESS_ONLY", False)),
    )


class AllauthBackend(AccountBackend):
    name = "allauth"
    features = frozenset(
        {"login", "logout", "signup", "verify_email", "password_reset", "social", "remember_me"}
    )

    def __init__(self, options: dict | None = None) -> None:
        super().__init__(options)
        social_only, headless_only = _allauth_modes()
        if headless_only:
            # allauth serves no HTML views; neither does djust.
            self.features = frozenset()
        elif social_only:
            # allauth removes local sign-up, email and password routes; so do we.
            self.features = self.features - {"signup", "verify_email", "password_reset"}
        connect_signal_bridge()

    def urlpatterns(self) -> list:
        from allauth.account import views as account_views

        # Stable djust_auth names on the same paths as allauth's own routes
        # (same views; the first match wins, so behaviour is identical). Only
        # for routes allauth itself serves in its current mode.
        patterns = []
        if self.supports("login"):
            patterns.append(path("login/", account_views.login, name="login"))
        if self.supports("logout"):
            patterns.append(path("logout/", account_views.logout, name="logout"))
        if self.supports("signup"):
            patterns.append(path("signup/", account_views.signup, name="signup"))
        if self.supports("verify_email"):
            patterns.append(
                path("confirm-email/", account_views.email_verification_sent, name="verify")
            )
        if self.supports("password_reset"):
            patterns.append(
                path("password/reset/", account_views.password_reset, name="password_reset")
            )
        # No ``social_login`` alias: each provider's login URL differs and is
        # carried on ``auth.providers[*].login_url`` (Provider.login_url).
        return patterns

    def extra_urlpatterns(self) -> list:
        return [path("", include("allauth.urls"))]

    def providers(self, request: HttpRequest) -> list[Provider]:
        try:
            from allauth.socialaccount.adapter import get_adapter
        except ImportError:
            return []
        out = []
        try:
            for provider in get_adapter().list_providers(request):
                out.append(
                    Provider(
                        id=provider.id,
                        label=label_for(provider.id, str(provider.name)),
                        login_url=provider.get_login_url(request),
                    )
                )
        except Exception:  # noqa: BLE001 - a broken provider must not break the login page
            logger.exception("Listing allauth social providers failed; showing none")
            return []
        return out


def connect_signal_bridge() -> None:
    """Re-send allauth's signals as djust's backend-neutral ones (idempotent)."""
    try:
        from allauth.account import signals as allauth_signals
    except ImportError:
        return
    from djust.auth import signals

    def _confirmed(sender: Any, request: HttpRequest, email_address: Any, **kwargs: Any) -> None:
        signals.email_verified.send(
            sender=AllauthBackend,
            request=request,
            user=email_address.user,
            email=email_address.email,
        )

    def _signed_up(sender: Any, request: HttpRequest, user: Any, **kwargs: Any) -> None:
        signals.user_signed_up.send(sender=AllauthBackend, request=request, user=user)

    allauth_signals.email_confirmed.connect(
        _confirmed, weak=False, dispatch_uid="djust_auth_email_confirmed"
    )
    allauth_signals.user_signed_up.connect(
        _signed_up, weak=False, dispatch_uid="djust_auth_user_signed_up"
    )
