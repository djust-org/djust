"""The allauth classes djust plugs in through allauth's own settings (ADR-039).

Kept apart from ``backends/allauth.py`` on purpose: importing this module loads
allauth's models, so only allauth's settings strings (``ACCOUNT_ADAPTER``,
``ACCOUNT_FORMS``, ``SOCIALACCOUNT_ADAPTER``, ``SOCIALACCOUNT_FORMS``) reference
it. A project on another backend never imports it, even when allauth is
installed but not configured.
"""

from __future__ import annotations

from typing import Any

from allauth.account.adapter import DefaultAccountAdapter
from allauth.account.forms import SignupForm as _AccountSignupForm
from allauth.core import context
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.forms import SignupForm as _SocialSignupForm
from django.core.exceptions import ValidationError
from django.http import HttpRequest
from django.utils.http import url_has_allowed_host_and_scheme

from ..registry import get_account_backend


def _run_validators(form: Any, source: str) -> None:
    data = {**form.cleaned_data, "source": source}
    try:
        get_account_backend().run_signup_validators(context.request, data)
    except ValidationError as exc:
        form.add_error(None, exc)


class DjustAccountAdapter(DefaultAccountAdapter):
    """Sign-up gate from the djust backend, and strict redirects."""

    def is_open_for_signup(self, request: HttpRequest) -> bool:
        return bool(get_account_backend().is_open_for_signup(request))

    def is_safe_url(self, url: str) -> bool:
        """Only same-host redirects, plus hosts listed in ``OPTIONS["redirect_hosts"]``.

        allauth's default also trusts every host ``ALLOWED_HOSTS`` matches, so
        ``ALLOWED_HOSTS = ["*"]`` (or a wildcard like ``.example.app`` whose
        subdomains users control) turns ``?next=`` into an open redirect.
        """
        request = context.request
        allowed = set(get_account_backend().options.get("redirect_hosts", []))
        secure = False
        if request is not None:
            allowed.add(request.get_host())
            secure = request.is_secure()
        return url_has_allowed_host_and_scheme(url, allowed_hosts=allowed, require_https=secure)


class DjustSignupForm(_AccountSignupForm):
    """allauth's sign-up form plus the backend's ``signup_validators`` (``source="form"``)."""

    def clean(self) -> dict:
        cleaned: dict = super().clean()
        _run_validators(self, "form")
        return cleaned


class DjustSocialSignupForm(_SocialSignupForm):
    """allauth's "finish signing up" form for social sign-in, plus ``signup_validators`` (``source="social"``)."""

    def clean(self) -> dict:
        cleaned: dict = super().clean()
        _run_validators(self, "social")
        return cleaned


class DjustSocialAccountAdapter(DefaultSocialAccountAdapter):
    """Runs ``signup_validators`` before a social sign-in creates an account on its own.

    When a validator refuses, auto-signup is declined and allauth shows the
    "finish signing up" form, which runs the validators again and explains why.
    """

    def is_auto_signup_allowed(self, request: HttpRequest, sociallogin: Any) -> bool:
        if not super().is_auto_signup_allowed(request, sociallogin):
            return False
        user = sociallogin.user
        data = {
            "email": getattr(user, "email", "") or "",
            "username": getattr(user, user.USERNAME_FIELD, "") or "",
            "source": "social",
        }
        try:
            get_account_backend().run_signup_validators(request, data)
        except ValidationError:
            return False
        return True
