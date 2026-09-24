"""The account-backend contract (ADR-039).

A backend owns the account *flows* (log in, sign up, verify an email, reset
a password, social sign-in) and their views; every backend renders through
the shared ``djust_auth`` page kit, so swapping backends never touches a
template and overriding a template never touches the backend.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest

#: Every feature a backend may declare. Pages and links render only the
#: features the active backend supports.
FEATURES = frozenset(
    {"login", "logout", "signup", "verify_email", "password_reset", "social", "remember_me"}
)

SignupValidator = Callable[[HttpRequest, dict], None]


@dataclass(frozen=True)
class Provider:
    """A sign-in provider shown on the login and signup pages."""

    id: str
    label: str
    login_url: str
    icon: str = ""


class AccountBackend:
    """Base class for account backends.

    Extend a built-in backend by subclassing it and overriding a hook, or
    implement one from scratch: declare ``features`` and return the views
    from ``urlpatterns()`` under the stable ``djust_auth`` URL names.
    """

    #: Short name, used in logs and by ``DJUST_CONFIG["ACCOUNTS"]["BACKEND"]`` aliases.
    name: str = "base"
    #: The features this backend supports (a subset of :data:`FEATURES`).
    features: frozenset[str] = frozenset()
    #: Callables ``(request, cleaned_data) -> None`` run before an account is
    #: created; raise ``django.core.exceptions.ValidationError`` to refuse it.
    signup_validators: list[SignupValidator] = []

    def __init__(self, options: Optional[dict] = None) -> None:
        unknown = set(self.features) - FEATURES
        if unknown:
            raise ImproperlyConfigured(
                f"{type(self).__name__}.features has unknown feature(s) {sorted(unknown)}; "
                f"known features are {sorted(FEATURES)}."
            )
        self.options: dict = dict(options or {})
        self.signup_validators = list(type(self).signup_validators) + list(
            self.options.get("signup_validators", [])
        )

    def supports(self, feature: str) -> bool:
        """True when this backend implements ``feature``."""
        return feature in self.features

    def urlpatterns(self) -> list:
        """The views for each supported flow, named with the stable ``djust_auth`` names."""
        raise NotImplementedError(f"{type(self).__name__} must implement urlpatterns()")

    def extra_urlpatterns(self) -> list:
        """Routes mounted outside the ``djust_auth`` namespace (e.g. a library's own URLs)."""
        return []

    def providers(self, request: HttpRequest) -> list[Provider]:
        """Social sign-in providers to offer on this request."""
        return []

    def is_open_for_signup(self, request: HttpRequest) -> bool:
        """Whether new accounts can be created right now."""
        return True

    def client_ip(self, request: HttpRequest) -> Optional[str]:
        """The visitor's IP, honouring ``DJUST_TRUSTED_PROXY_COUNT``."""
        from djust._client_ip import resolve_client_ip

        return resolve_client_ip(
            request.META.get("HTTP_X_FORWARDED_FOR"), request.META.get("REMOTE_ADDR")
        )

    def run_signup_validators(self, request: HttpRequest, cleaned_data: dict) -> None:
        """Run every signup validator in order; the first ``ValidationError`` stops signup."""
        for validator in self.signup_validators:
            validator(request, cleaned_data)
