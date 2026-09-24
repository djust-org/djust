"""The ``auth`` object every account page renders from (ADR-039).

Built by ``{% auth_context as auth %}``, so no context processor is needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from django.http import HttpRequest
from django.urls import NoReverseMatch, reverse
from django.utils.http import url_has_allowed_host_and_scheme

from .base import Provider
from .registry import get_account_backend

# feature -> stable URL name(s) exposed in ``auth.links``
_LINKS = {
    "login": ("login",),
    "logout": ("logout",),
    "signup": ("signup",),
    "password_reset": ("password_reset",),
}


@dataclass
class AuthContext:
    """What an account page knows: step, form, providers, a safe ``next``, links, features, errors."""

    step: str = ""
    form: Any = None
    providers: list[Provider] = field(default_factory=list)
    next: str = ""
    links: dict[str, str] = field(default_factory=dict)
    features: frozenset[str] = frozenset()
    errors: list[str] = field(default_factory=list)
    signup_open: bool = False


def safe_next(request: Optional[HttpRequest]) -> str:
    """The request's ``next`` if it stays on this host, else ``""``."""
    if request is None:
        return ""
    candidate = request.POST.get("next") or request.GET.get("next") or ""
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return candidate
    return ""


def build_auth(request: Optional[HttpRequest], form: Any = None, step: str = "") -> AuthContext:
    """Assemble the ``auth`` object for one page render."""
    backend = get_account_backend()
    links: dict[str, str] = {}
    for feature, names in _LINKS.items():
        if not backend.supports(feature):
            continue
        for name in names:
            try:
                links[name] = reverse(f"djust_auth:{name}")
            except NoReverseMatch:
                pass
    providers = (
        backend.providers(request) if (request is not None and backend.supports("social")) else []
    )
    errors = (
        [str(e) for e in form.non_field_errors()]
        if form is not None and hasattr(form, "non_field_errors")
        else []
    )
    signup_open = backend.supports("signup") and (
        request is None or backend.is_open_for_signup(request)
    )
    return AuthContext(
        step=step,
        form=form,
        providers=list(providers),
        next=safe_next(request),
        links=links,
        features=backend.features,
        errors=errors,
        signup_open=signup_open,
    )
