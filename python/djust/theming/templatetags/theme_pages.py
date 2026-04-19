"""
Page-level template tags for auth, error, and utility pages.

Each tag renders a complete page fragment (not a full HTML document) that
composes existing components (card, input, button) with theme-aware styling.
Use inside your own base template or the centered layout::

    {% extends "djust_theming/layouts/centered.html" %}
    {% load theme_pages %}
    {% block centered_content %}
        {% theme_login_page action="/auth/login/" %}
    {% endblock %}

Template resolution supports theme-specific overrides via:

    djust_theming/themes/{theme_name}/pages/{page}.html

Falling back to:

    djust_theming/pages/{page}.html
"""

from django import template
from django.middleware.csrf import get_token
from django.utils.safestring import mark_safe

from ..manager import get_theme_config
from ..template_resolver import resolve_page_template

register = template.Library()


def _css_prefix() -> str:
    """Return the current css_prefix from theme config."""
    return get_theme_config().get("css_prefix", "")


def _extract_slots(attrs: dict) -> tuple[dict, dict]:
    """Separate slot_* keys from regular attrs."""
    slots = {}
    remaining = {}
    for k, v in attrs.items():
        if k.startswith("slot_"):
            slots[k] = v
        else:
            remaining[k] = v
    return slots, remaining


def _csrf_token_value(request) -> str:
    """Return the CSRF token string for the given request, or empty string."""
    if request is None:
        return ""
    try:
        return get_token(request)
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Auth pages
# ---------------------------------------------------------------------------


@register.simple_tag(takes_context=True)
def theme_login_page(
    context,
    action: str = "",
    title: str = "Sign in",
    forgot_password_url: str = "",
    register_url: str = "",
    **attrs,
):
    """
    Render a themed login page fragment.

    Args:
        action: Form action URL
        title: Page heading
        forgot_password_url: URL for "Forgot password?" link
        register_url: URL for "Register" link
        **attrs: slot_social, slot_footer, class, id, etc.

    Usage:
        {% theme_login_page action="/auth/login/" forgot_password_url="/reset/" register_url="/register/" %}
    """
    slots, remaining_attrs = _extract_slots(attrs)
    request = context.get("request")
    tmpl = resolve_page_template(request, "login")
    ctx = {
        "title": title,
        "action": action,
        "forgot_password_url": forgot_password_url,
        "register_url": register_url,
        "attrs": remaining_attrs,
        "css_prefix": _css_prefix(),
        "csrf_token": _csrf_token_value(request),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_register_page(
    context,
    action: str = "",
    title: str = "Create account",
    login_url: str = "",
    terms_url: str = "",
    **attrs,
):
    """
    Render a themed registration page fragment.

    Args:
        action: Form action URL
        title: Page heading
        login_url: URL for "Sign in" link
        terms_url: URL for terms of service
        **attrs: slot_footer, class, id, etc.

    Usage:
        {% theme_register_page action="/auth/register/" login_url="/login/" terms_url="/terms/" %}
    """
    slots, remaining_attrs = _extract_slots(attrs)
    request = context.get("request")
    tmpl = resolve_page_template(request, "register")
    ctx = {
        "title": title,
        "action": action,
        "login_url": login_url,
        "terms_url": terms_url,
        "attrs": remaining_attrs,
        "css_prefix": _css_prefix(),
        "csrf_token": _csrf_token_value(request),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_password_reset_page(
    context,
    action: str = "",
    title: str = "Reset password",
    description: str = "Enter your email address and we'll send you a link to reset your password.",
    login_url: str = "",
    **attrs,
):
    """
    Render a themed password reset page fragment.

    Args:
        action: Form action URL
        title: Page heading
        description: Help text below the heading
        login_url: URL for "Back to login" link
        **attrs: class, id, etc.

    Usage:
        {% theme_password_reset_page action="/auth/reset/" login_url="/login/" %}
    """
    slots, remaining_attrs = _extract_slots(attrs)
    request = context.get("request")
    tmpl = resolve_page_template(request, "password_reset")
    ctx = {
        "title": title,
        "action": action,
        "description": description,
        "login_url": login_url,
        "attrs": remaining_attrs,
        "css_prefix": _css_prefix(),
        "csrf_token": _csrf_token_value(request),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_password_confirm_page(
    context,
    action: str = "",
    title: str = "Set new password",
    description: str = "Choose a strong password for your account.",
    **attrs,
):
    """
    Render a themed password confirmation page fragment.

    Args:
        action: Form action URL
        title: Page heading
        description: Help text below the heading
        **attrs: class, id, etc.

    Usage:
        {% theme_password_confirm_page action="/auth/confirm/" %}
    """
    slots, remaining_attrs = _extract_slots(attrs)
    request = context.get("request")
    tmpl = resolve_page_template(request, "password_confirm")
    ctx = {
        "title": title,
        "action": action,
        "description": description,
        "attrs": remaining_attrs,
        "css_prefix": _css_prefix(),
        "csrf_token": _csrf_token_value(request),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))


# ---------------------------------------------------------------------------
# Error pages
# ---------------------------------------------------------------------------


@register.simple_tag(takes_context=True)
def theme_404_page(
    context,
    title: str = "Page not found",
    description: str = "Sorry, the page you're looking for doesn't exist or has been moved.",
    home_url: str = "/",
    **attrs,
):
    """
    Render a themed 404 error page fragment.

    Args:
        title: Page heading
        description: Description text
        home_url: URL for the "Go home" button
        **attrs: slot_illustration, class, id, etc.

    Usage:
        {% theme_404_page home_url="/" %}
    """
    slots, remaining_attrs = _extract_slots(attrs)
    request = context.get("request")
    tmpl = resolve_page_template(request, "404")
    ctx = {
        "title": title,
        "description": description,
        "home_url": home_url,
        "attrs": remaining_attrs,
        "css_prefix": _css_prefix(),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_500_page(
    context,
    title: str = "Something went wrong",
    description: str = "We're experiencing an internal server error. Please try again later.",
    home_url: str = "/",
    retry_url: str = "",
    **attrs,
):
    """
    Render a themed 500 error page fragment.

    Args:
        title: Page heading
        description: Description text
        home_url: URL for the "Go home" button
        retry_url: URL for the "Try again" button
        **attrs: class, id, etc.

    Usage:
        {% theme_500_page home_url="/" retry_url="/dashboard/" %}
    """
    slots, remaining_attrs = _extract_slots(attrs)
    request = context.get("request")
    tmpl = resolve_page_template(request, "500")
    ctx = {
        "title": title,
        "description": description,
        "home_url": home_url,
        "retry_url": retry_url,
        "attrs": remaining_attrs,
        "css_prefix": _css_prefix(),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_403_page(
    context,
    title: str = "Access denied",
    description: str = "You don't have permission to access this resource.",
    back_url: str = "/",
    **attrs,
):
    """
    Render a themed 403 error page fragment.

    Args:
        title: Page heading
        description: Description text
        back_url: URL for the "Go back" button
        **attrs: class, id, etc.

    Usage:
        {% theme_403_page back_url="/" %}
    """
    slots, remaining_attrs = _extract_slots(attrs)
    request = context.get("request")
    tmpl = resolve_page_template(request, "403")
    ctx = {
        "title": title,
        "description": description,
        "back_url": back_url,
        "attrs": remaining_attrs,
        "css_prefix": _css_prefix(),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))


# ---------------------------------------------------------------------------
# Utility pages
# ---------------------------------------------------------------------------


@register.simple_tag(takes_context=True)
def theme_maintenance_page(
    context,
    title: str = "Under maintenance",
    description: str = "We're performing scheduled maintenance. We'll be back shortly.",
    **attrs,
):
    """
    Render a themed maintenance page fragment.

    Args:
        title: Page heading
        description: Description text
        **attrs: slot_illustration, slot_eta, slot_progress, class, id, etc.

    Usage:
        {% theme_maintenance_page slot_eta="<p>Back at 2pm UTC</p>" %}
    """
    slots, remaining_attrs = _extract_slots(attrs)
    request = context.get("request")
    tmpl = resolve_page_template(request, "maintenance")
    ctx = {
        "title": title,
        "description": description,
        "attrs": remaining_attrs,
        "css_prefix": _css_prefix(),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))


@register.simple_tag(takes_context=True)
def theme_empty_state_page(
    context,
    title: str = "No items yet",
    description: str = "Get started by creating your first item.",
    cta_text: str = "",
    cta_url: str = "",
    **attrs,
):
    """
    Render a themed empty state page fragment.

    Args:
        title: Page heading
        description: Description text
        cta_text: Call-to-action button text
        cta_url: Call-to-action button URL
        **attrs: slot_icon, class, id, etc.

    Usage:
        {% theme_empty_state_page title="No projects" cta_text="New project" cta_url="/projects/new/" %}
    """
    slots, remaining_attrs = _extract_slots(attrs)
    request = context.get("request")
    tmpl = resolve_page_template(request, "empty_state")
    ctx = {
        "title": title,
        "description": description,
        "cta_text": cta_text,
        "cta_url": cta_url,
        "attrs": remaining_attrs,
        "css_prefix": _css_prefix(),
        **slots,
    }
    return mark_safe(tmpl.render(ctx))
