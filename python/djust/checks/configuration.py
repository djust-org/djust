"""djust system checks — configuration checks (C0xx) — settings/CSS/ASGI validation.

Split from the former monolithic ``checks.py`` (#1822). No behavior change.
"""

import inspect
import logging
import os
from importlib import import_module
from typing import Any

from django.core.checks import CheckMessage, register

import djust.checks as _root
from djust.checks.utils import (
    DjustError,
    DjustInfo,
    DjustWarning,
    _is_check_suppressed,
    _walk_subclasses,
    _get_template_dirs,
)

logger = logging.getLogger(__name__)


def _has_asgi_server() -> bool:
    """Return True if a recognized ASGI server is importable.

    djust's README + ``djust-start`` canonical example use ``uvicorn``;
    ``daphne`` is the Channels default; ``hypercorn`` is the third common
    choice. C003 only fires when *none* is installed — telling a
    uvicorn-based project to ``pip install daphne`` is incorrect guidance
    (see #1630).

    Probed via ``importlib.util.find_spec`` (no actual import) so a
    detection failure can't ImportError-break ``manage.py check``.
    """
    import importlib.util

    for name in ("daphne", "uvicorn", "hypercorn"):
        try:
            if importlib.util.find_spec(name) is not None:
                return True
        except (ImportError, ValueError):
            continue
    return False


def _has_multiple_permission_groups(settings: Any) -> bool:
    """Return True if the project appears to use a role/group-based auth model.

    Detects one of:
      * ``django.contrib.auth.models.Group`` table has more than one row (runtime signal).
      * A known role-management package is in ``INSTALLED_APPS``
        (``rolepermissions``, ``rules``, ``django_guardian``, ``django_rules``).

    Used by check ``djust.A020`` (#659) to decide whether a hardcoded
    ``LOGIN_REDIRECT_URL`` deserves a warning about per-role redirects.

    Returns False on any exception (DB not ready, etc.) — checks must never
    raise from this helper.
    """
    installed = set(getattr(settings, "INSTALLED_APPS", []) or [])
    ROLE_PACKAGES = {
        "rolepermissions",
        "rules",
        "guardian",
        "django_guardian",
        "django_rules",
    }
    if installed & ROLE_PACKAGES:
        return True

    # Runtime signal — query the Group table if Django is ready. Guarded
    # because checks run during startup and the DB may not be initialised.
    try:
        from django.contrib.auth.models import Group

        return bool(Group.objects.count() > 1)
    except Exception:
        return False


def _check_tailwind_cdn_in_production(errors: list[CheckMessage]) -> None:
    """Check for Tailwind CDN usage in production (performance issue)."""
    template_dirs = _get_template_dirs()
    for template_dir in template_dirs:
        for root, dirs, files in os.walk(template_dir):
            for filename in files:
                if filename.endswith((".html", ".htm")):
                    # Check base/layout templates (most common location)
                    if "base" in filename.lower() or "layout" in filename.lower():
                        filepath = os.path.join(root, filename)
                        try:
                            with open(filepath, "r", encoding="utf-8") as f:
                                content = f.read()
                                # Scan template content for CDN reference (not URL validation)
                                # nosemgrep: python.lang.security.audit.dangerous-system-call.dangerous-system-call
                                cdn_domain = "cdn.tailwindcss.com"
                                if cdn_domain in content:
                                    errors.append(
                                        DjustWarning(
                                            f"Tailwind CDN detected in production template: {filename}",
                                            hint=(
                                                "Using Tailwind CDN in production is slow and triggers console warnings. "
                                                "Compile Tailwind CSS instead:\n"
                                                "1. Run: python manage.py djust_setup_css tailwind\n"
                                                "2. Or manually: tailwindcss -i static/css/input.css -o static/css/output.css --minify"
                                            ),
                                            id="djust.C010",
                                        )
                                    )
                        except Exception:
                            # Silently skip templates that can't be read (permissions, encoding, etc.)
                            # This is acceptable because check failures shouldn't block startup
                            pass


def _output_css_looks_built(path: str) -> bool:
    """Return True if ``path`` looks like a real compiled Tailwind output.

    #1003 — `os.path.exists()` is insufficient: a committed placeholder
    `output.css` (e.g. ``/* Run tailwindcss ... */``) passes that test
    but the site renders without any Tailwind utilities. This helper
    extends the contract to "the file exists AND looks built":

    - **Size threshold**: real Tailwind v4 output is always > 10 KB
      (even a tiny project pulls in the preflight reset + utility set).
    - **Header marker**: the Tailwind v4 minifier emits a
      ``/*! tailwindcss ... */`` banner; bare-bones theme CSS or
      stand-alone hand-written stylesheets contain ``@layer`` blocks.
      Either marker in the first 512 bytes is sufficient.

    Both checks must pass — a 50 KB hand-rolled stylesheet without
    Tailwind markers is also "not built Tailwind output". A real
    placeholder fails both.

    Returns False on any I/O error (missing file, permission denied,
    invalid encoding) — the safest behavior is to treat the missing
    signal as "not built" and let C011 fire.
    """
    try:
        if not os.path.exists(path):
            return False
        size = os.path.getsize(path)
        if size <= 10_000:
            return False
        with open(path, "rb") as f:
            head = f.read(512).decode("utf-8", errors="replace")
    except OSError:
        return False
    return "tailwindcss" in head.lower() or "@layer" in head


def _check_missing_compiled_css(errors: list[CheckMessage]) -> None:
    """Warn if Tailwind is configured but compiled CSS is missing or stale."""
    from django.conf import settings

    # Check common Tailwind indicators
    has_tailwind_config = os.path.exists("tailwind.config.js")
    has_input_css = False

    # Check for input.css in STATICFILES_DIRS
    static_dirs = getattr(settings, "STATICFILES_DIRS", [])
    for static_dir in static_dirs:
        if os.path.exists(os.path.join(static_dir, "css", "input.css")):
            has_input_css = True
            # Check if it's a Tailwind file
            try:
                with open(os.path.join(static_dir, "css", "input.css"), "r") as f:
                    content = f.read()
                    if "@import" in content and "tailwind" in content.lower():
                        has_input_css = True
                        break
            except Exception:
                # Silently skip files that can't be read (permissions, encoding, missing files)
                # This is acceptable because we're checking for Tailwind presence, not enforcement
                pass

    if has_tailwind_config or has_input_css:
        # #1003 — a committed-but-stale placeholder `output.css` (e.g.
        # `/* Run tailwindcss ... */`) silently passes a bare
        # `os.path.exists()` test, leading to a broken site that
        # serves with no Tailwind utilities and no `manage.py check`
        # warning. Use the content-sniffing helper instead so a
        # placeholder is treated the same as a missing file.
        output_built = False
        for static_dir in static_dirs:
            if _output_css_looks_built(os.path.join(static_dir, "css", "output.css")):
                output_built = True
                break

        if not output_built:
            if settings.DEBUG:
                errors.append(
                    DjustInfo(
                        "Tailwind CSS configured but output.css is missing or stale (development mode).",
                        hint=(
                            "djust will use Tailwind CDN as fallback in development. "
                            "A placeholder or empty output.css triggers this — run a "
                            "real Tailwind build for production-grade output:\n"
                            "  python manage.py djust_setup_css tailwind --watch"
                        ),
                        id="djust.C011",
                    )
                )
            else:
                errors.append(
                    DjustWarning(
                        "Tailwind CSS configured but output.css is missing or stale.",
                        hint=(
                            "A committed placeholder or empty output.css produces a site "
                            "that serves with no Tailwind utilities applied. Run:\n"
                            "  tailwindcss -i static/css/input.css -o static/css/output.css --minify\n"
                            "Or: python manage.py djust_setup_css tailwind"
                        ),
                        id="djust.C011",
                    )
                )


def _check_stale_collected_client(errors: list[CheckMessage]) -> None:
    """C013 — STATIC_ROOT/djust/client.min.js is older than the wheel-bundled
    copy at python/djust/static/djust/client.min.js (closes #1088).

    The trap: anyone with STATIC_ROOT configured (typical production setup
    behind WhiteNoise / nginx / a CDN) can ship a stale client.min.js after
    a djust wheel upgrade if they forget `collectstatic --clear`. The
    server runs new code; the browser loads old client.js → wire-protocol
    skew → mysterious VDOM patch failures. #1081 was reopened twice
    before the reporter root-caused this.

    Honors DJUST_CONFIG['suppress_checks'] = ['C013'] for users who
    deliberately serve client.min.js from elsewhere (CDN, custom build).
    """
    import hashlib
    from pathlib import Path

    from django.conf import settings

    if _is_check_suppressed("djust.C013"):
        return

    static_root = getattr(settings, "STATIC_ROOT", None)
    if not static_root:
        # No STATIC_ROOT means collectstatic isn't part of the deploy story
        return

    collected = Path(static_root) / "djust" / "client.min.js"
    if not collected.exists():
        # Either collectstatic hasn't run or the user serves client.min.js
        # from a different path. Either way, not our problem to flag here —
        # C011 (missing compiled CSS) and C012 (manual client.js) cover the
        # related symptoms.
        return

    # Find the wheel-bundled copy. djust.__file__ resolves to the installed
    # package path; static/djust/client.min.js is right alongside.
    try:
        from djust import __file__ as djust_init_path
    except ImportError:
        return
    wheel_bundled = Path(djust_init_path).parent / "static" / "djust" / "client.min.js"
    if not wheel_bundled.exists():
        return

    # Compare by content hash — mtime is unreliable across pip-install,
    # tar extraction, file-system mounts.
    def _sha256(p: Path) -> str:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    try:
        if _sha256(collected) == _sha256(wheel_bundled):
            return  # Up-to-date — quiet
    except OSError:
        return  # Permission / IO error — don't fail the check at startup

    errors.append(
        DjustWarning(
            f"Stale collectstatic copy of client.min.js detected at "
            f"{collected}. The wheel-bundled copy is different — your browser "
            f"will load outdated client code.",
            hint=(
                "Run `python manage.py collectstatic --clear --noinput` to refresh, "
                "then hard-reload the browser (Cmd+Shift+R / Ctrl+Shift+R). "
                "Suppress this check with DJUST_CONFIG = {'suppress_checks': ['C013']} "
                "if you serve client.min.js from a CDN or custom build."
            ),
            id="djust.C013",
            fix_hint=("Run: python manage.py collectstatic --clear --noinput"),
        )
    )


def _check_manual_client_js(errors: list[CheckMessage]) -> None:
    """Detect manual client.js loading in base templates (causes double-loading)."""
    template_dirs = _get_template_dirs()
    for template_dir in template_dirs:
        for root, dirs, files in os.walk(template_dir):
            for filename in files:
                if filename.endswith((".html", ".htm")):
                    # Check base/layout templates
                    if "base" in filename.lower() or "layout" in filename.lower():
                        filepath = os.path.join(root, filename)
                        try:
                            with open(filepath, "r", encoding="utf-8") as f:
                                lines = f.readlines()
                                for line_num, line in enumerate(lines, 1):
                                    # Look for manual client.js or client.min.js loading
                                    has_manual_ref = (
                                        "djust/client.js" in line or "djust/client.min.js" in line
                                    )
                                    if has_manual_ref and "<script" in line:
                                        # Make sure it's not a comment
                                        stripped = line.strip()
                                        if not stripped.startswith(
                                            "<!--"
                                        ) and not stripped.startswith("*"):
                                            errors.append(
                                                DjustWarning(
                                                    f"Manual client.js detected in {filename}:{line_num}",
                                                    hint=(
                                                        "djust automatically injects client.js for LiveView pages. "
                                                        "Remove the manual <script src=\"{% static 'djust/client.js' %}\"> tag "
                                                        "to avoid double-loading and race conditions."
                                                    ),
                                                    id="djust.C012",
                                                    file_path=filepath,
                                                    line_number=line_num,
                                                )
                                            )
                        except Exception:
                            pass  # Skip files that can't be read


def _check_multi_tenant_asgi_set_calls(errors: list[CheckMessage]) -> None:
    """C014 — django-tenants + ASGI without TENANT_LIMIT_SET_CALLS (closes #1556).

    Under ASGI + django-tenants, every WS event handler runs through
    TenantMainMiddleware → set_tenant() → SET search_path. LiveView amplifies
    this: tick_interval polling, push_to_view re-mounts, and @notify_on_save
    re-mounts each re-enter the middleware. Without TENANT_LIMIT_SET_CALLS,
    every re-entry emits a Postgres roundtrip. Production replicas can
    exhaust the Postgres pool and serve 503 simultaneously (#1556 captured
    via py-spy on djustlive prod: ThreadPoolExecutor-1168_0, -1199_0,
    -1206_0 each holding a fresh connection).

    Fires when ALL of the following hold:
    - django-tenants is configured (django_tenants in INSTALLED_APPS OR
      TENANT_MODEL setting is set)
    - ASGI_APPLICATION is set (the deployment is ASGI-shaped)
    - TENANT_LIMIT_SET_CALLS is unset or False

    Suppress with DJUST_CONFIG = {'suppress_checks': ['C014']} if the app
    deliberately tolerates the per-request SET search_path cost (e.g., low
    concurrency, no LiveView polling, or a hardened pgbouncer in front).

    Tracking #1557 for the framework-level fix (per-WS-session tenant cache).
    """
    from django.conf import settings

    if _is_check_suppressed("djust.C014"):
        return

    installed = list(getattr(settings, "INSTALLED_APPS", []))
    has_tenants_app = "django_tenants" in installed
    has_tenant_model = getattr(settings, "TENANT_MODEL", None) is not None
    if not (has_tenants_app or has_tenant_model):
        return

    if not getattr(settings, "ASGI_APPLICATION", None):
        return

    if getattr(settings, "TENANT_LIMIT_SET_CALLS", False):
        return

    errors.append(
        DjustWarning(
            "django-tenants integration is deprecated under djust — and "
            "this deploy is missing TENANT_LIMIT_SET_CALLS, which means "
            "every WS event will emit a redundant `SET search_path` and "
            "can exhaust the Postgres connection pool under LiveView load.",
            hint=(
                "django-tenants (schema-per-tenant) is DEPRECATED as a "
                "multi-tenancy strategy for djust applications. djust "
                "ships its own built-in row-level multi-tenancy "
                "(`djust.tenants`) which is the recommended and default "
                "ASGI-native path — it has no `SET search_path` in the "
                "per-event path by construction, so the connection-storm "
                "class of bug that motivated this check (#1556) does not "
                "exist on that path. Migrate to `djust.tenants` — see "
                "docs/website/guides/multi-tenant.md "
                "(`Choosing Your Multi-Tenancy Strategy`) for the "
                "decision criteria and migration target, and "
                "docs/website/guides/migrating-from-django-tenants.md "
                "for the step-by-step migration recipe (data migration, "
                "code/settings diffs, rollout, isolation canary). "
                "If you cannot migrate immediately, the stopgap is to "
                "set `TENANT_LIMIT_SET_CALLS = True` in settings; this "
                "django-tenants flag skips the `SET search_path` wire "
                "trip when the connection is already on the right "
                "tenant, which prevents the per-event Postgres roundtrip "
                "but does NOT make django-tenants supported long-term. "
                "See #1556 for the prod incident that motivated this "
                "check, and #1557 for the tracked framework-level fix "
                "for the migration period (per-WS-session tenant cache). "
                "Suppress with DJUST_CONFIG = {'suppress_checks': ['C014']}."
            ),
            id="djust.C014",
            fix_hint=(
                "Recommended: migrate to djust.tenants (row-level "
                "multi-tenancy) — see docs/website/guides/multi-tenant.md "
                "for the strategy decision and "
                "docs/website/guides/migrating-from-django-tenants.md "
                "for the step-by-step migration recipe. "
                "django-tenants integration is deprecated under djust. "
                "Stopgap until migration: add `TENANT_LIMIT_SET_CALLS = "
                "True` to your Django settings file. Under the stopgap "
                "also size Postgres `max_connections` for "
                "`replicas × ASGI_THREAD_LIMIT` and consider a "
                "transaction-pooling pgbouncer in front for any "
                "multi-replica multi-tenant deploy."
            ),
        )
    )


def _check_tenant_strict_mode_disabled(errors: list[CheckMessage]) -> None:
    """S006 — DJUST_TENANTS['STRICT_MODE'] explicitly False (fail-open tenancy).

    Finding #6 (CWE-862/CWE-636). djust's tenant-scoped managers
    (``TenantManager`` / ``TenantQuerySet.as_manager``) fail CLOSED by default:
    when no tenant is bound to the current context they return ``.none()`` so a
    missing tenant can never leak another tenant's rows. Setting
    ``DJUST_TENANTS['STRICT_MODE'] = False`` flips this to fail-OPEN — a query
    with no tenant bound returns ALL tenants' rows.

    This is especially dangerous on the live (WebSocket/SSE) path: the tenant is
    bound from the resolved view tenant, but any code path that queries a
    tenant-scoped model WITHOUT a tenant in context (a bug, an un-resolved
    tenant, a background task) discloses every tenant's data instead of erroring
    closed. Warn so the operator confirms the opt-out is intentional.

    Suppress with DJUST_CONFIG = {'suppress_checks': ['S006']} (or 'djust.S006').
    """
    from django.conf import settings

    if _is_check_suppressed("djust.S006"):
        return

    djust_tenants = getattr(settings, "DJUST_TENANTS", {}) or {}
    # Only fire when STRICT_MODE is *explicitly* set to a falsy value — absence
    # means the safe default (fail-closed) is in effect.
    if "STRICT_MODE" not in djust_tenants:
        return
    if djust_tenants.get("STRICT_MODE"):
        return

    errors.append(
        DjustWarning(
            "DJUST_TENANTS['STRICT_MODE'] is set to False — djust tenant "
            "isolation is fail-OPEN. Tenant-scoped queries that run without a "
            "tenant bound to the current context will return EVERY tenant's "
            "rows instead of an empty set, risking cross-tenant data "
            "disclosure (especially on the WebSocket/SSE live path).",
            hint=(
                "Remove DJUST_TENANTS['STRICT_MODE'] (or set it to True) to "
                "keep fail-closed isolation: with no tenant in context, "
                "tenant-scoped managers return .none(). STRICT_MODE=False is a "
                "backwards-compat escape hatch only — if you rely on it, scope "
                "every query explicitly (Model.objects.unscoped(reason=...) for "
                "deliberate cross-tenant reads) and ensure the tenant is always "
                "resolved on the live path. Suppress with "
                "DJUST_CONFIG = {'suppress_checks': ['S006']} if the fail-open "
                "behaviour is genuinely intended."
            ),
            id="djust.S006",
            fix_hint=(
                "In your Django settings, remove the "
                "`DJUST_TENANTS['STRICT_MODE'] = False` line (the default is "
                "True / fail-closed), or set it to True. Only keep it False if "
                "you have audited every tenant-scoped query for an explicit "
                "tenant filter."
            ),
        )
    )


# ---------------------------------------------------------------------------
# Configuration checks (C0xx)
# ---------------------------------------------------------------------------


@register("djust")
def check_configuration(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """Validate Django settings required by djust."""
    from django.conf import settings

    errors: list[CheckMessage] = []

    # C001 -- ASGI_APPLICATION not set
    if not getattr(settings, "ASGI_APPLICATION", None):
        errors.append(
            DjustError(
                "ASGI_APPLICATION is not set.",
                hint="Add ASGI_APPLICATION to your settings (e.g. 'myproject.asgi.application').",
                id="djust.C001",
                fix_hint="Add `ASGI_APPLICATION = 'myproject.asgi.application'` to your Django settings file.",
            )
        )

    # C002 -- CHANNEL_LAYERS not configured
    channel_layers = getattr(settings, "CHANNEL_LAYERS", None)
    if not channel_layers:
        errors.append(
            DjustError(
                "CHANNEL_LAYERS is not configured.",
                hint=(
                    "djust requires Django Channels. Add CHANNEL_LAYERS to your settings. "
                    "For development: CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}"
                ),
                id="djust.C002",
                fix_hint=(
                    "Add `CHANNEL_LAYERS = {'default': "
                    "{'BACKEND': 'channels.layers.InMemoryChannelLayer'}}` "
                    "to your Django settings file."
                ),
            )
        )

    # C003 -- daphne ordering in INSTALLED_APPS
    installed = list(getattr(settings, "INSTALLED_APPS", []))
    has_daphne = "daphne" in installed
    has_staticfiles = "django.contrib.staticfiles" in installed
    if has_daphne and has_staticfiles:
        if installed.index("daphne") > installed.index("django.contrib.staticfiles"):
            errors.append(
                DjustWarning(
                    "'daphne' should be listed before 'django.contrib.staticfiles' in INSTALLED_APPS.",
                    hint="Move 'daphne' above 'django.contrib.staticfiles' so it can override the runserver command.",
                    id="djust.C003",
                    fix_hint=(
                        "In INSTALLED_APPS, move `'daphne'` before "
                        "`'django.contrib.staticfiles'` in your Django settings file."
                    ),
                )
            )
    elif not has_daphne and not _is_check_suppressed("djust.C003"):
        # #1630: only fire C003 when NO ASGI server is detected. djust's
        # canonical recommendation is uvicorn (README + djust-start), not
        # daphne — so projects with uvicorn or hypercorn installed should
        # not get nagged to install daphne.
        if not _root._has_asgi_server():  # type: ignore[attr-defined]  # _root.* is dynamic re-export (patch-by-path; #1822 split)
            errors.append(
                DjustInfo(
                    "No ASGI server detected (daphne, uvicorn, or hypercorn).",
                    hint="Install one — uvicorn is recommended: "
                    "`pip install 'uvicorn[standard]'`. "
                    "Suppress this check with DJUST_CONFIG = {'suppress_checks': ['C003']}.",
                    id="djust.C003",
                    fix_hint="Install uvicorn: `uv pip install 'uvicorn[standard]'`.",
                )
            )

    # C004 -- djust not in INSTALLED_APPS
    if "djust" not in installed:
        errors.append(
            DjustError(
                "'djust' is not in INSTALLED_APPS.",
                hint="Add 'djust' to INSTALLED_APPS.",
                id="djust.C004",
                fix_hint="Add `'djust'` to INSTALLED_APPS in your Django settings file.",
            )
        )

    # C010 -- Tailwind CDN in production
    if not settings.DEBUG:
        _root._check_tailwind_cdn_in_production(errors)  # type: ignore[attr-defined]  # _root.* is dynamic re-export (patch-by-path; #1822 split)

    # C011 -- Missing compiled CSS
    _root._check_missing_compiled_css(errors)  # type: ignore[attr-defined]  # _root.* is dynamic re-export (patch-by-path; #1822 split)

    # C012 -- Manual client.js in base templates
    _root._check_manual_client_js(errors)  # type: ignore[attr-defined]  # _root.* is dynamic re-export (patch-by-path; #1822 split)

    # C013 -- Stale collectstatic copy of client.min.js (closes #1088)
    _check_stale_collected_client(errors)

    # C014 -- Multi-tenant ASGI without TENANT_LIMIT_SET_CALLS (closes #1556)
    _check_multi_tenant_asgi_set_calls(errors)

    # S006 -- DJUST_TENANTS['STRICT_MODE']=False disables fail-closed tenancy
    _check_tenant_strict_mode_disabled(errors)

    # C005 -- WebSocket routes missing AuthMiddlewareStack
    # A001 -- WebSocket routes missing AllowedHostsOriginValidator (#659)
    asgi_path = getattr(settings, "ASGI_APPLICATION", None)
    if asgi_path:
        try:
            module_path, attr = asgi_path.rsplit(".", 1)
            asgi_app = getattr(import_module(module_path), attr)
            # ProtocolTypeRouter stores routes in .application_mapping
            app_map = getattr(asgi_app, "application_mapping", None)
            if app_map and "websocket" in app_map:
                ws_app = app_map["websocket"]
                # Walk the middleware chain looking for Auth/DjustMiddlewareStack
                # AND for AllowedHostsOriginValidator (defence-in-depth to #653).
                has_middleware = False
                has_origin_validator = False
                current = ws_app
                for _ in range(10):  # bounded walk
                    cls_name = type(current).__name__
                    mod_name = type(current).__module__ or ""
                    # #659 A001 — check for OriginValidator (any flavor)
                    if "originvalidator" in cls_name.lower():
                        has_origin_validator = True
                    if "auth" in cls_name.lower() or "auth" in mod_name.lower():
                        has_middleware = True
                    if "session" in cls_name.lower() or "session" in mod_name.lower():
                        # DjustMiddlewareStack wraps SessionMiddlewareStack
                        has_middleware = True
                    # Follow common wrapper patterns
                    inner = getattr(current, "inner", None) or getattr(current, "application", None)
                    if inner is None or inner is current:
                        break
                    current = inner
                if not has_middleware:
                    errors.append(
                        DjustWarning(
                            "WebSocket routes are not wrapped with AuthMiddlewareStack "
                            "or DjustMiddlewareStack.",
                            hint=(
                                "Without middleware, request.session is unavailable in "
                                "LiveView mount() over WebSocket. Wrap your URLRouter: "
                                "AuthMiddlewareStack(URLRouter(...)) for apps with auth, "
                                "or DjustMiddlewareStack(URLRouter(...)) for apps without."
                            ),
                            id="djust.C005",
                            fix_hint=(
                                "In your ASGI routing file, wrap your WebSocket URLRouter with "
                                "`AuthMiddlewareStack(URLRouter(...))` or "
                                "`DjustMiddlewareStack(URLRouter(...))`."
                            ),
                        )
                    )
                if not has_origin_validator:
                    errors.append(
                        DjustError(
                            "WebSocket routes are not wrapped in AllowedHostsOriginValidator; "
                            "the app is vulnerable to Cross-Site WebSocket Hijacking (CSWSH).",
                            hint=(
                                "Any cross-origin page on the internet can open a WebSocket "
                                "connection to your app, mount any LiveView, and dispatch events "
                                "from a victim browser. Wrap the WebSocket router in "
                                "channels.security.websocket.AllowedHostsOriginValidator "
                                "(DjustMiddlewareStack does this by default since #653). "
                                "Prerequisite: settings.ALLOWED_HOSTS must not contain '*'."
                            ),
                            id="djust.A001",
                            fix_hint=(
                                "Wrap your WebSocket router: "
                                '"websocket": AllowedHostsOriginValidator(DjustMiddlewareStack(URLRouter(...))). '
                                "Or update DjustMiddlewareStack — since djust 0.4.1 it wraps "
                                "the origin validator by default."
                            ),
                        )
                    )
        except Exception:
            pass  # Don't fail the check if ASGI app can't be introspected

    # A010/A011/A012 -- ALLOWED_HOSTS wildcard footguns (#659)
    allowed_hosts = list(getattr(settings, "ALLOWED_HOSTS", []) or [])
    # Proxy-trusted escape hatch (#890): a deployer behind AWS ALB / Cloudflare /
    # Fly.io / similar L7 load balancers can't enumerate rotating task private IPs.
    # If both SECURE_PROXY_SSL_HEADER and DJUST_TRUSTED_PROXIES are set, the
    # deployer is explicitly asserting a trusted proxy terminates requests, so
    # the wildcard Host check at the Django layer is redundant.
    trusted_proxies = getattr(settings, "DJUST_TRUSTED_PROXIES", None)
    proxy_ssl_header = getattr(settings, "SECURE_PROXY_SSL_HEADER", None)
    proxy_trusted = bool(trusted_proxies) and bool(proxy_ssl_header)
    if not getattr(settings, "DEBUG", False) and not proxy_trusted:
        if "*" in allowed_hosts and len(allowed_hosts) == 1:
            errors.append(
                DjustError(
                    "ALLOWED_HOSTS contains only '*' in production.",
                    hint=(
                        "Wildcard ALLOWED_HOSTS disables Django's Host header defense "
                        "entirely. Combined with AllowedHostsOriginValidator this also "
                        "re-opens CSWSH (#653) because the validator reads ALLOWED_HOSTS. "
                        "Set ALLOWED_HOSTS to explicit hostnames, or set "
                        "DJUST_TRUSTED_PROXIES + SECURE_PROXY_SSL_HEADER if you're behind "
                        "a trusted proxy (AWS ALB, Cloudflare, Fly.io, etc.)."
                    ),
                    id="djust.A010",
                    fix_hint=(
                        "In settings.py, set ALLOWED_HOSTS to the explicit hostnames your "
                        "app serves (e.g. ['myapp.example.com', 'api.example.com']). "
                        "Or, if you're behind a trusted L7 load balancer, set both "
                        "SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https') and "
                        "DJUST_TRUSTED_PROXIES=['<proxy-identifier>'] to suppress A010."
                    ),
                )
            )
        elif "*" in allowed_hosts and len(allowed_hosts) > 1:
            errors.append(
                DjustError(
                    "ALLOWED_HOSTS has '*' mixed with explicit hosts — the wildcard makes "
                    "the other entries meaningless.",
                    hint=(
                        "Django accepts any Host header as soon as '*' is present, so "
                        "listing 'myapp.example.com' alongside '*' is a common footgun — "
                        "the explicit host is ignored. Remove '*' and keep only the "
                        "explicit hostnames, or set DJUST_TRUSTED_PROXIES + "
                        "SECURE_PROXY_SSL_HEADER if you're behind a trusted proxy."
                    ),
                    id="djust.A011",
                    fix_hint=(
                        "In settings.py, remove '*' from ALLOWED_HOSTS and keep only the "
                        "explicit hostnames. Or, if you're behind a trusted L7 load "
                        "balancer, set both SECURE_PROXY_SSL_HEADER and "
                        "DJUST_TRUSTED_PROXIES to suppress A011."
                    ),
                )
            )
        if getattr(settings, "USE_X_FORWARDED_HOST", False) and "*" in allowed_hosts:
            errors.append(
                DjustError(
                    "USE_X_FORWARDED_HOST=True combined with wildcard ALLOWED_HOSTS enables "
                    "Host header injection.",
                    hint=(
                        "USE_X_FORWARDED_HOST makes Django trust the X-Forwarded-Host header, "
                        "which attackers control. With wildcard ALLOWED_HOSTS there is no "
                        "validation. Set ALLOWED_HOSTS to explicit hostnames."
                    ),
                    id="djust.A012",
                )
            )

    # A014 -- SECRET_KEY still has the insecure scaffold prefix in production
    secret_key = getattr(settings, "SECRET_KEY", "") or ""
    if not getattr(settings, "DEBUG", False) and secret_key.startswith("django-insecure-"):
        errors.append(
            DjustError(
                "SECRET_KEY starts with 'django-insecure-' in production.",
                hint=(
                    "The scaffold default SECRET_KEY is a placeholder meant to be replaced "
                    "before deployment. An attacker who knows the value (anyone with access "
                    "to the source repo) can forge session cookies and password-reset tokens."
                ),
                id="djust.A014",
                fix_hint=(
                    "Generate a new SECRET_KEY with "
                    '`python -c "from django.core.management.utils import get_random_secret_key; '
                    'print(get_random_secret_key())"` and load it from an environment variable.'
                ),
            )
        )

    # A020 -- LOGIN_REDIRECT_URL is a single hardcoded path but the project has roles
    login_redirect = getattr(settings, "LOGIN_REDIRECT_URL", None)
    if isinstance(login_redirect, str) and _root._has_multiple_permission_groups(settings):  # type: ignore[attr-defined]  # _root.* is dynamic re-export (patch-by-path; #1822 split)
        errors.append(
            DjustWarning(
                "LOGIN_REDIRECT_URL is a single hardcoded path (%r) but the project has "
                "multiple auth groups/permissions. All roles will be redirected to the "
                "same page after login — both a UX problem and a strong signal that "
                "per-role access control wasn't considered." % login_redirect,
                hint=(
                    "Use a custom LoginView.get_success_url() that picks a role-appropriate "
                    "landing URL, OR handle routing in the view layer with a redirect based "
                    "on request.user's group/permissions."
                ),
                id="djust.A020",
                fix_hint=(
                    "Subclass django.contrib.auth.views.LoginView and override get_success_url() "
                    "to return a role-specific path based on request.user."
                ),
            )
        )

    # A030 -- django.contrib.admin without brute-force protection
    if "django.contrib.admin" in installed:
        brute_force_packages = {
            "axes",
            "defender",
            "brutebuster",
            "ratelimit",
            "django_ratelimit",
            "django_axes",
        }
        if not any(app in brute_force_packages for app in installed):
            errors.append(
                DjustWarning(
                    "django.contrib.admin is installed but no brute-force protection package "
                    "was detected in INSTALLED_APPS.",
                    hint=(
                        "The Django admin has no built-in rate limiting. Without a package "
                        "like django-axes, /admin/ is vulnerable to credential brute-force. "
                        "Install one of: django-axes, django-defender, django-brutebuster, "
                        "django-ratelimit."
                    ),
                    id="djust.A030",
                    fix_hint=(
                        "Install django-axes: `pip install django-axes`, then add 'axes' to "
                        "INSTALLED_APPS and 'axes.middleware.AxesMiddleware' to MIDDLEWARE."
                    ),
                )
            )

    # S004 -- DEBUG=True with non-localhost ALLOWED_HOSTS
    if getattr(settings, "DEBUG", False):
        allowed = getattr(settings, "ALLOWED_HOSTS", [])
        non_local = [
            h
            for h in allowed
            if h not in ("localhost", "127.0.0.1", "::1", "", "*", ".localhost")
            and not h.startswith("192.168.")
            and not h.startswith("10.")
        ]
        if non_local:
            errors.append(
                DjustWarning(
                    "DEBUG=True with non-localhost ALLOWED_HOSTS: %s" % ", ".join(non_local),
                    hint="Ensure DEBUG is False in production or restrict ALLOWED_HOSTS to local addresses.",
                    id="djust.S004",
                    fix_hint=(
                        "Set `DEBUG = False` in your production settings, or remove "
                        "non-localhost entries from ALLOWED_HOSTS."
                    ),
                )
            )

        # A031 -- observability endpoints wired but the localhost middleware
        # is not installed (defense-in-depth recommendation; the in-view gate
        # in observability.views._gate is the authoritative localhost check,
        # so this is a WARNING, not an error). Finding #9.
        try:
            from django.urls import NoReverseMatch, reverse

            try:
                reverse("djust_observability:health")
                _obs_wired = True
            except NoReverseMatch:
                _obs_wired = False
            if _obs_wired:
                mw = getattr(settings, "MIDDLEWARE", []) or []
                if not any("LocalhostOnlyObservabilityMiddleware" in m for m in mw):
                    errors.append(
                        DjustWarning(
                            "djust observability endpoints (_djust/observability/) are "
                            "wired but LocalhostOnlyObservabilityMiddleware is not in "
                            "MIDDLEWARE.",
                            hint=(
                                "The endpoints self-gate to localhost in-view, so this is "
                                "defense-in-depth — but the middleware rejects non-localhost "
                                "requests before the view runs. Add it under DEBUG."
                            ),
                            id="djust.A031",
                            fix_hint=(
                                "In settings.py under `if DEBUG:` add "
                                "'djust.observability.middleware.LocalhostOnlyObservabilityMiddleware' "
                                "to the start of MIDDLEWARE."
                            ),
                        )
                    )
        except Exception:  # noqa: BLE001 — never let a check crash the suite
            pass

    # S005 -- LiveView exposes state without authentication
    try:
        from djust.live_view import LiveView
        from djust.management.commands.djust_audit import _extract_exposed_state

        for cls in _walk_subclasses(LiveView):
            module = getattr(cls, "__module__", "") or ""
            if module.startswith("djust.") or module.startswith("djust_"):
                if "test" not in module and "example" not in module:
                    continue

            login_req = getattr(cls, "login_required", None)
            perm_req = getattr(cls, "permission_required", None)
            # Check if auth has been addressed (True/False) vs unaddressed (None).
            # login_required = False means "intentionally public", so skip warning.
            if login_req is not None or perm_req is not None:
                continue  # View has auth configured

            # Check if check_permissions is overridden
            has_custom_check = False
            for klass in cls.__mro__:
                if klass.__name__ in ("LiveView", "LiveComponent", "object"):
                    break
                if "check_permissions" in klass.__dict__:
                    has_custom_check = True
                    break
            if has_custom_check:
                continue

            # Check for dispatch-based auth mixins (e.g. LoginRequiredMixin)
            from djust.management.commands.djust_audit import _has_auth_mixin

            if _has_auth_mixin(cls):
                continue

            exposed = _extract_exposed_state(cls)
            if exposed:
                cls_label = "%s.%s" % (cls.__module__, cls.__qualname__)
                try:
                    cls_file = inspect.getfile(cls) if hasattr(cls, "__module__") else ""
                except (OSError, TypeError):
                    cls_file = ""
                try:
                    cls_line = inspect.getsourcelines(cls)[1]
                except (OSError, TypeError):
                    cls_line = None
                errors.append(
                    DjustWarning(
                        "%s exposes state without authentication." % cls_label,
                        hint=(
                            "Add login_required = True or permission_required to protect "
                            "this view, or set login_required = False to acknowledge "
                            "public access."
                        ),
                        id="djust.S005",
                        fix_hint=(
                            "Add `login_required = True` as a class attribute on `%s`."
                            % cls.__qualname__
                        ),
                        file_path=cls_file,
                        line_number=cls_line,
                    )
                )
    except ImportError:
        pass  # LiveView not available (Rust extension not built)

    return errors
