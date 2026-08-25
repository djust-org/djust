"""The single place the active Django timezone is handed to Rust (#2209).

Django applies ``timezone.localtime()`` to an aware datetime before formatting
it. The Rust template engine did no conversion at all, so under ``USE_TZ=True``
every rendered timestamp came out in UTC — off by the UTC offset in the
configuration ``djust new`` generates, since the scaffold sets ``USE_TZ = True``.

Why this is a module rather than a method
-----------------------------------------
There are two independent Python→Rust render paths, and they share no base
class: ``RustBridgeMixin`` (every ``LiveView``, via ``_sync_state_to_rust``) and
``SimpleLiveView``, which calls ``render_template_with_dirs`` directly. A method
on the mixin would have covered the first and silently missed the second —
exactly the parallel-path drift (#1646) that produced the family of bugs this
release keeps closing. One function both call cannot drift.

Why per render, and not once at startup
---------------------------------------
``timezone.activate()`` is per-request — the documented way to show each user
their own zone — and ``override_settings(TIME_ZONE=...)`` is per-test, while the
``RustLiveView`` is session-cached and outlives both. A zone captured at
``DjustConfig.ready()``, or once per view instance, would be stale for exactly
the case users care about. The cost is two attribute reads and a thread-local
store; Django resolves the name from a thread-local it already maintains.
"""

import logging
from typing import Optional, Set

logger = logging.getLogger(__name__)

# Zone names the bundled tz database rejected, so the warning fires once per
# process rather than once per render. A misconfigured ``TIME_ZONE`` would
# otherwise emit a log line on every event for the life of the connection.
_UNKNOWN_TIMEZONES_WARNED: Set[Optional[str]] = set()


def apply_active_timezone() -> None:
    """Push the active zone to Rust for the calling thread.

    ``None`` when ``USE_TZ`` is false, which disables conversion — and it is
    pushed rather than skipped, because the thread is reused: a render that
    left a zone set would keep converting for a later ``USE_TZ=False`` one.

    A ``TIME_ZONE`` Rust does not recognise is logged once and left unconverted
    rather than raised — a settings typo should not take a page down, and Django
    itself would have rejected such a value long before here.
    """
    try:
        from ._rust import set_active_timezone
    except ImportError:  # pragma: no cover - Rust build predates #2209
        return
    try:
        from django.conf import settings
        from django.utils import timezone as dj_timezone

        name = dj_timezone.get_current_timezone_name() if settings.USE_TZ else None
    except Exception:  # pragma: no cover - settings access is defensive
        logger.debug("[djust] timezone read failed; leaving conversion off")
        return
    if not set_active_timezone(name) and name not in _UNKNOWN_TIMEZONES_WARNED:
        _UNKNOWN_TIMEZONES_WARNED.add(name)
        logger.warning(
            "[djust] TIME_ZONE %r is not a zone the bundled tz database knows; "
            "timestamps will render unconverted",
            name,
        )
