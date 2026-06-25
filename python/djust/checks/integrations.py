"""djust system checks — integration checks — service worker, hot-view, time-travel, admin, psycopg.

Split from the former monolithic ``checks.py`` (#1822). No behavior change.
"""

import logging
import re
from typing import Any, Optional

from django.core.checks import CheckMessage, register

from djust.checks.utils import (
    DjustError,
    DjustInfo,
    DjustWarning,
    _is_check_suppressed,
    _walk_subclasses,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Service Worker advanced features (C3xx) — v0.6.0
# ---------------------------------------------------------------------------


# Regex matching common PII / credential naming patterns on snapshot-opt-in
# views. Keep conservative — false positives (e.g. a benign ``token_count``
# counter) are easier for users to tolerate than silent misses on real
# credentials leaking into the client-side state cache.
_PII_NAME_PATTERN = re.compile(
    r"password|token|secret|api_?key|pii|ssn|credit_?card|cc_?num"
    r"|bearer|private_?key|auth_?header|sensitive|credential",
    re.IGNORECASE,
)


@register("djust")
def check_service_worker_advanced(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """Validate service-worker advanced-feature configuration (v0.6.0).

    Covers the VDOM-cache TTL / max-entries ranges and the per-view
    state-snapshot PII naming heuristic. These are configuration /
    guardrail checks — none of them block startup; security-critical
    state-snapshot behaviors (JSON-only, safe_setattr) are enforced at
    runtime in the websocket handler regardless of check outcome.
    """
    errors = []
    from django.conf import settings

    # Resolve config values. Prefer explicit top-level settings; fall
    # back to the nested LIVEVIEW_CONFIG['service_worker'] dict; finally
    # fall back to the defaults shipped in ``config.py``.
    liveview_cfg = getattr(settings, "LIVEVIEW_CONFIG", {}) or {}
    sw_cfg = liveview_cfg.get("service_worker", {}) if isinstance(liveview_cfg, dict) else {}

    ttl_seconds = getattr(
        settings,
        "DJUST_VDOM_CACHE_TTL_SECONDS",
        sw_cfg.get("vdom_cache_ttl_seconds", 1800),
    )
    max_entries = getattr(
        settings,
        "DJUST_VDOM_CACHE_MAX_ENTRIES",
        sw_cfg.get("vdom_cache_max_entries", 50),
    )
    vdom_enabled = getattr(
        settings,
        "DJUST_VDOM_CACHE_ENABLED",
        sw_cfg.get("vdom_cache_enabled", True),
    )

    # C301 — TTL must be positive.
    try:
        ttl_int = int(ttl_seconds)
    except (TypeError, ValueError):
        ttl_int = -1
    if ttl_int <= 0:
        errors.append(
            DjustError(
                "DJUST_VDOM_CACHE_TTL_SECONDS must be a positive integer.",
                hint=(
                    "TTL <= 0 disables expiry and would let the SW serve "
                    "indefinitely stale HTML on back-nav."
                ),
                id="djust.C301",
                fix_hint=(
                    "Set `DJUST_VDOM_CACHE_TTL_SECONDS = 1800` (30 minutes) "
                    "or remove the override to use the default."
                ),
            )
        )

    # C302 — max entries must be >= 1.
    try:
        max_int = int(max_entries)
    except (TypeError, ValueError):
        max_int = 0
    if max_int < 1:
        errors.append(
            DjustError(
                "DJUST_VDOM_CACHE_MAX_ENTRIES must be >= 1.",
                hint=(
                    "A max of 0 would evict every entry on insertion and "
                    "silently disable the cache."
                ),
                id="djust.C302",
                fix_hint=(
                    "Set `DJUST_VDOM_CACHE_MAX_ENTRIES = 50` or remove the "
                    "override to use the default."
                ),
            )
        )

    # C303 — informational when the operator explicitly disabled the cache.
    if not vdom_enabled and not _is_check_suppressed("djust.C303"):
        errors.append(
            DjustInfo(
                "DJUST_VDOM_CACHE_ENABLED is False; VDOM cache disabled.",
                hint=(
                    "Back-navigation will fall through to a fresh mount + "
                    "render instead of an instant paint. Suppress this "
                    "check with DJUST_CONFIG = {'suppress_checks': ['C303']}."
                ),
                id="djust.C303",
                fix_hint=(
                    "Remove the `DJUST_VDOM_CACHE_ENABLED = False` override "
                    "to re-enable, or suppress the check."
                ),
            )
        )

    # C304 — scan snapshot-opt-in views for attr names matching PII patterns.
    try:
        from djust.live_view import LiveView

        for cls in _walk_subclasses(LiveView):
            # Skip internal djust classes (tests/examples still checked).
            module = getattr(cls, "__module__", "") or ""
            if module.startswith("djust.") or module.startswith("djust_"):
                if "test" not in module and "example" not in module:
                    continue
            if not getattr(cls, "enable_state_snapshot", False):
                continue
            # Inspect both class-level attrs (defaults) and __init__ mount
            # attrs are unreachable statically — C304 scans class vars plus
            # any annotations the user declared.
            suspect_names = []
            for name in list(cls.__dict__.keys()) + list(
                getattr(cls, "__annotations__", {}).keys()
            ):
                if name.startswith("_"):
                    continue
                if _PII_NAME_PATTERN.search(name):
                    suspect_names.append(name)
            if suspect_names:
                cls_label = "%s.%s" % (cls.__module__, cls.__qualname__)
                errors.append(
                    DjustWarning(
                        "%s: enable_state_snapshot=True with PII-like "
                        "attribute names: %s" % (cls_label, ", ".join(sorted(set(suspect_names)))),
                        hint=(
                            "State snapshots are cached client-side by the "
                            "Service Worker. Attributes matching "
                            "password|token|secret|api_key|pii|ssn|"
                            "credit_card|bearer|private_key|auth_header|"
                            "sensitive|credential would be stored in "
                            "browser cache storage."
                        ),
                        id="djust.C304",
                        fix_hint=(
                            "Either rename the attributes, prefix them with "
                            "'_' to exclude from snapshots, or disable "
                            "enable_state_snapshot on this view."
                        ),
                    )
                )
    except ImportError:
        pass  # LiveView not importable (Rust extension missing) — skip scan.

    return errors


@register("djust")
def check_hot_view_replacement(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """C401 — Hot View Replacement requires ``watchdog`` for file watching.

    Only fires when the operator has explicitly opted into the dev-time
    HVR pipeline (``DEBUG=True`` + ``hot_reload=True`` +
    ``hvr_enabled=True``) but the underlying ``watchdog`` package isn't
    importable. In that case HVR would silently no-op; the warning nudges
    the developer to ``pip install watchdog`` so module reloads actually
    reach the WebSocket consumers.

    Silent in production: ``DEBUG=False`` suppresses the entire check
    block so release builds stay quiet.
    """
    from django.conf import settings

    warnings: list[CheckMessage] = []
    debug = bool(getattr(settings, "DEBUG", False))
    if not debug:
        return warnings

    try:
        from djust.config import config
    except ImportError:
        return warnings

    if not config.get("hvr_enabled", True):
        return warnings
    if not config.get("hot_reload", True):
        return warnings

    try:
        from djust.dev_server import WATCHDOG_AVAILABLE
    except ImportError:
        WATCHDOG_AVAILABLE = False

    if not WATCHDOG_AVAILABLE:
        warnings.append(
            DjustWarning(
                "Hot View Replacement is enabled but watchdog is not installed.",
                hint=(
                    "HVR requires the watchdog package for file watching. "
                    "Without it, code changes won't hot-swap live view "
                    "instances in development."
                ),
                fix_hint="pip install watchdog",
                id="djust.C401",
            )
        )
    return warnings


@register("djust")
def check_time_travel_debugging(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """C501/C502 — Time-travel debugging config validation.

    C501 (info) — surfaced when ``DEBUG=True`` AND the global
    ``time_travel_enabled`` config flag is on, as a breadcrumb that
    the feature is wired. Per-view opt-in is still required via
    ``LiveView.time_travel_enabled = True``.

    C502 (error) — fires when ``time_travel_max_events`` is <= 0,
    which would make the ring buffer raise on allocation.

    Silent in production: ``DEBUG=False`` suppresses both.
    """
    from django.conf import settings

    results: list[CheckMessage] = []
    debug = bool(getattr(settings, "DEBUG", False))
    if not debug:
        return results

    try:
        from djust.config import config
    except ImportError:
        return results

    max_events = config.get("time_travel_max_events", 100)
    if not isinstance(max_events, int) or max_events <= 0:
        results.append(
            DjustError(
                "time_travel_max_events must be a positive integer (got %r)." % (max_events,),
                hint=(
                    "The time-travel ring buffer raises ValueError when "
                    "the cap is non-positive, which breaks LiveView "
                    "__init__ for any view with time_travel_enabled=True."
                ),
                fix_hint=(
                    "Set LIVEVIEW_CONFIG['time_travel_max_events'] to a "
                    "positive int (default: 100)."
                ),
                id="djust.C502",
            )
        )

    if config.get("time_travel_enabled", False):
        results.append(
            DjustInfo(
                "Time-travel debugging is enabled globally "
                "(LIVEVIEW_CONFIG['time_travel_enabled']=True).",
                hint=(
                    "Individual views still require "
                    "``time_travel_enabled = True`` on the class to "
                    "allocate a buffer. This notice confirms the "
                    "global switch is on for discoverability."
                ),
                id="djust.C501",
            )
        )

    return results


# ---------------------------------------------------------------------------
# Admin widget checks (A072 / A073) -- djust.admin_ext per-page widget slots
# ---------------------------------------------------------------------------


@register("djust")
def check_admin_widgets(
    app_configs: Any, _admin_sites: Optional[Any] = None, **kwargs: Any
) -> list[CheckMessage]:
    """Audit widget-slot registrations on DjustModelAdmin subclasses.

    - **A072** (Warning): non-LiveView class registered in
      ``change_form_widgets`` / ``change_list_widgets``. Such widgets
      cannot be embedded via ``{% live_render %}`` and will raise at
      render time.
    - **A073** (Info): runtime-detectable notice that the in-process
      ``_JOBS`` registry backing ``BulkActionProgressWidget`` is
      single-worker-only. Only emitted when (a) a ``DjustAdminSite``
      has a registered admin action decorated with
      ``@admin_action_with_progress`` AND (b) the ``DJUST_ASGI_WORKERS``
      setting is greater than 1 (defaults to 1). In single-worker
      development this check stays silent so ``manage.py check`` has
      no noise. Multi-worker deploys must use sticky sessions or a
      single ASGI worker until v0.7.1 ships the channel-layer backend.

    The ``_admin_sites`` kwarg is for tests; production runs walk the
    default admin site registry.
    """
    results: list[CheckMessage] = []

    try:
        from djust.admin_ext import site as default_site
    except Exception:
        logger.debug("admin_ext not importable; skipping A072/A073", exc_info=True)
        return results

    # Lazy import — avoids a circular import when checks.py is loaded
    # during Django setup.
    try:
        from djust.live_view import LiveView
    except Exception:
        logger.debug("LiveView import failed; skipping admin widget audit", exc_info=True)
        return results

    sites = list(_admin_sites) if _admin_sites is not None else [default_site]

    # A073 is gated on DJUST_ASGI_WORKERS > 1. In single-worker dev (the
    # common case) the check stays silent; it only fires when the
    # developer has deliberately declared a multi-worker deploy.
    from django.conf import settings as _settings  # lazy import

    asgi_workers = getattr(_settings, "DJUST_ASGI_WORKERS", 1)
    try:
        asgi_workers = int(asgi_workers)
    except (TypeError, ValueError):
        asgi_workers = 1
    emit_a073 = asgi_workers > 1

    for site in sites:
        registry = getattr(site, "_registry", {}) or {}
        site_name = getattr(site, "name", "djust_admin")
        has_progress_action = False

        for model, model_admin in registry.items():
            model_label = "%s.%s" % (
                getattr(model._meta, "app_label", "?"),
                getattr(model._meta, "model_name", "?"),
            )

            for slot in ("change_form_widgets", "change_list_widgets"):
                widgets = getattr(model_admin, slot, []) or []
                for widget_cls in widgets:
                    if not (isinstance(widget_cls, type) and issubclass(widget_cls, LiveView)):
                        widget_name = getattr(widget_cls, "__name__", repr(widget_cls))
                        results.append(
                            DjustWarning(
                                (
                                    "Admin %s -- %s on %s contains non-LiveView class %r. "
                                    "Widget slots can only embed djust LiveView subclasses."
                                )
                                % (site_name, slot, model_label, widget_name),
                                hint=(
                                    "Make %s a subclass of djust.LiveView, or remove it from %s."
                                    % (widget_name, slot)
                                ),
                                id="djust.A072",
                                fix_hint=(
                                    "Change `class %s` to `class %s(LiveView):` or drop it from "
                                    "`%s` on the ModelAdmin for `%s`."
                                    % (widget_name, widget_name, slot, model_label)
                                ),
                            )
                        )

            # Scan actions for @admin_action_with_progress to drive A073.
            actions = getattr(model_admin, "actions", []) or []
            for action_name in actions:
                func = (
                    action_name
                    if callable(action_name)
                    else getattr(model_admin, str(action_name), None)
                )
                if func is not None and getattr(func, "_djust_admin_action_with_progress", False):
                    has_progress_action = True
                    break

        if has_progress_action and emit_a073:
            results.append(
                DjustInfo(
                    (
                        "Admin %s -- uses @admin_action_with_progress with "
                        "DJUST_ASGI_WORKERS=%d. The v0.7.0 BulkActionProgressWidget keeps "
                        "job state in a process-local dict (_JOBS); multi-worker deploys "
                        "must pin the progress URL to the worker that started the job "
                        "(sticky sessions) or run a single ASGI worker. v0.7.1 will back "
                        "this with a channel layer."
                    )
                    % (site_name, asgi_workers),
                    hint=(
                        "For v0.7.0, deploy with --workers 1 OR enable sticky sessions on "
                        "your load balancer. Unset DJUST_ASGI_WORKERS (or set it to 1) to "
                        "silence this check. See docs/website/guides/admin-widgets.md."
                    ),
                    id="djust.A073",
                )
            )

    return results


# ---------------------------------------------------------------------------
# Database-driver checks (D0xx)
# ---------------------------------------------------------------------------


def _parse_psycopg_version(version_str: str) -> tuple:
    """Parse a `major.minor[.patch[...]]` version string into a tuple of ints.

    Returns ``(0, 0)`` on anything we can't parse — the caller treats
    that as "couldn't determine version, don't make claims about it".
    """
    parts = []
    for chunk in version_str.split(".")[:2]:
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        if not digits:
            return (0, 0)
        parts.append(int(digits))
    while len(parts) < 2:
        parts.append(0)
    return tuple(parts[:2])


@register("djust")
def check_psycopg3_for_pg_notify(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """djust.D001 — warn when Postgres is configured but psycopg3 is missing.

    djust's ``db.notifications`` (LISTEN/NOTIFY bridge) requires
    ``psycopg[binary]>=3.2``. The 0.9.5 cycle hardened the runtime path
    to permanent-fail with a WARNING when ``@notify_on_save`` actually
    fires (#1357), but operators who deploy the misconfig and don't have
    any consumer running won't see that warning until much later.

    This check surfaces the misconfig at ``manage.py check`` /
    ``runserver`` startup, before traffic. It only emits when:

      1. ``DATABASES['default']['ENGINE']`` is the Postgres backend.
      2. The legacy ``psycopg2`` driver IS importable.
      3. ``psycopg`` (3.x) is NOT importable, OR is at version < 3.2.

    Scoped to ``"default"`` because that's where 99% of djust apps put
    their primary DB; multi-database setups can silence per-project via
    ``SILENCED_SYSTEM_CHECKS``.
    """
    results: list = []
    if _is_check_suppressed("djust.D001"):
        return results

    try:
        from django.conf import settings
    except Exception:
        return results

    databases = getattr(settings, "DATABASES", {}) or {}
    default = databases.get("default", {}) or {}
    engine = default.get("ENGINE", "") or ""

    # Only the postgres backend matters for LISTEN/NOTIFY.
    if engine != "django.db.backends.postgresql":
        return results

    # If psycopg2 is NOT importable AND psycopg is NOT importable,
    # Django itself raises an unrelated error — don't pile on.
    try:
        import psycopg2  # noqa: F401
    except ImportError:
        return results

    psycopg2_version = ""
    try:
        psycopg2_version = getattr(psycopg2, "__version__", "")  # noqa: F821
    except Exception:
        # getattr already supplies a "" default; this only fires on a
        # pathological __version__ descriptor. Keep the "unknown" fallback.
        psycopg2_version = ""

    psycopg3_version: str | None = None
    try:
        import psycopg as _psycopg3

        psycopg3_version = getattr(_psycopg3, "__version__", "0.0")
    except ImportError:
        psycopg3_version = None

    needs_warning = False
    if psycopg3_version is None:
        needs_warning = True
        detected_msg = "psycopg[binary]>=3.2 NOT installed"
    else:
        major_minor = _parse_psycopg_version(psycopg3_version)
        if major_minor < (3, 2):
            needs_warning = True
            detected_msg = f"psycopg installed but at version {psycopg3_version} (need >= 3.2)"

    if not needs_warning:
        return results

    psycopg2_label = (
        f"psycopg2 (legacy driver) at version {psycopg2_version}"
        if psycopg2_version
        else "psycopg2 (legacy driver)"
    )

    results.append(
        DjustWarning(
            (
                "Postgres LISTEN/NOTIFY (db.notifications) is unavailable because "
                "psycopg[binary]>=3.2 is not installed.\n"
                "  Detected: %s.\n"
                "  Required: psycopg[binary] (psycopg3) at version >= 3.2.\n"
                "  %s.\n"
                "  Non-Postgres apps and apps that don't use @notify_on_save / "
                "db.listen() are unaffected. Apps that DO use those features will "
                "hit a permanent-failure WARNING at first NOTIFY attempt (#1357)."
            )
            % (psycopg2_label, detected_msg),
            hint=(
                "pip install 'psycopg[binary]>=3.2' — or silence with "
                "SILENCED_SYSTEM_CHECKS = ['djust.D001'] if your app doesn't "
                "use db.notifications."
            ),
            id="djust.D001",
            fix_hint="pip install 'psycopg[binary]>=3.2'",
        )
    )
    return results
