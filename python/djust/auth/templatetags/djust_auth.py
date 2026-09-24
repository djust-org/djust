"""``{% load djust_auth %}``: components for account pages (ADR-039).

Every tag renders plain, escaped HTML that works without JavaScript;
``djust_auth/auth.js`` only enhances it. The one ``mark_safe`` is for the
static provider SVGs in ``djust.auth.accounts.providers``.
"""

from __future__ import annotations

from typing import Any

from django import template
from django.utils.safestring import SafeString, mark_safe

from djust.auth.accounts.context import AuthContext, build_auth
from djust.auth.accounts.providers import icon_for

register = template.Library()

_AUTOCOMPLETE = {
    "login": "username",
    "username": "username",
    "email": "email",
    "password": "current-password",
    "oldpassword": "current-password",
    "old_password": "current-password",
    "password1": "new-password",
    "password2": "new-password",
    "new_password1": "new-password",
    "new_password2": "new-password",
}


@register.simple_tag(takes_context=True)
def auth_context(context: Any) -> AuthContext:
    """``{% auth_context as auth %}``: the page's ``auth`` object."""
    return build_auth(context.get("request"), context.get("form"), context.get("auth_step", ""))


@register.filter
def auth_provider_icon(provider_id: str) -> SafeString:
    """The static inline SVG for a provider id (``""`` when unknown)."""
    return mark_safe(icon_for(str(provider_id)))  # noqa: S308 — static module constants only


@register.inclusion_tag("djust_auth/components/providers.html")
def auth_providers(auth: Any) -> dict:
    """Social sign-in buttons ("Continue with …" / "Sign up with …")."""
    return {
        "providers": getattr(auth, "providers", []),
        "step": getattr(auth, "step", ""),
        "next": getattr(auth, "next", ""),
    }


@register.inclusion_tag("djust_auth/components/divider.html")
def auth_divider(label: str = "or") -> dict:
    """A labelled divider between provider buttons and the email form."""
    return {"label": label}


def _widget_for(field: Any, extra: dict) -> str:
    attrs = dict(field.field.widget.attrs)
    attrs.update(extra)
    return field.as_widget(attrs=attrs)


@register.inclusion_tag("djust_auth/components/field.html")
def auth_field(field: Any) -> dict:
    """A labelled input with help, error and ARIA wiring; passwords get a show/hide button."""
    widget = field.field.widget
    is_password = getattr(widget, "input_type", "") == "password"
    extra = {}
    auto = _AUTOCOMPLETE.get(field.name)
    if auto and "autocomplete" not in widget.attrs:
        extra["autocomplete"] = auto
    described = []
    if field.help_text:
        described.append(f"{field.id_for_label}-help")
    if field.errors:
        extra["aria-invalid"] = "true"
        described.append(f"{field.id_for_label}-error")
    if described:
        extra["aria-describedby"] = " ".join(described)
    return {"field": field, "widget": _widget_for(field, extra), "is_password": is_password}


@register.inclusion_tag("djust_auth/components/field.html")
def auth_code_input(field: Any) -> dict:
    """A one-time-code input (numeric keypad, OTP autofill, paste-friendly)."""
    extra = {
        "inputmode": "numeric",
        "autocomplete": "one-time-code",
        "class": "dj-auth-input dj-auth-code",
    }
    if field.errors:
        extra["aria-invalid"] = "true"
        extra["aria-describedby"] = f"{field.id_for_label}-error"
    return {"field": field, "widget": _widget_for(field, extra), "is_password": False}


@register.inclusion_tag("djust_auth/components/errors.html")
def auth_errors(form: Any) -> dict:
    """A ``role=alert`` summary of form-level errors."""
    errors = (
        list(form.non_field_errors())
        if form is not None and hasattr(form, "non_field_errors")
        else []
    )
    return {"errors": errors}


@register.inclusion_tag("djust_auth/components/links.html")
def auth_links(auth: Any) -> dict:
    """Secondary links ("Forgot password?", "New here?") for supported features only."""
    links = dict(getattr(auth, "links", {}) or {})
    if not getattr(auth, "signup_open", False):
        links.pop("signup", None)
    return {"links": links, "step": getattr(auth, "step", "")}
