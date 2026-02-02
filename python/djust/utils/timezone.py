"""
Timezone utilities for converting datetimes to the client's browser timezone.
"""

import datetime
from zoneinfo import ZoneInfo

from django.utils import timezone as django_tz


def to_client_tz(dt, view_or_tz=None):
    """
    Convert a datetime to the client's timezone.

    Args:
        dt: A datetime object (naive or aware).
        view_or_tz: A LiveView instance (with client_timezone attr),
                     an IANA timezone string, or None for server default.

    Returns:
        An aware datetime in the client's timezone,
        or the server timezone if client TZ is not available.
    """
    if dt is None:
        return None

    # Resolve the target timezone
    tz_str = None
    if isinstance(view_or_tz, str):
        tz_str = view_or_tz
    elif hasattr(view_or_tz, "client_timezone"):
        tz_str = view_or_tz.client_timezone

    if tz_str:
        try:
            target_tz = ZoneInfo(tz_str)
        except (KeyError, Exception):
            target_tz = django_tz.get_current_timezone()
    else:
        target_tz = django_tz.get_current_timezone()

    # Make naive datetimes aware (assume server timezone)
    if dt.tzinfo is None:
        dt = django_tz.make_aware(dt, django_tz.get_current_timezone())

    return dt.astimezone(target_tz)
