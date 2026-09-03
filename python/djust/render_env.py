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
import threading
from typing import Any, Optional, Set

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

    **TWO formats are pushed, not one** (#2266). Django's ``floatformat`` has a
    ``u`` suffix meaning ``use_l10n=False``, and ``formats.get_format`` short-
    circuits on that flag *before* it consults the active language::

        if use_l10n is False:
            return getattr(settings, format_type)   # the RAW setting

    So ``u`` formats through ``settings.DECIMAL_SEPARATOR`` /
    ``THOUSAND_SEPARATOR`` / ``NUMBER_GROUPING`` directly. Neither triple can be
    derived from the other — under ``de`` the localized separator is ``,`` and
    the raw one is ``.``; under ``DECIMAL_SEPARATOR="!"`` with English it is the
    other way round — so both are resolved here and pushed together. Measured:
    with ``DECIMAL_SEPARATOR="!"`` and ``Decimal("6666.6666")``, Django renders
    ``{{ p|floatformat:"2u" }}`` as ``6666!67``.
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
        # Django's own ``use_l10n=False`` read — ``getattr(settings, ...)``, not
        # ``get_format``, because ``get_format`` would re-consult the locale.
        raw_decimal_sep = settings.DECIMAL_SEPARATOR
        raw_thousand_sep = settings.THOUSAND_SEPARATOR
        raw_grouping = settings.NUMBER_GROUPING
    except Exception:  # pragma: no cover - settings access is defensive
        logger.debug("[djust] number-format read failed; leaving numbers unlocalized")
        return

    set_number_format(
        str(decimal_sep),
        str(thousand_sep),
        _grouping_intervals(grouping),
        use_grouping,
        str(raw_decimal_sep),
        str(raw_thousand_sep),
        _grouping_intervals(raw_grouping),
    )


def _grouping_intervals(grouping: object) -> list[int]:
    """Django's ``NUMBER_GROUPING`` as ``numberformat.format`` consumes it.

    Django allows a sequence here (Indian grouping is ``[3, 2, 0]``); a scalar
    becomes ``[n, 0]``, matching ``numberformat.format``'s own
    ``intervals = [grouping, 0]`` fallback. Shared by the localized and the raw
    read so the two cannot normalize differently (#1646).
    """
    if isinstance(grouping, (list, tuple)):
        return [int(g) for g in grouping]
    return [int(grouping), 0]  # type: ignore[call-overload]


def apply_resolve_lazy() -> None:
    """Push ADR-027's lazy-resolution flag to Rust for the calling thread (#2539).

    The third ambient setting, and the one whose home this module already
    argued for. It has to be legible in TWO places in Rust — the conversion
    (``impl FromPyObject for Value``, which decides whether an ordinary object
    crosses as itself and carries a live handle) and the resolver
    (``Context::resolve_without_builtins``, which decides whether a dotted
    lookup walks that handle). The first has no ``Context`` and no config
    parameter to thread one through, so a per-``Context`` flag could not reach
    it; one thread-local read from both sites is one mechanism rather than two
    seeded from one reader (#1646).

    Default **OFF**, both here and in Rust.

    **A failed read pushes OFF rather than leaving the previous value**, and
    that is the one place this differs from the timezone and number-format
    handoffs above. Those two describe how a value is FORMATTED, so keeping the
    last good format through a transient settings failure is the conservative
    answer. This one selects which RESOLUTION MECHANISM runs, and the thread is
    reused: leaving it alone would let a render whose config read failed
    silently inherit the previous render's mechanism — the opposite of a
    kill-switch. Pushed OFF, a failed read gets the shipped default, which is
    the behaviour a project that never heard of ADR-027 has.

    **The thread-local is SET, not scoped.** Nothing restores it at the end of
    a render, so a thread keeps the last value pushed. Every framework entry
    (``RustBridgeMixin``, ``SimpleLiveView``, ``DjustTemplateBackend``, and the
    component path through :func:`apply_render_env_once`) pushes on each
    render, so they always reset it. The exception is a caller reaching
    ``_rust.render_template`` / ``render_template_with_dirs`` DIRECTLY: it
    inherits whatever the thread last rendered with, which for a fresh thread
    is the Rust default OFF and for a reused one is the previous framework
    render's setting.
    """
    try:
        from ._rust import set_resolve_lazy
    except ImportError:  # pragma: no cover - Rust build predates #2539
        return
    try:
        from .config import template_resolve_lazy_enabled

        enabled = template_resolve_lazy_enabled()
    except Exception:  # pragma: no cover - config access is defensive
        logger.debug("[djust] resolve-lazy read failed; forcing ADR-027 resolution OFF")
        enabled = False
    set_resolve_lazy(enabled)


#: Threads that have pushed the render env at least once. See
#: :func:`apply_render_env_once`.
_PUSHED = threading.local()


def apply_render_env_once() -> None:
    """:func:`apply_render_env`, but at most once per thread.

    For the COMPONENT path, which is the one entry that can run many times
    inside a single parent render — once per component instance. The three
    top-level entries push unconditionally on every render, so a component
    nested inside one already has correct thread-locals and a second push per
    instance buys nothing: measured at ~12us against a ~15us small render, an
    N+1 on a component-heavy page.

    What the first push still buys, and why this is not simply removed: a
    ``Component`` rendered OUTSIDE any djust render — from a plain Django view,
    or as the first thing a fresh ``sync_to_async`` worker thread does — has no
    enclosing render to inherit from, and before #2539 it rendered UTC
    timestamps, unlocalized numbers and a stale ADR-027 flag.

    The sentinel is never cleared, and that is the deliberate limit: after the
    first push this path trusts the enclosing render. A component rendered
    outside any render LATER on the same thread, under settings that have since
    changed, keeps the previous values — which is exactly the position the
    other three entries were always in for their nested calls, and strictly
    better than the pre-#2539 behaviour of never pushing at all.
    """
    if getattr(_PUSHED, "done", False):
        return
    _PUSHED.done = True
    apply_render_env()


def install_scope_hooks() -> bool:
    """Install the ``{% language %}`` / ``{% timezone %}`` hook pairs (#2558).

    The switch MUST happen in Python's thread-locals — a bridged
    ``{% translate %}`` inside the block reads
    ``translation.get_language()`` here, and ``get_current_timezone`` reads
    ``timezone._active`` — so the Rust scope nodes cross INTO Python to
    enter/exit rather than keeping a Rust-side copy (the #1646 parallel
    path). Each half re-pushes the render env afterwards, so the Rust
    locale/zone state follows the switch and is restored after — which is
    what makes ``{{ n }}`` inside ``{% language "de" %}`` format as ``de``
    while the outer render stays put.

    Idempotent in effect (re-registering the same closures); ``False``
    without the Rust extension.
    """
    try:
        from djust._rust import (
            register_language_scope_hooks,
            register_timezone_scope_hooks,
        )
    except ImportError:  # pragma: no cover - Rust build predates #2558
        return False
    register_language_scope_hooks(language_scope_enter, language_scope_exit)
    register_timezone_scope_hooks(timezone_scope_enter, timezone_scope_exit)
    return True


# The four scope hooks (#2558), module-level so a caller that re-asserts them
# (the filter-parity differential's entry-point ledger) registers the SAME
# objects ``install_scope_hooks`` does rather than a copy that could drift
# (#1646). Each is the ``enter(arg) -> token`` / ``exit(token)`` pair the Rust
# scope node calls around its body — on the error path too.


def _stamp_hook_error(exc: BaseException) -> None:
    """Mark an exception raised inside a scope hook as user-raised, so it
    crosses back WHOLE with its type (``ZoneInfoNotFoundError`` for
    ``{% timezone "Bogus/Zone" %}``) instead of being re-wrapped as a bare
    ``Exception`` by ``DjustTemplate.render`` — the #2547 contract for a
    bridged library tag's exception, applied to the scope nodes."""
    from .template_libraries import _stamp

    _stamp(exc)


def language_scope_enter(lang: Optional[str]) -> Any:
    """Enter ``{% language lang %}``: switch Django's thread-local and re-push
    the render env, so the Rust locale state follows the switch.

    ``lang`` arrives VERBATIM — ``None`` for a ``None`` operand, ``""`` for a
    missing variable — because ``translation.override(None)`` deactivates
    (``get_language()`` becomes ``None``) while ``override("")`` activates
    the fallback language; Django's ``LanguageNode`` makes the same call
    with the same value, and collapsing the two would render ``[None]``
    where Django renders ``[en-us]`` (measured, 5.2.16).
    """
    from django.utils import translation

    override = translation.override(lang)
    try:
        override.__enter__()
    except BaseException as exc:
        _stamp_hook_error(exc)
        raise
    apply_render_env()
    return override


def language_scope_exit(token: Any) -> None:
    """Leave ``{% language %}``: restore the thread-local, re-push the env."""
    token.__exit__(None, None, None)
    apply_render_env()


def timezone_scope_enter(name: Optional[str]) -> Any:
    """Enter ``{% timezone name %}``: same shape as the language pair, and
    the same verbatim operand — ``override(None)`` deactivates, ``override("")``
    raises Django's own ``ValueError``, an unknown zone its
    ``ZoneInfoNotFoundError``; each crosses back with its type."""
    from django.utils import timezone as dj_timezone

    override = dj_timezone.override(name)
    try:
        override.__enter__()
    except BaseException as exc:
        _stamp_hook_error(exc)
        raise
    apply_render_env()
    return override


def timezone_scope_exit(token: Any) -> None:
    """Leave ``{% timezone %}``: restore the thread-local, re-push the env."""
    token.__exit__(None, None, None)
    apply_render_env()


def apply_render_env() -> None:
    """Push every per-render Django setting Rust needs, for this thread.

    One entry point so a render path cannot pick up the timezone and miss the
    number format (#1646) or ADR-027's resolution flag (#2539). Every render
    path calls this and only this; the structural test in
    ``test_timezone_render_2209.py`` pins that caller set.
    """
    apply_active_timezone()
    apply_number_format()
    apply_resolve_lazy()
