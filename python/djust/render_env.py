"""The single place per-render Django settings are handed to Rust (#2209, #2221).

Two ambient settings live here, and both were invisible to Rust:

* **Timezone** (#2209) — Django applies ``timezone.localtime()`` to an aware
  datetime before formatting it. The engine did no conversion, so under
  ``USE_TZ=True`` every timestamp came out in UTC.
* **Number format** (#2221) — Django localizes a number on its way into the
  page. The engine used Rust's defaults, so ``1234567`` rendered without
  separators in every locale, English included.

They are pushed together by :func:`apply_render_env`, so a render path cannot
acquire one and miss the other.

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


def apply_number_format() -> None:
    """Push the active locale's number format to Rust (#2221).

    The **inverse** of the timezone handoff above, deliberately. Timezone needed
    a self-contained database in Rust (``chrono-tz``) and nothing from Python
    but a zone name. Locale is defined by ``django/conf/locale/*/formats.py``,
    so reimplementing it in Rust would fork Django's data rather than use it —
    Python resolves three values per render and Rust only applies them.

    Note what is NOT read: ``USE_L10N``. It is inert in Django 5.2 — verified
    across the full ``USE_L10N`` x ``USE_THOUSAND_SEPARATOR`` x language matrix,
    where flipping it changed no output. Django 5.0 removed it as a toggle.
    Only the active language (decimal separator) and ``USE_THOUSAND_SEPARATOR``
    (grouping) matter, so reading ``USE_L10N`` would be cargo cult.
    """
    try:
        from ._rust import set_number_format
    except ImportError:  # pragma: no cover - Rust build predates #2221
        return
    try:
        from django.conf import settings
        from django.utils import formats

        decimal_sep = formats.get_format("DECIMAL_SEPARATOR")
        thousand_sep = formats.get_format("THOUSAND_SEPARATOR")
        grouping = formats.get_format("NUMBER_GROUPING")
        use_grouping = bool(getattr(settings, "USE_THOUSAND_SEPARATOR", False))
    except Exception:  # pragma: no cover - settings access is defensive
        logger.debug("[djust] number-format read failed; leaving numbers unlocalized")
        return

    # Django allows a sequence here (Indian grouping is ``[3, 2, 0]``); a scalar
    # becomes ``[n, 0]``, matching ``numberformat.format``'s own
    # ``intervals = [grouping, 0]`` fallback.
    if isinstance(grouping, (list, tuple)):
        groups = [int(g) for g in grouping]
    else:
        groups = [int(grouping), 0]

    set_number_format(str(decimal_sep), str(thousand_sep), groups, use_grouping)


def apply_render_env() -> None:
    """Push every per-render Django setting Rust needs, for this thread.

    One entry point so a render path cannot pick up the timezone and miss the
    number format (#1646). Both render paths call this and only this; the
    structural test in ``test_timezone_render_2209.py`` pins that caller set.
    """
    apply_active_timezone()
    apply_number_format()
