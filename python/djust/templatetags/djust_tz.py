"""
Template filters for rendering datetimes in the client's browser timezone.

Usage:
    {% load djust_tz %}
    {{ timestamp|localtime_for_client }}
    {{ timestamp|client_time:"h:i A" }}

Requires that the LiveView has `client_timezone` set (auto-detected from browser).
Falls back to server timezone if not available.
"""

from django import template
from django.utils.dateformat import format as django_date_format

from ..utils.timezone import to_client_tz

register = template.Library()


def _get_client_tz(context):
    """Extract client timezone from template context (set by LiveView)."""
    # The view instance is typically available as 'view' in context
    view = context.get("view")
    if view and hasattr(view, "client_timezone"):
        return view.client_timezone
    # Also check for explicit client_timezone in context
    return context.get("client_timezone")


@register.filter(name="localtime_for_client", needs_autoescape=False)
def localtime_for_client(value):
    """
    Convert a datetime to the client's timezone.

    This filter version works without context — it just converts to server TZ.
    Use the {% client_localtime %} tag for full client TZ support,
    or pass client_timezone explicitly in your view's get_context_data.
    """
    if value is None:
        return ""
    return to_client_tz(value)


@register.filter(name="client_time", needs_autoescape=False)
def client_time(value, fmt="N j, Y, P"):
    """
    Format a datetime in the client's timezone with a custom format string.

    Without context access, falls back to server timezone.
    Use the {% client_time_tag %} for full client TZ support.

    Format uses Django's date format characters (same as the |date filter).
    """
    if value is None:
        return ""
    converted = to_client_tz(value)
    return django_date_format(converted, fmt)


# Simple tags that have access to context for full client_timezone support

@register.simple_tag(takes_context=True)
def client_localtime(context, dt):
    """
    Convert a datetime to the client's timezone (context-aware).

    Usage: {% client_localtime my_datetime as local_dt %}
    """
    tz_str = _get_client_tz(context)
    return to_client_tz(dt, tz_str)


@register.simple_tag(takes_context=True)
def client_time_tag(context, dt, fmt="N j, Y, P"):
    """
    Format a datetime in the client's timezone (context-aware).

    Usage: {% client_time_tag my_datetime "h:i A" %}
    """
    tz_str = _get_client_tz(context)
    converted = to_client_tz(dt, tz_str)
    return django_date_format(converted, fmt)
