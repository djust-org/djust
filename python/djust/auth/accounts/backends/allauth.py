"""The ``allauth`` account backend: django-allauth's flows, skinned by the djust kit (ADR-039).

allauth keeps its own views and page templates (they carry real logic:
code flows, reauthentication, conditional fields). djust restyles every one
of them through allauth's supported override API, ``allauth/layouts/*.html``
and ``allauth/elements/*.html``, shipped in ``djust.auth``'s templates, so
``djust.auth`` must come before ``allauth`` in ``INSTALLED_APPS``.

Secure defaults (applied only where the project hasn't set the allauth
setting itself): mandatory verification by code, no verification on GET,
reset by code, logout by POST only, remember-me honoured, and allauth's
client-IP proxy count derived from ``DJUST_TRUSTED_PROXY_COUNT``.
"""

from __future__ import annotations

import logging
from typing import Any

from django.core.exceptions import ValidationError
from django.http import HttpRequest
from django.urls import include, path

from djust._client_ip import _trusted_proxy_count

from ..base import AccountBackend, Provider
from ..providers import label_for
from ..registry import get_account_backend

logger = logging.getLogger(__name__)

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
    "ACCOUNT_ADAPTER": "djust.auth.accounts.backends.allauth.DjustAccountAdapter",
    "ACCOUNT_FORMS": {"signup": "djust.auth.accounts.backends.allauth.DjustSignupForm"},
}


def apply_allauth_defaults(settings: Any, options: dict | None = None) -> list[str]:
    """Set each secure default the project hasn't set itself; return the names applied.

    ``options["verification"] == "link"`` keeps email verification by link
    instead of by code.
    """
    if options is None:
        from djust.config import get_djust_config

        spec = get_djust_config().get("ACCOUNTS") or {}
        options = (spec.get("OPTIONS") if isinstance(spec, dict) else None) or {}
    defaults = dict(SECURE_DEFAULTS)
    if options.get("verification") == "link":
        defaults["ACCOUNT_EMAIL_VERIFICATION_BY_CODE_ENABLED"] = False
    applied = []
    for name, value in defaults.items():
        if not hasattr(settings, name):
            setattr(settings, name, value)
            applied.append(name)
    if not hasattr(settings, "ALLAUTH_TRUSTED_PROXY_COUNT"):
        # _trusted_proxy_count() coerces junk fail-safe (toward 0) and warns once.
        settings.ALLAUTH_TRUSTED_PROXY_COUNT = _trusted_proxy_count()
        applied.append("ALLAUTH_TRUSTED_PROXY_COUNT")
    return applied


class AllauthBackend(AccountBackend):
    name = "allauth"
    features = frozenset(
        {"login", "logout", "signup", "verify_email", "password_reset", "social", "remember_me"}
    )

    def __init__(self, options: dict | None = None) -> None:
        super().__init__(options)
        connect_signal_bridge()

    def urlpatterns(self) -> list:
        from allauth.account import views as account_views

        # Stable djust_auth names on the same paths as allauth's own routes
        # (same views; the first match wins, so behaviour is identical).
        patterns = [
            path("login/", account_views.login, name="login"),
            path("logout/", account_views.logout, name="logout"),
            path("signup/", account_views.signup, name="signup"),
            path("confirm-email/", account_views.email_verification_sent, name="verify"),
            path("password/reset/", account_views.password_reset, name="password_reset"),
        ]
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


def _allauth_request() -> HttpRequest | None:
    from allauth.core import context

    return context.request


try:
    from allauth.account.adapter import DefaultAccountAdapter
    from allauth.account.forms import SignupForm as _AllauthSignupForm
except ImportError:  # pragma: no cover - allauth is an optional dependency
    DefaultAccountAdapter = object  # type: ignore[assignment,misc]
    _AllauthSignupForm = None  # type: ignore[assignment,misc]


class DjustAccountAdapter(DefaultAccountAdapter):  # type: ignore[misc,valid-type]
    """allauth adapter that asks the djust account backend whether signup is open."""

    def is_open_for_signup(self, request: HttpRequest) -> bool:
        return bool(get_account_backend().is_open_for_signup(request))


if _AllauthSignupForm is not None:

    class DjustSignupForm(_AllauthSignupForm):  # type: ignore[misc,valid-type]
        """allauth's signup form, plus the djust backend's ``signup_validators``."""

        def clean(self) -> dict:
            cleaned: dict = super().clean()
            request = _allauth_request()
            try:
                get_account_backend().run_signup_validators(request, cleaned)  # type: ignore[arg-type]
            except ValidationError as exc:
                self.add_error(None, exc)
            return cleaned


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
