"""
Django system checks for the djust framework.

Registers checks with Django's check framework that also run via
``python manage.py check``. Categories:

- Configuration (C0xx) -- settings validation
- LiveView (V0xx) -- LiveView subclass validation
- Security (S0xx) -- AST-based security checks
- Templates (T0xx) -- template file scanning
- Code Quality (Q0xx) -- AST-based quality checks
- Accessibility (Y0xx) -- template ARIA/WCAG scanning
"""

import ast
import inspect
import logging
import os
import re
from importlib import import_module

from django.core.checks import Error, Warning, Info, register

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom check result classes with fix_hint support
# ---------------------------------------------------------------------------


class _DjustCheckMixin:
    """Mixin that adds fix_hint, file_path, and line_number to check results."""

    def __init__(self, *args, fix_hint="", file_path="", line_number=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fix_hint = fix_hint
        self.file_path = file_path
        self.line_number = line_number


class DjustError(_DjustCheckMixin, Error):
    """Error with fix_hint metadata."""

    pass


class DjustWarning(_DjustCheckMixin, Warning):
    """Warning with fix_hint metadata."""

    pass


class DjustInfo(_DjustCheckMixin, Info):
    """Info with fix_hint metadata."""

    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EVENT_HANDLER_LIKE_NAMES = re.compile(
    r"^(handle_|on_|toggle_|select_|update_|delete_|create_|add_|remove_|save_|cancel_|submit_|close_|open_)"
)

_SERVICE_INSTANCE_KEYWORDS = re.compile(r"(Service|Client|Session|API|Connection)", re.IGNORECASE)

_DJ_VIEW_RE = re.compile(r"dj-view")


def _is_check_suppressed(check_id: str) -> bool:
    """Return True if the user has suppressed *check_id* via settings.

    Users can add ``suppress_checks`` to ``DJUST_CONFIG`` (or
    ``LIVEVIEW_CONFIG``) to silence informational checks that are noisy for
    projects that deliberately don't use the checked feature::

        # settings.py
        DJUST_CONFIG = {
            "suppress_checks": ["T002", "V008", "C003"],
        }

    Both short IDs (``"T002"``) and fully-qualified IDs (``"djust.T002"``)
    are accepted; comparison is case-insensitive.
    """
    try:
        from django.conf import settings

        suppressed = (
            getattr(settings, "DJUST_CONFIG", {}).get("suppress_checks")
            or getattr(settings, "LIVEVIEW_CONFIG", {}).get("suppress_checks")
            or []
        )
    except Exception:
        return False

    if not suppressed:
        return False

    # Normalise: accept "T002" or "djust.T002", case-insensitive
    normalised = set()
    for item in suppressed:
        item_lower = str(item).lower()
        normalised.add(item_lower)
        # Also add/remove the "djust." prefix so either form matches
        if item_lower.startswith("djust."):
            normalised.add(item_lower[len("djust.") :])
        else:
            normalised.add("djust." + item_lower)

    return check_id.lower() in normalised


def _has_multiple_permission_groups(settings) -> bool:
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

        return Group.objects.count() > 1
    except Exception:
        return False


def _check_tailwind_cdn_in_production(errors):
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


def _check_missing_compiled_css(errors):
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


def _check_stale_collected_client(errors):
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


def _check_manual_client_js(errors):
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


def _check_multi_tenant_asgi_set_calls(errors):
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
            "Multi-tenant ASGI deploy without TENANT_LIMIT_SET_CALLS — "
            "every WS event will emit a redundant `SET search_path` and "
            "can exhaust the Postgres connection pool under LiveView load.",
            hint=(
                "Set `TENANT_LIMIT_SET_CALLS = True` in settings. This is a "
                "django-tenants flag that skips the `SET search_path` wire "
                "trip when the connection is already on the right tenant. "
                "Under djust, every WebSocket event (tick_interval, "
                "push_to_view, presence updates, @notify_on_save) re-enters "
                "TenantMainMiddleware; without this flag, each re-entry "
                "issues a Postgres roundtrip. See #1556 for the prod "
                "incident that motivated this check, and #1557 for the "
                "tracked framework-level fix (per-WS-session tenant cache). "
                "Suppress with DJUST_CONFIG = {'suppress_checks': ['C014']}."
            ),
            id="djust.C014",
            fix_hint=(
                "Add `TENANT_LIMIT_SET_CALLS = True` to your Django "
                "settings file. Also size Postgres `max_connections` for "
                "`replicas × ASGI_THREAD_LIMIT` and consider a "
                "transaction-pooling pgbouncer in front for any "
                "multi-replica multi-tenant deploy."
            ),
        )
    )


def _get_project_app_dirs():
    """Return directories for project apps (excluding third-party and djust itself)."""
    from django.apps import apps

    dirs = []
    for config in apps.get_app_configs():
        path = config.path
        # Skip site-packages / third-party
        if "site-packages" in path:
            continue
        # Skip djust's own package
        if path.endswith("djust") or "/djust/" in path:
            continue
        if os.path.isdir(path):
            dirs.append(path)
    return dirs


def _get_template_dirs():
    """Return all configured template directories."""
    from django.conf import settings

    dirs = []
    for backend in getattr(settings, "TEMPLATES", []):
        for d in backend.get("DIRS", []):
            if os.path.isdir(d):
                dirs.append(d)
        # Also check APP_DIRS templates
        if backend.get("APP_DIRS"):
            for app_dir in _get_project_app_dirs():
                tpl_dir = os.path.join(app_dir, "templates")
                if os.path.isdir(tpl_dir):
                    dirs.append(tpl_dir)
    return dirs


def _iter_python_files(directories):
    """Yield .py file paths from directories, skipping migrations/tests."""
    for directory in directories:
        for root, _dirs, files in os.walk(directory):
            # Skip common non-project directories
            basename = os.path.basename(root)
            if basename in ("migrations", "tests", "__pycache__", ".venv", "node_modules"):
                continue
            for fname in files:
                if fname.endswith(".py"):
                    yield os.path.join(root, fname)


def _iter_template_files(directories):
    """Yield .html template file paths from directories."""
    for directory in directories:
        for root, _dirs, files in os.walk(directory):
            for fname in files:
                if fname.endswith(".html"):
                    yield os.path.join(root, fname)


def _iter_js_files(directories):
    """Yield .js file paths from directories."""
    for directory in directories:
        for root, _dirs, files in os.walk(directory):
            basename = os.path.basename(root)
            if basename in ("node_modules", "__pycache__", ".venv"):
                continue
            for fname in files:
                if fname.endswith(".js"):
                    yield os.path.join(root, fname)


def _parse_python_file(filepath):
    """Return (AST tree, source_lines) for a Python file, or (None, []) on failure.

    source_lines is 1-indexed: source_lines[0] is unused, source_lines[1] is line 1.
    """
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
            source = fh.read()
        tree = ast.parse(source, filename=filepath)
        # Prepend empty string so source_lines[1] == first line of file
        lines = [""] + source.splitlines()
        return tree, lines
    except SyntaxError:
        return None, []


def _has_noqa(source_lines, lineno, check_id):
    """Return True if source line has a # noqa comment suppressing check_id.

    Supports:
        # noqa           — suppress all checks on this line
        # noqa: Q001     — suppress specific check
        # noqa: Q001,S002 — suppress multiple checks
    """
    if lineno < 1 or lineno >= len(source_lines):
        return False
    line = source_lines[lineno]
    # Find # noqa in the line
    idx = line.find("# noqa")
    if idx == -1:
        return False
    rest = line[idx + 6 :].strip()
    if not rest:
        return True  # bare # noqa — suppress everything
    if rest.startswith(":"):
        # Split on comma, take first whitespace-delimited token from each
        # to handle trailing comments like "# noqa: Q001 — reason here"
        codes = []
        for part in rest[1:].split(","):
            token = part.strip().split()[0] if part.strip() else ""
            codes.append(token)
        return check_id in codes
    return True


# ---------------------------------------------------------------------------
# Configuration checks (C0xx)
# ---------------------------------------------------------------------------


@register("djust")
def check_configuration(app_configs, **kwargs):
    """Validate Django settings required by djust."""
    from django.conf import settings

    errors = []

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
        errors.append(
            DjustInfo(
                "'daphne' is not in INSTALLED_APPS.",
                hint="Consider adding 'daphne' to INSTALLED_APPS for ASGI support. "
                "Suppress this check with DJUST_CONFIG = {'suppress_checks': ['C003']}.",
                id="djust.C003",
                fix_hint="Add `'daphne'` to the beginning of INSTALLED_APPS in your Django settings file.",
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
        _check_tailwind_cdn_in_production(errors)

    # C011 -- Missing compiled CSS
    _check_missing_compiled_css(errors)

    # C012 -- Manual client.js in base templates
    _check_manual_client_js(errors)

    # C013 -- Stale collectstatic copy of client.min.js (closes #1088)
    _check_stale_collected_client(errors)

    # C014 -- Multi-tenant ASGI without TENANT_LIMIT_SET_CALLS (closes #1556)
    _check_multi_tenant_asgi_set_calls(errors)

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
    if isinstance(login_redirect, str) and _has_multiple_permission_groups(settings):
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
def check_service_worker_advanced(app_configs, **kwargs):
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


# ---------------------------------------------------------------------------
# LiveView checks (V0xx)
# ---------------------------------------------------------------------------


def _walk_subclasses(cls):
    """Recursively yield all subclasses of cls."""
    for sub in cls.__subclasses__():
        yield sub
        yield from _walk_subclasses(sub)


@register("djust")
def check_liveviews(app_configs, **kwargs):
    """Validate LiveView subclasses."""
    errors = []

    try:
        from djust.live_view import LiveView
    except ImportError:
        return errors

    from django.conf import settings
    from djust.decorators import is_event_handler

    for cls in _walk_subclasses(LiveView):
        # Skip abstract-looking classes (mixins, bases defined in djust itself)
        module = getattr(cls, "__module__", "") or ""
        if module.startswith("djust.") or module.startswith("djust_"):
            # Skip internal djust classes -- only check user classes
            # But still check classes in djust's own examples/tests
            if "test" not in module and "example" not in module:
                continue

        cls_label = "%s.%s" % (cls.__module__, cls.__qualname__)

        # V001 -- missing template_name
        has_template_name = (
            cls.__dict__.get("template_name") is not None
            or cls.__dict__.get("template") is not None
        )
        if not has_template_name:
            # Check parent classes (but not LiveView itself)
            found_in_parent = False
            for parent in cls.__mro__[1:]:
                if parent is LiveView:
                    break
                if parent.__dict__.get("template_name") or parent.__dict__.get("template"):
                    found_in_parent = True
                    break
            if not found_in_parent:
                cls_file = ""
                cls_line = None
                try:
                    cls_file = inspect.getfile(cls)
                    cls_line = inspect.getsourcelines(cls)[1]
                except (OSError, TypeError):
                    pass  # Source introspection may fail for built-in or C-extension classes
                errors.append(
                    DjustWarning(
                        "%s: missing 'template_name' attribute." % cls_label,
                        hint="Set template_name on your LiveView class.",
                        id="djust.V001",
                        fix_hint=(
                            "Add `template_name = 'your_template.html'` as a class "
                            "attribute on `%s`." % cls.__qualname__
                        ),
                        file_path=cls_file,
                        line_number=cls_line,
                    )
                )

        # V002 -- missing mount() method
        if "mount" not in cls.__dict__:
            # Check if any parent (other than LiveView/mixins) defines mount
            has_mount = False
            for parent in cls.__mro__[1:]:
                if parent is LiveView:
                    break
                if "mount" in parent.__dict__:
                    has_mount = True
                    break
            if not has_mount:
                cls_file = ""
                cls_line = None
                try:
                    cls_file = inspect.getfile(cls)
                    cls_line = inspect.getsourcelines(cls)[1]
                except (OSError, TypeError):
                    pass  # Source introspection may fail for built-in or C-extension classes
                errors.append(
                    DjustInfo(
                        "%s: no mount() method defined." % cls_label,
                        hint="Define mount(self, request, **kwargs) to initialise state.",
                        id="djust.V002",
                        fix_hint=(
                            "Add a `def mount(self, request, **kwargs):` method to `%s`."
                            % cls.__qualname__
                        ),
                        file_path=cls_file,
                        line_number=cls_line,
                    )
                )

        # V003 -- mount() has wrong signature
        mount_method = cls.__dict__.get("mount")
        if mount_method and callable(mount_method):
            sig = inspect.signature(mount_method)
            params = list(sig.parameters.keys())
            # Should be (self, request, **kwargs) at minimum
            if len(params) < 2 or params[1] != "request":
                cls_file = ""
                cls_line = None
                try:
                    cls_file = inspect.getfile(cls)
                    cls_line = inspect.getsourcelines(mount_method)[1]
                except (OSError, TypeError):
                    pass  # Source introspection may fail for built-in or C-extension classes
                errors.append(
                    DjustError(
                        "%s: mount() should accept (self, request, **kwargs)." % cls_label,
                        hint="Change signature to: def mount(self, request, **kwargs):",
                        id="djust.V003",
                        fix_hint=(
                            "Change the `mount()` signature to "
                            "`def mount(self, request, **kwargs):` in `%s`." % cls.__qualname__
                        ),
                        file_path=cls_file,
                        line_number=cls_line,
                    )
                )

        # V004 -- public method looks like event handler but missing @event_handler
        for name, method in cls.__dict__.items():
            if name.startswith("_"):
                continue
            if not callable(method):
                continue
            # Skip known lifecycle methods — these are called by the framework, not
            # by user events, and should never carry @event_handler.
            if name in (
                "mount",
                "get_context_data",
                "dispatch",
                "setup",
                "get",
                "post",
                "handle_params",
                "handle_disconnect",
                "handle_connect",
                "handle_event",
            ):
                continue
            if is_event_handler(method):
                continue
            if _EVENT_HANDLER_LIKE_NAMES.match(name):
                method_file = ""
                method_line = None
                try:
                    method_file = inspect.getfile(method)
                    method_line = inspect.getsourcelines(method)[1]
                except (OSError, TypeError):
                    pass  # Source introspection may fail for built-in or C-extension classes
                errors.append(
                    DjustInfo(
                        "%s.%s() looks like an event handler but is missing @event_handler."
                        % (cls_label, name),
                        hint="Add @event_handler decorator or prefix with _ if it is private.",
                        id="djust.V004",
                        fix_hint=(
                            "Add `@event_handler()` decorator above the method `%s` in `%s`."
                            % (name, method_file or cls_label)
                        ),
                        file_path=method_file,
                        line_number=method_line,
                    )
                )

        # Q007 -- overlapping static_assigns and temporary_assigns
        static = set(getattr(cls, "static_assigns", []))
        temporary = set(getattr(cls, "temporary_assigns", {}).keys())
        overlap = static & temporary
        if overlap:
            cls_file = ""
            cls_line = None
            try:
                cls_file = inspect.getfile(cls)
                cls_line = inspect.getsourcelines(cls)[1]
            except (OSError, TypeError):
                pass  # Source introspection may fail for built-in or C-extension classes
            errors.append(
                DjustWarning(
                    "%s: keys %s appear in both static_assigns and temporary_assigns."
                    % (cls_label, overlap),
                    hint="A key cannot be both static (never re-sent) and temporary (cleared after render).",
                    id="djust.Q007",
                    fix_hint=(
                        "Remove overlapping keys from either static_assigns or "
                        "temporary_assigns in `%s`." % cls.__qualname__
                    ),
                    file_path=cls_file,
                    line_number=cls_line,
                )
            )

        # V005 -- module not in LIVEVIEW_ALLOWED_MODULES
        allowed = getattr(settings, "LIVEVIEW_ALLOWED_MODULES", None)
        if allowed is not None and module not in allowed:
            errors.append(
                DjustWarning(
                    "%s is not in LIVEVIEW_ALLOWED_MODULES. "
                    "WebSocket mount will silently fail." % cls_label,
                    hint="Add '%s' to LIVEVIEW_ALLOWED_MODULES in settings." % module,
                    id="djust.V005",
                    fix_hint=(
                        "Add `'%s'` to the `LIVEVIEW_ALLOWED_MODULES` list in your "
                        "Django settings file." % module
                    ),
                )
            )

        # V007 -- event handler missing **kwargs
        for name, method in cls.__dict__.items():
            if not callable(method):
                continue
            if not is_event_handler(method):
                continue
            # Unwrap decorators to get original function
            inner = method
            for _attempt in range(10):
                inner = getattr(inner, "__wrapped__", None) or getattr(inner, "func", None)
                if inner is None:
                    break
            sig_target = inner if inner is not None else method
            try:
                sig = inspect.signature(sig_target)
            except (ValueError, TypeError):
                continue
            has_var_keyword = any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            )
            if not has_var_keyword:
                method_file = ""
                method_line = None
                try:
                    method_file = inspect.getfile(sig_target)
                    method_line = inspect.getsourcelines(sig_target)[1]
                except (OSError, TypeError):
                    pass  # Source introspection may fail for built-in or C-extension classes
                errors.append(
                    DjustWarning(
                        "%s.%s() event handler missing **kwargs in signature." % (cls_label, name),
                        hint="Add **kwargs to the event handler signature to receive event parameters.",
                        id="djust.V007",
                        fix_hint=(
                            "Add `**kwargs` to the `%s` method signature in `%s`."
                            % (name, method_file or cls_label)
                        ),
                        file_path=method_file,
                        line_number=method_line,
                    )
                )

        # V009 -- on_mount contains non-callable items
        on_mount_hooks = cls.__dict__.get("on_mount")
        if on_mount_hooks is not None:
            if not isinstance(on_mount_hooks, (list, tuple)):
                cls_file = ""
                cls_line = None
                try:
                    cls_file = inspect.getfile(cls)
                    cls_line = inspect.getsourcelines(cls)[1]
                except (OSError, TypeError):
                    pass  # Fall back to empty file/line for built-in or C-extension classes
                errors.append(
                    DjustWarning(
                        "%s: 'on_mount' should be a list of hook functions." % cls_label,
                        hint="Set on_mount = [hook1, hook2, ...] on your LiveView class.",
                        id="djust.V009",
                        fix_hint=("Change `on_mount` to a list in `%s`." % cls.__qualname__),
                        file_path=cls_file,
                        line_number=cls_line,
                    )
                )
            else:
                for i, hook in enumerate(on_mount_hooks):
                    if not callable(hook):
                        cls_file = ""
                        cls_line = None
                        try:
                            cls_file = inspect.getfile(cls)
                            cls_line = inspect.getsourcelines(cls)[1]
                        except (OSError, TypeError):
                            pass  # Fall back to empty file/line for built-in or C-extension classes
                        errors.append(
                            DjustWarning(
                                "%s: on_mount[%d] is not callable (%s)."
                                % (cls_label, i, type(hook).__name__),
                                hint="Each on_mount entry must be a callable hook function.",
                                id="djust.V009",
                                fix_hint=(
                                    "Ensure all items in `on_mount` are callable "
                                    "in `%s`." % cls.__qualname__
                                ),
                                file_path=cls_file,
                                line_number=cls_line,
                            )
                        )

    # V010 -- TutorialMixin listed after LiveView in MRO (#691)
    _check_tutorial_mixin_mro(errors, LiveView)

    # V006 -- service instance in mount() (AST-based scan of project files)
    _check_service_instances_in_mount(errors)

    # V008 -- non-primitive type assignments in mount() (broader than V006)
    _check_non_primitive_assignments_in_mount(errors)

    return errors


def _check_tutorial_mixin_mro(errors, LiveView):
    """V010 (Error): Detect TutorialMixin listed after LiveView in the MRO.

    Django's ``View.__init__`` does not call ``super().__init__()``, so any
    mixin listed after a ``View``-derived class in the bases tuple never gets
    its ``__init__`` called.  When ``TutorialMixin`` is listed after
    ``LiveView``, its instance state (``tutorial_running``, signals, etc.) is
    never initialised and the tour silently fails at runtime.

    Fires ``djust.V010`` as an **Error** because the class is guaranteed to
    break at runtime — not a style issue.

    See: https://github.com/djust-org/djust/issues/691
    """
    if _is_check_suppressed("djust.V010"):
        return

    try:
        from djust.tutorials.mixin import TutorialMixin
    except ImportError:
        return

    from django.views import View

    for cls in _walk_subclasses(LiveView):
        module = getattr(cls, "__module__", "") or ""
        if module.startswith("djust.") or module.startswith("djust_"):
            if "test" not in module and "example" not in module:
                continue

        if TutorialMixin not in cls.__mro__:
            continue

        # Check that TutorialMixin appears before any View-derived class
        # in the *direct bases* (not the full MRO). If a user writes
        # ``class MyView(LiveView, TutorialMixin)``, TutorialMixin.__init__
        # is unreachable because View.__init__ breaks the super() chain.
        bases = cls.__bases__
        tutorial_idx = None
        view_idx = None
        for i, base in enumerate(bases):
            if tutorial_idx is None and issubclass(base, TutorialMixin):
                tutorial_idx = i
            if view_idx is None and issubclass(base, View):
                view_idx = i

        if tutorial_idx is not None and view_idx is not None and tutorial_idx > view_idx:
            cls_label = "%s.%s" % (cls.__module__, cls.__qualname__)
            cls_file = ""
            cls_line = None
            try:
                cls_file = inspect.getfile(cls)
                cls_line = inspect.getsourcelines(cls)[1]
            except (OSError, TypeError):
                pass  # Source introspection may fail for built-in or C-extension classes
            errors.append(
                DjustError(
                    "%s: TutorialMixin must be listed before LiveView in bases." % cls_label,
                    hint=(
                        "Change `class %s(LiveView, TutorialMixin)` to "
                        "`class %s(TutorialMixin, LiveView)`. Django's View.__init__ "
                        "does not call super().__init__(), so mixins listed after "
                        "LiveView never get initialised." % (cls.__qualname__, cls.__qualname__)
                    ),
                    id="djust.V010",
                    fix_hint=(
                        "Reorder bases: `class %s(TutorialMixin, LiveView):`" % cls.__qualname__
                    ),
                    file_path=cls_file,
                    line_number=cls_line,
                )
            )


# First-positional-arg extraction for a ``{% live_render %}`` tag body. The
# ``_LIVE_RENDER_TAG_RE`` capture group holds everything after ``live_render``;
# the first positional arg (the quoted dotted child path) is at the START.
# A bare-identifier first arg (``{% live_render some_var %}``) yields no match
# and is skipped — a dynamic path is unresolvable statically.
_LIVE_RENDER_FIRST_ARG_RE = re.compile(r"""^\s*["']([^"']+)["']""")


@register("djust")
def check_sticky_child_optin(app_configs, **kwargs):
    """V011 (Warning): sticky child opts into ``enable_state_snapshot`` but its
    embedding parent does not — ADR-018 Decision 5 enforcement.

    A sticky child is persisted across a WebSocket reconnect only when BOTH
    the child class AND its embedding parent class set
    ``enable_state_snapshot = True`` (the ``sticky_child_should_persist``
    both-opt-in gate). A child that opts in under a parent that does not gets
    silently-incomplete persistence — its save is skipped, its state is lost
    on reconnect. V011 surfaces that misconfiguration at ``manage.py check``
    time.

    Discovery (mirrors the A075 template scanner):

    1. Walk ``LiveView`` subclasses, build a ``template_name -> [parent cls]``
       map (a child template may be embedded under several parents).
    2. Walk every template file, for each ``{% live_render ... sticky=True %}``
       tag resolve the child class via ``import_string``.
    3. If the child opts in (``enable_state_snapshot`` + truthy ``sticky_id``)
       and a matched parent does NOT opt in → emit V011.

    Conservative skips (no false positives): unresolvable dynamic view paths,
    ``import_string`` failures, templates with no matching parent class, and
    children that themselves don't opt in are all skipped silently — the
    runtime one-shot warning (``warn_sticky_child_optin_skip``) is the safety
    net for cases the static scan can't see.
    """
    errors = []

    if _is_check_suppressed("djust.V011"):
        return errors

    try:
        from djust.live_view import LiveView
    except ImportError:
        return errors

    from django.utils.module_loading import import_string

    # Build template_name -> [parent LiveView class, ...]. Mirrors
    # check_liveviews' internal-class skip: djust-internal classes are
    # skipped UNLESS the module is a test/example module.
    parent_map = {}
    for cls in _walk_subclasses(LiveView):
        module = getattr(cls, "__module__", "") or ""
        if module.startswith("djust.") or module.startswith("djust_"):
            if "test" not in module and "example" not in module:
                continue
        tpl = cls.__dict__.get("template_name")
        if not tpl:
            continue
        parent_map.setdefault(tpl, []).append(cls)

    for filepath in _iter_template_files(_get_template_dirs()):
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError:
            continue

        # The template's path RELATIVE to its template dir is what
        # ``template_name`` holds (e.g. ``parent_page.html`` or
        # ``app/parent.html``). Resolve against each configured dir.
        relname = None
        for tpl_dir in _get_template_dirs():
            try:
                candidate = os.path.relpath(filepath, tpl_dir)
            except ValueError:
                continue
            if not candidate.startswith(".."):
                relname = candidate
                break
        if relname is None:
            continue

        scan_source = _strip_verbatim_blocks(content)
        for match in _LIVE_RENDER_TAG_RE.finditer(scan_source):
            args = match.group(1)

            # Only sticky embeds are persistable (Decision 1) — a non-sticky
            # ``{% live_render %}`` is explicitly unsupported, not flagged.
            sticky_falsy = bool(_LIVE_RENDER_STICKY_FALSY_RE.search(args))
            sticky_truthy = bool(_LIVE_RENDER_STICKY_TRUTHY_RE.search(args)) and not sticky_falsy
            if not sticky_truthy:
                continue

            arg_match = _LIVE_RENDER_FIRST_ARG_RE.match(args)
            if not arg_match:
                # Dynamic (bare-identifier) child path — unresolvable
                # statically; the runtime warning covers it.
                continue
            child_path = arg_match.group(1)

            try:
                child_cls = import_string(child_path)
            except (ImportError, AttributeError, ModuleNotFoundError, ValueError):
                # A broken path is A075/runtime's problem, not V011's.
                continue

            # Child opt-in test: only an opted-in sticky child can be a
            # Decision-5 misconfiguration. "Neither opts in" is silent.
            child_opts_in = bool(getattr(child_cls, "enable_state_snapshot", False)) and bool(
                getattr(child_cls, "sticky_id", None)
            )
            if not child_opts_in:
                continue

            matched_parents = parent_map.get(relname, [])
            if not matched_parents:
                # No parent class maps to this template — can't statically
                # determine the parent. Skip conservatively.
                continue

            for parent_cls in matched_parents:
                if getattr(parent_cls, "enable_state_snapshot", False):
                    continue  # this parent opts in — tree-consistent restore

                child_label = "%s.%s" % (child_cls.__module__, child_cls.__qualname__)
                parent_label = "%s.%s" % (parent_cls.__module__, parent_cls.__qualname__)

                child_file = ""
                child_line = None
                try:
                    child_file = inspect.getfile(child_cls)
                    child_line = inspect.getsourcelines(child_cls)[1]
                except (OSError, TypeError):
                    pass  # Source introspection may fail for some classes.

                errors.append(
                    DjustWarning(
                        "%s: used as a sticky child with enable_state_snapshot=True, "
                        "but embedding parent %s does not opt in — the child's state "
                        "will be silently dropped on reconnect." % (child_label, parent_label),
                        hint=(
                            "ADR-018 Decision 5: a sticky child is persisted across a "
                            "WebSocket reconnect only when BOTH the child class and its "
                            "embedding parent class set enable_state_snapshot = True. "
                            "Requiring the parent too keeps the reconnect-restored "
                            "subtree tree-consistent — a child must not restore to "
                            "saved state while its parent re-mounts fresh. Suppress "
                            "with DJUST_CONFIG = {'suppress_checks': ['V011']} if you "
                            "have a deliberate reason."
                        ),
                        id="djust.V011",
                        fix_hint=(
                            "Add `enable_state_snapshot = True` to `%s`, or remove it "
                            "from `%s`." % (parent_cls.__qualname__, child_cls.__qualname__)
                        ),
                        file_path=child_file,
                        line_number=child_line,
                    )
                )

    return errors


def _check_service_instances_in_mount(errors):
    """V006 (Warning): Detect service/client/session instantiation in mount() methods via AST.

    High-confidence subset of V008. Fires for names matching _SERVICE_INSTANCE_KEYWORDS
    (Service, Client, Session, API, Connection). Because V006 already emits a Warning for
    these patterns, V008 explicitly skips them so developers see only one message per line.
    """
    app_dirs = _get_project_app_dirs()
    if not app_dirs:
        return

    for filepath in _iter_python_files(app_dirs):
        tree, source_lines = _parse_python_file(filepath)
        if tree is None:
            continue

        relpath = os.path.relpath(filepath)

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue

            # Find mount() methods inside class definitions
            for item in node.body:
                if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if item.name != "mount":
                    continue

                # Walk the mount body looking for self.X = SomeService(...)
                for stmt in ast.walk(item):
                    if not isinstance(stmt, ast.Assign):
                        continue
                    for target in stmt.targets:
                        if not isinstance(target, ast.Attribute):
                            continue
                        if not (isinstance(target.value, ast.Name) and target.value.id == "self"):
                            continue
                        # Check if the value is a Call whose function name
                        # contains service-like keywords
                        if not isinstance(stmt.value, ast.Call):
                            continue
                        call_name = _get_call_name(stmt.value)
                        if call_name and _SERVICE_INSTANCE_KEYWORDS.search(call_name):
                            if not _has_noqa(source_lines, stmt.lineno, "V006"):
                                errors.append(
                                    DjustWarning(
                                        "%s:%d -- Service instance '%s' assigned in mount(). "
                                        "Service instances cannot be serialized."
                                        % (relpath, stmt.lineno, target.attr),
                                        hint=(
                                            "Use a helper method pattern instead. "
                                            "See: docs/guides/services.md"
                                        ),
                                        id="djust.V006",
                                        fix_hint=(
                                            "Move `self.%s = %s(...)` out of mount() into a "
                                            "helper method or property at line %d in `%s`."
                                            % (target.attr, call_name, stmt.lineno, relpath)
                                        ),
                                        file_path=filepath,
                                        line_number=stmt.lineno,
                                    )
                                )


def _check_non_primitive_assignments_in_mount(errors):
    """V008: Detect assignments of non-primitive types in mount() methods via AST.

    This is a broader, lower-confidence check than V006. V006 covers a specific
    well-known pattern (service/client/session names → Warning); V008 catches
    *all* non-primitive call results that V006 doesn't already flag (→ Info).

    The two checks are deliberately non-overlapping:
    - Assignments whose call name matches _SERVICE_INSTANCE_KEYWORDS are left
      to V006 (Warning), so developers see one message, not two.
    - Everything else that is not a primitive literal is reported by V008 (Info)
      because it *might* be serializable (e.g. a dataclass) but needs annotation.

    Catches assignments like:
    - self.items = []  (OK - primitive)
    - self.data = CustomClass()  (V008 Info - check serialisability)
    - self.service = PaymentService()  (V006 Warning - skipped here)
    - self.count = 0  (OK - primitive)

    Related to issue #292: Silent str() fallback when non-serializable objects
    are stored in LiveView state. This check helps catch these at development time
    before they cause runtime AttributeError on deserialization.

    Users can suppress with # noqa: V008 if they know the type is serializable,
    or globally with DJUST_CONFIG = {'suppress_checks': ['V008']}.
    """
    if _is_check_suppressed("djust.V008"):
        return

    app_dirs = _get_project_app_dirs()
    if not app_dirs:
        return

    # Primitive types that are always serializable
    SAFE_TYPES = {
        "list",
        "dict",
        "set",
        "tuple",
        "str",
        "int",
        "float",
        "bool",
        "List",
        "Dict",
        "Set",
        "Tuple",
    }

    for filepath in _iter_python_files(app_dirs):
        tree, source_lines = _parse_python_file(filepath)
        if tree is None:
            continue

        relpath = os.path.relpath(filepath)

        # Build a set of module-level function names whose return annotation is a
        # primitive type.  Calls to these functions are safe to assign in mount().
        primitive_return_funcs = _build_primitive_return_funcs(tree)

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue

            # Find mount() methods inside class definitions
            for item in node.body:
                if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if item.name != "mount":
                    continue

                # Walk the mount body looking for self.X = NonPrimitive(...)
                for stmt in ast.walk(item):
                    if not isinstance(stmt, ast.Assign):
                        continue
                    for target in stmt.targets:
                        if not isinstance(target, ast.Attribute):
                            continue
                        if not (isinstance(target.value, ast.Name) and target.value.id == "self"):
                            continue

                        # Skip private attributes (self._foo)
                        if target.attr.startswith("_"):
                            continue

                        # Check if the value is a Call (instantiation or function call)
                        if not isinstance(stmt.value, ast.Call):
                            continue

                        call_name = _get_call_name(stmt.value)
                        if call_name and call_name not in SAFE_TYPES:
                            # Skip patterns already reported by V006 (Warning) to
                            # avoid emitting a duplicate V008 (Info) for the same line.
                            if _SERVICE_INSTANCE_KEYWORDS.search(call_name):
                                continue
                            # Skip calls to module-level functions whose return
                            # annotation declares a primitive type (e.g. -> str).
                            if call_name in primitive_return_funcs:
                                continue
                            # This is a non-primitive instantiation
                            if not _has_noqa(source_lines, stmt.lineno, "V008"):
                                errors.append(
                                    DjustInfo(
                                        "%s:%d -- Non-primitive type '%s' assigned to self.%s in mount(). "
                                        "Ensure this type is JSON-serializable."
                                        % (relpath, stmt.lineno, call_name, target.attr),
                                        hint=(
                                            "If '%s' is not serializable, use self._%s instead "
                                            "or re-initialize in event handlers. "
                                            "See: docs/guides/services.md"
                                            % (call_name, target.attr)
                                        ),
                                        id="djust.V008",
                                        fix_hint=(
                                            "If `%s` is not serializable, rename to `self._%s` "
                                            "or move initialization out of mount() at line %d in `%s`."
                                            % (target.attr, target.attr, stmt.lineno, relpath)
                                        ),
                                        file_path=filepath,
                                        line_number=stmt.lineno,
                                    )
                                )


def _get_call_name(call_node):
    """Extract a human-readable name from a Call node's function."""
    func = call_node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        # e.g., boto3.client -> "boto3.client"
        parts = []
        current = func
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        return ".".join(reversed(parts))
    return None


_PRIMITIVE_ANNOTATION_NAMES = frozenset(
    {
        "str",
        "int",
        "bool",
        "float",
        "bytes",
        "list",
        "dict",
        "set",
        "tuple",
        "List",
        "Dict",
        "Set",
        "Tuple",
    }
)


def _build_primitive_return_funcs(tree):
    """Return the set of top-level function names whose return annotation is a primitive type.

    Only inspects module-level (top-level) function definitions.  If a function
    is annotated with ``-> str``, ``-> int``, ``-> bool``, ``-> float``,
    ``-> bytes``, or any of the collection primitives (``list``, ``dict``,
    ``set``, ``tuple`` and their capitalised aliases), its name is included in
    the returned set.

    This is used by the V008 check to avoid false-positive warnings when
    ``mount()`` assigns the result of a helper function that is provably
    primitive because of its return-type annotation.
    """
    safe_funcs = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.returns is None:
            continue
        annotation = node.returns
        ann_name = None
        if isinstance(annotation, ast.Name):
            ann_name = annotation.id
        elif isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
            # PEP 563 / ``from __future__ import annotations`` stringifies all annotations
            ann_name = annotation.value
        if ann_name in _PRIMITIVE_ANNOTATION_NAMES:
            safe_funcs.add(node.name)
    return safe_funcs


def _collect_patch_param_names(class_node, original_source):
    """Collect URL param names from self.patch() calls in a class.

    Inspects all methods in the class for ``self.patch(...)`` calls and extracts
    param names from dict-style (``{"tab": ...}``) and query-string-style
    (``"?tab=value"`` or f-strings) arguments.

    Returns a set of lowercase param name strings, e.g. ``{"tab", "view"}``.
    """
    param_names = set()
    for method in class_node.body:
        if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for call_node in ast.walk(method):
            if not isinstance(call_node, ast.Call):
                continue
            func = call_node.func
            # Match self.patch(...)
            if not (
                isinstance(func, ast.Attribute)
                and func.attr == "patch"
                and isinstance(func.value, ast.Name)
                and func.value.id == "self"
            ):
                continue
            if not call_node.args:
                continue
            first_arg = call_node.args[0]
            # Dict-style: self.patch({"tab": val, ...})
            if isinstance(first_arg, ast.Dict):
                for key in first_arg.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        param_names.add(key.value)
            # Constant string: self.patch("?tab=value")
            elif isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                for m in re.finditer(r"[?&](\w+)=", first_arg.value):
                    param_names.add(m.group(1))
            # f-string: self.patch(f"?tab={val}") — extract from the source segment
            elif isinstance(first_arg, ast.JoinedStr):
                seg = ast.get_source_segment(original_source, first_arg) or ""
                for m in re.finditer(r"[?&](\w+)=", seg):
                    param_names.add(m.group(1))
    return param_names


def _nav_var_matches_patch_params(var_name, param_names):
    """Return True if *var_name* plausibly corresponds to a URL param in *param_names*.

    Checks direct match and simple prefix/suffix stripping so that, for example,
    ``active_tab`` matches a param named ``tab``.
    """
    if var_name in param_names:
        return True
    # Strip common adjective prefixes: active_, current_, selected_
    base = var_name.split("_")[-1]  # "active_tab" → "tab", "current_view" → "view"
    return base in param_names


def _check_navigation_state_in_handlers(errors):
    """Q010: Heuristic to detect event handlers that set navigation state without patching.

    Lower-confidence check that looks for @event_handler methods whose body primarily
    sets navigation state variables (self.active_view, self.current_tab, etc.) without
    using patch() or handle_params(). Suggests converting to dj-patch pattern.

    To reduce false positives, Q010 only fires when the class already uses
    ``self.patch()`` with URL params somewhere, AND the nav variable name matches
    one of those param names.  Variables that merely sound like navigation but are
    not URL params (e.g. ``self.active_tab`` for CSS toggling) are therefore
    silently skipped.

    This is INFO level as it's a heuristic and may have false positives.
    """
    app_dirs = _get_project_app_dirs()
    if not app_dirs:
        return

    # Navigation state variable patterns
    NAV_STATE_VARS = re.compile(
        r"self\.(active_view|current_tab|selected_page|current_section|active_tab|selected_view)"
    )

    for filepath in _iter_python_files(app_dirs):
        tree, source_lines = _parse_python_file(filepath)
        if tree is None:
            continue

        # Read the original source for ast.get_source_segment
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
                original_source = fh.read()
        except OSError:
            continue

        relpath = os.path.relpath(filepath)

        # Look for classes that might be LiveViews
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue

            # Cross-reference: collect URL param names from self.patch() calls in
            # this class.  Only flag variables whose names appear in this set so
            # we avoid false positives for nav-sounding names that are not URL state.
            patch_params = _collect_patch_param_names(node, original_source)

            # Check each method in the class
            for item in node.body:
                if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue

                # Skip non-event-handler methods
                if not any(
                    isinstance(deco, ast.Name)
                    and deco.id == "event_handler"
                    or isinstance(deco, ast.Call)
                    and isinstance(deco.func, ast.Name)
                    and deco.func.id == "event_handler"
                    for deco in item.decorator_list
                ):
                    continue

                # Check if the method body sets navigation state
                method_source = ast.get_source_segment(original_source, item) or ""
                nav_match = NAV_STATE_VARS.search(method_source)

                if not nav_match:
                    continue

                var_name = nav_match.group(1)

                # Only flag when the variable name is confirmed to be a URL param
                # used elsewhere via self.patch() — prevents false positives for
                # nav-sounding names that are not URL state.
                if not patch_params or not _nav_var_matches_patch_params(var_name, patch_params):
                    continue

                # Check if it uses patch() or handle_params (indicators it's already using patching)
                has_patch_pattern = "patch(" in method_source or "handle_params" in method_source

                if not has_patch_pattern:
                    # Check for noqa on function definition line or any decorator line
                    has_noqa_suppression = False
                    for deco in item.decorator_list:
                        if _has_noqa(source_lines, deco.lineno, "Q010"):
                            has_noqa_suppression = True
                            break
                    if not has_noqa_suppression and _has_noqa(source_lines, item.lineno, "Q010"):
                        has_noqa_suppression = True

                    if not has_noqa_suppression:
                        errors.append(
                            DjustInfo(
                                "%s:%d -- Event handler '%s.%s()' sets %s without using patch(). "
                                "Consider using dj-patch for URL updates."
                                % (relpath, item.lineno, node.name, item.name, var_name),
                                hint=(
                                    "Navigation state changes are better handled with dj-patch + handle_params(). "
                                    "This enables URL updates and back-button support. "
                                    'Example: Replace dj-click with dj-patch="?tab=value" and handle in handle_params().'
                                ),
                                id="djust.Q010",
                                fix_hint=(
                                    "Convert method `%s` to use handle_params() instead of direct state "
                                    "assignment at line %d in `%s`."
                                    % (item.name, item.lineno, relpath)
                                ),
                                file_path=filepath,
                                line_number=item.lineno,
                            )
                        )


# ---------------------------------------------------------------------------
# Security checks (S0xx) -- AST-based
# ---------------------------------------------------------------------------


@register("djust")
def check_security(app_configs, **kwargs):
    """AST-based security checks on project Python files."""
    errors = []
    app_dirs = _get_project_app_dirs()
    if not app_dirs:
        return errors

    for filepath in _iter_python_files(app_dirs):
        tree, source_lines = _parse_python_file(filepath)
        if tree is None:
            continue

        relpath = os.path.relpath(filepath)

        for node in ast.walk(tree):
            # S001 -- mark_safe(f'...') with interpolated values
            if isinstance(node, ast.Call):
                func = node.func
                func_name = None
                if isinstance(func, ast.Name):
                    func_name = func.id
                elif isinstance(func, ast.Attribute):
                    func_name = func.attr

                if func_name == "mark_safe" and node.args:
                    arg = node.args[0]
                    if isinstance(arg, ast.JoinedStr) and not _has_noqa(
                        source_lines, node.lineno, "S001"
                    ):
                        errors.append(
                            DjustError(
                                "%s:%d -- mark_safe() with f-string is a XSS risk."
                                % (relpath, node.lineno),
                                hint="Use format_html() instead of mark_safe(f'...').",
                                id="djust.S001",
                                fix_hint=(
                                    "Replace `mark_safe(f'...')` with `format_html()` "
                                    "at line %d in `%s`." % (node.lineno, relpath)
                                ),
                                file_path=filepath,
                                line_number=node.lineno,
                            )
                        )

            # S002 -- @csrf_exempt without justification comment
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for deco in node.decorator_list:
                    deco_name = None
                    if isinstance(deco, ast.Name):
                        deco_name = deco.id
                    elif isinstance(deco, ast.Attribute):
                        deco_name = deco.attr
                    if deco_name == "csrf_exempt":
                        # Check for a comment/docstring justification
                        has_justification = False
                        if (
                            node.body
                            and isinstance(node.body[0], ast.Expr)
                            and isinstance(node.body[0].value, ast.Constant)
                        ):
                            doc = node.body[0].value.value
                            if "csrf" in doc.lower():
                                has_justification = True
                        if not has_justification and not _has_noqa(
                            source_lines, deco.lineno, "S002"
                        ):
                            errors.append(
                                DjustWarning(
                                    "%s:%d -- @csrf_exempt without justification."
                                    % (relpath, node.lineno),
                                    hint="Add a docstring explaining why CSRF protection is disabled.",
                                    id="djust.S002",
                                    fix_hint=(
                                        "Add a docstring mentioning 'csrf' to function "
                                        "`%s` at line %d in `%s`."
                                        % (node.name, node.lineno, relpath)
                                    ),
                                    file_path=filepath,
                                    line_number=node.lineno,
                                )
                            )

            # S003 -- bare except: pass
            if isinstance(node, ast.ExceptHandler):
                if node.type is None:  # bare except
                    if (
                        len(node.body) == 1
                        and isinstance(node.body[0], ast.Pass)
                        and not _has_noqa(source_lines, node.lineno, "S003")
                    ):
                        errors.append(
                            DjustWarning(
                                "%s:%d -- bare 'except: pass' swallows all exceptions."
                                % (relpath, node.lineno),
                                hint="Catch a specific exception and log it, or re-raise.",
                                id="djust.S003",
                                fix_hint=(
                                    "Replace bare `except: pass` with a specific exception "
                                    "type (e.g., `except Exception:`) and add logging, "
                                    "at line %d in `%s`." % (node.lineno, relpath)
                                ),
                                file_path=filepath,
                                line_number=node.lineno,
                            )
                        )

    return errors


# ---------------------------------------------------------------------------
# Template checks (T0xx)
# ---------------------------------------------------------------------------

_DEPRECATED_ATTR_RE = re.compile(
    r"@(click|input|change|submit|blur|focus|keydown|keyup|mouseenter|mouseleave)="
)
# A070 / A071 — ``{% dj_activity %}`` block tag scanner (v0.7.0).
# Captures the raw argument list after the tag name so we can inspect it
# for a ``name=`` / first-positional string and detect missing / duplicate
# names without invoking the full Django template parser. Multi-line tag
# bodies are handled via ``re.DOTALL``.
_DJ_ACTIVITY_TAG_RE = re.compile(r"\{%\s*dj_activity\b([^%]*?)%\}", re.DOTALL)
# Activity name extractor. We accept three forms of the first argument:
#   group 1: double-quoted string literal -> "panel-name"
#   group 2: single-quoted string literal -> 'panel-name'
#   group 3: bare identifier or dotted path -> panel_name, view.panel_name
# A bare identifier is treated as "name present" but resolves at render
# time, so the A071 duplicate check cannot compare it to another tag's
# identifier — we skip A071 for identifier-form names. Only emit A070
# when NONE of the three groups match (truly missing name).
_DJ_ACTIVITY_NAME_RE = re.compile(
    r"""^\s*(?:name\s*=\s*)?(?:"([^"]+)"|'([^']+)'|([A-Za-z_][\w.]*))\s*(?:$|\s)"""
)
_DJ_ROOT_RE = re.compile(r"dj-root")
_INCLUDE_RE = re.compile(r"\{%\s*include\s+")
_LIVEVIEW_CONTENT_RE = re.compile(r"\{\{\s*liveview_content\s*\|\s*safe\s*\}\}")
_DOC_DJUST_EVENT_RE = re.compile(r"""document\s*\.\s*addEventListener\s*\(\s*['"]djust:""")
_NAV_DATA_ATTRS = re.compile(r"data-(view|tab|page|section)")  # Navigation-style data attributes
_DJ_EVENT_DIRECTIVES_RE = re.compile(
    r"dj-(click|input|change|submit|blur|focus|keydown|keyup|mouseenter|mouseleave|window-\w+|document-\w+|click-away|shortcut)="
)
_DJ_COMPONENT_RE = re.compile(r"dj-component")
# #1096 — opt-out marker for fragment templates that are intentionally
# {% include %}d from a parent LiveView root. Fragment authors annotate
# the file with `{# djust:partial #}` (case-insensitive, optional surrounding
# whitespace) to silence T012 without introducing a global suppression.
_DJ_PARTIAL_MARKER_RE = re.compile(r"\{#\s*djust\s*:\s*partial\s*#\}", re.IGNORECASE)
_DEPRECATED_DATA_DJ_ID_RE = re.compile(r"""data-dj-id\s*=\s*["'][^"']*["']""")
# A090 — scanner for {% djust_markdown %} (v0.7.0). Fires info-level once
# per project when the tag is detected, confirming the Rust-side safe
# renderer is in use (raw HTML escaped, provisional-line splitter active).
_DJ_MARKDOWN_TAG_RE = re.compile(r"\{%\s*djust_markdown\b")

# A075 — `{% live_render ... %}` sticky+lazy collision scanner (v0.9.1, #1146).
# Captures the raw kwarg-list body so we can inspect it for both
# ``sticky=`` and ``lazy=`` truthy assignments. The two are mutually
# exclusive at tag-eval time (``TemplateSyntaxError`` in
# ``live_tags.live_render``); A075 promotes that runtime error to a
# startup-time warning so the misuse never reaches a request.
_LIVE_RENDER_TAG_RE = re.compile(r"\{%\s*live_render\b([^%]*?)%\}", re.DOTALL)
# Truthy assignments for sticky=/lazy=. Matches:
#   sticky=True / lazy=True  (literal)
#   sticky="..." / lazy="..." with non-empty quoted value (any non-empty string)
#   sticky=<bare-identifier> / lazy=<bare-identifier> (variable, conservatively
#       treated as truthy at scan time — false positives are silenceable
#       via the suppress_checks knob)
# False / "" / 0 are explicitly excluded.
_LIVE_RENDER_STICKY_TRUTHY_RE = re.compile(
    r"""\bsticky\s*=\s*(?:True|"[^"]+"|'[^']+'|[A-Za-z_]\w*)"""
)
_LIVE_RENDER_LAZY_TRUTHY_RE = re.compile(
    r"""\blazy\s*=\s*(?:True|"[^"]+"|'[^']+'|[A-Za-z_]\w*|\{[^}]*\})"""
)
# Explicit "this kwarg is FALSY" — overrides the truthy match above. We
# exclude these to avoid flagging ``sticky=False lazy=True`` (a legitimate
# pattern when toggling at template-evaluation time).
_LIVE_RENDER_STICKY_FALSY_RE = re.compile(r"""\bsticky\s*=\s*(?:False|"\s*"|'\s*'|0)\b""")
_LIVE_RENDER_LAZY_FALSY_RE = re.compile(r"""\blazy\s*=\s*(?:False|"\s*"|'\s*'|0)\b""")

# #1004 — `{% verbatim %}...{% endverbatim %}` regions hold raw template
# source that Django renders as-is without parsing. Docs and marketing
# templates routinely wrap literal `{% dj_activity %}` examples in
# verbatim blocks; the A070 / A071 scanner used to treat those as real
# uninstrumented activity calls and false-positive. The pre-processor
# below replaces each verbatim region with whitespace (preserving line
# breaks) so downstream regex scanners see no `{% dj_activity %}` text
# inside the region, while line numbers from the original source remain
# accurate for any matches OUTSIDE the region.
#
# Both unnamed (`{% verbatim %}`) and named (`{% verbatim foo %}`)
# variants are matched. The endverbatim form must match the opening
# variant, but Django allows either to close — we accept either since
# the goal is to redact the BODY of the region, not validate Django
# syntax (the template engine itself will reject malformed verbatim
# blocks at render time).
_VERBATIM_BLOCK_RE = re.compile(
    r"\{%\s*verbatim\b[^%]*%\}.*?\{%\s*endverbatim\b[^%]*%\}",
    re.DOTALL,
)


def _strip_verbatim_blocks(content: str) -> str:
    """Replace the BODY of every ``{% verbatim %}...{% endverbatim %}`` region
    with whitespace, preserving newlines so line numbers stay accurate for
    matches outside the region.

    Used before regex-scanning for A070 / A071 (and any future scanner
    that walks template source as raw text) — without this, literal
    `{% dj_activity %}` examples wrapped in verbatim blocks (a common
    pattern on docs / marketing pages that document the tag) get
    flagged as real uninstrumented activity calls.

    The verbatim tags themselves are kept (so the scanner doesn't see
    a totally absent region), but their content is blanked. We replace
    every non-newline character with a space and keep newlines verbatim
    — line numbers stay aligned with the original source.

    Returns ``content`` unchanged if no verbatim region is present (the
    common case). Cost: one regex pass plus one rewrite when at least
    one verbatim region exists.
    """
    if "verbatim" not in content:
        return content

    def _redact(match: re.Match) -> str:
        body = match.group(0)
        # Preserve newlines, blank everything else.
        return "".join("\n" if ch == "\n" else " " for ch in body)

    return _VERBATIM_BLOCK_RE.sub(_redact, content)


# ---------------------------------------------------------------------------
# Accessibility (Y0xx) scanners
# ---------------------------------------------------------------------------
#
# Y001 — interactive element (<button>/<a>) with no accessible name.
# Y002 — <img> tag missing an `alt` attribute (WCAG 1.1.1, A-level).
# Y003 — form control (<input>/<select>/<textarea>) with no associated
#        label (WCAG 1.3.1 / 3.3.2, A-level).
# Y004 — positive tabindex (WCAG 2.4.3, Focus Order anti-pattern).
#
# All are deliberately low-ambiguity a11y defects so the regex heuristics
# carry near-zero false positives (see the #1060 dogfood discipline note
# in the Stage-4 plan). The category is extensible — Y005+ are
# single-function-body additions in a follow-up.

# Y001 — captures the OPENING tag (group "open"), the tag name (group
# "tag"), and the inner content up to the matching close tag (group
# "inner"). Restricted to <button> and <a>; <a> only counts as
# interactive when it carries an href (a bare <a> is an anchor target,
# not a control). The follow-up heuristic in _content_is_icon_only()
# decides whether the inner content is icon-only.
_INTERACTIVE_EL_RE = re.compile(
    r"<(?P<tag>button|a)\b(?P<open>[^>]*)>(?P<inner>.*?)</(?P=tag)>",
    re.IGNORECASE | re.DOTALL,
)
# An accessible-name attribute present on the opening tag silences Y001.
# The `(?<![\w-])` lookbehind (in place of a plain `\b`) ensures a
# preceding hyphen also blocks the match, so a custom attribute like
# `data-aria-label='x'` is NOT mistaken for the element's real
# `aria-label` and used to wrongly silence a genuine Y001.
_ACCESSIBLE_NAME_ATTR_RE = re.compile(
    r"""(?<![\w-])(aria-label|aria-labelledby|title)\s*=\s*["'][^"']*["']""",
    re.IGNORECASE,
)
# href presence on an <a> opening tag (interactive only when linked).
# The `(?<![\w-])` lookbehind (in place of a plain `\b`) ensures a
# preceding hyphen also blocks the match, so a custom attribute like
# `data-href='/x'` is NOT mistaken for the anchor's real `href`.
_HREF_ATTR_RE = re.compile(r"""(?<![\w-])href\s*=\s*["'][^"']*["']""", re.IGNORECASE)
# "Icon-only" = the inner content is composed exclusively of HTML
# entities, <svg>...</svg>, self-closing tags (<i .../>, <img .../>),
# <i>...</i> / <span>...</span> wrappers whose own content is
# icon-only, Django template comments, and whitespace — i.e. no
# human-readable text and no {{ variable }} interpolation that could
# resolve to a label at render time.
_HTML_ENTITY_RE = re.compile(r"&(?:#\d+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]+);")
_SVG_BLOCK_RE = re.compile(r"<svg\b.*?</svg>", re.IGNORECASE | re.DOTALL)
_SELF_CLOSING_TAG_RE = re.compile(r"<[a-zA-Z][^>]*/\s*>")
_ICON_WRAPPER_RE = re.compile(
    r"<(?P<w>i|span|em)\b[^>]*>(?P<wi>.*?)</(?P=w)>", re.IGNORECASE | re.DOTALL
)
_TEMPLATE_COMMENT_RE = re.compile(r"\{#.*?#\}", re.DOTALL)

# Y002 — <img> tag missing an `alt` attribute. `alt=""` is the WCAG-
# correct way to mark a decorative image, so the regex only flags an
# <img> with NO `alt` token at all. {% ... %} / {{ ... }} dynamic
# attribute injection is treated as "alt may be present" (no flag).
_IMG_TAG_RE = re.compile(r"<img\b[^>]*?/?>", re.IGNORECASE | re.DOTALL)
# The `(?<![\w-])` lookbehind (in place of a plain `\b`) ensures a
# preceding hyphen also blocks the match, so a custom attribute like
# `data-alt='x'` is NOT mistaken for the image's real `alt` attribute.
_IMG_HAS_ALT_RE = re.compile(r"""(?<![\w-])alt\s*=""", re.IGNORECASE)
_IMG_DYNAMIC_ATTRS_RE = re.compile(r"\{[%{].*?[%}]\}", re.DOTALL)

# Y003 — form control (<input>/<select>/<textarea>) with no associated
# accessible name (WCAG 1.3.1 / 3.3.2, Level A). Matches the OPENING tag
# only (group "tag" = element name, group "open" = attribute text). For
# <input>, `type` values in {hidden, submit, button, reset, image} are
# skipped — those are not user-named text controls (submit/button/reset
# get their name from `value`, image inputs from `alt`).
_FORM_CONTROL_RE = re.compile(
    r"<(?P<tag>input|select|textarea)\b(?P<open>[^>]*)>",
    re.IGNORECASE | re.DOTALL,
)
# <input type="..."> extraction — used to skip non-text-control types.
# The `(?<![\w-])` lookbehind (in place of a plain `\b`) ensures a
# preceding hyphen also blocks the match, so `data-type='hidden'` is
# NOT mistaken for the control's real `type` attribute.
_INPUT_TYPE_RE = re.compile(r"""(?<![\w-])type\s*=\s*["']?\s*([a-zA-Z]+)""", re.IGNORECASE)
# <input> types that are NOT user-named text controls (no Y003 flag).
_Y003_SKIPPED_INPUT_TYPES = frozenset({"hidden", "submit", "button", "reset", "image"})
# An `id="X"` attribute on a form control — pairs with a <label for="X">.
# The `(?<![\w-])` lookbehind (in place of a plain `\b`) ensures a
# preceding hyphen also blocks the match, so a custom attribute like
# `data-id='X'` is NOT mistaken for the control's real `id` and wrongly
# paired with an unrelated <label for='X'>.
_CONTROL_ID_RE = re.compile(r"""(?<![\w-])id\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
# A <label for="X"> attribute (the `for` value, file-scoped). The set of
# all `for` values silences any control whose `id` is in the set.
_LABEL_FOR_RE = re.compile(
    r"""<label\b[^>]*?\bfor\s*=\s*["']([^"']+)["']""", re.IGNORECASE | re.DOTALL
)
# A <label>...</label> span — a control appearing inside one is wrapped
# (its accessible name comes from the label text).
_LABEL_BLOCK_RE = re.compile(r"<label\b[^>]*>.*?</label>", re.IGNORECASE | re.DOTALL)
# {% ... %} / {{ ... }} dynamic attribute injection on a control's
# opening tag — the id / aria-* may be injected at render time (no flag).
_CONTROL_DYNAMIC_ATTRS_RE = re.compile(r"\{[%{].*?[%}]\}", re.DOTALL)

# Y004 — positive tabindex (WCAG 2.4.3, Focus Order anti-pattern). Only
# a value matching `[1-9]\d*` (a positive integer) is flagged; `0`, `-1`,
# and `{{ }}`-interpolated values do not match the body and are silent.
# The `(?<![\w-])` lookbehind (in place of a plain `\b`) ensures a
# preceding hyphen also blocks the match, so a JS-driven custom
# attribute like `data-tabindex='5'` is NOT flagged as a Y004 defect.
_POSITIVE_TABINDEX_RE = re.compile(
    r"""(?<![\w-])tabindex\s*=\s*["']\s*([1-9]\d*)\s*["']""", re.IGNORECASE
)


def _content_is_icon_only(inner: str) -> bool:
    """Return True if *inner* has no human-readable accessible text.

    Used by Y001 to decide whether a <button>/<a> needs an explicit
    accessible-name attribute. Returns True only when the inner content
    is exclusively HTML entities, <svg> blocks, self-closing tags,
    icon-wrapper elements (<i>/<span>/<em>) that are themselves
    icon-only, template comments, and whitespace.

    A {{ variable }} interpolation is conservatively treated as
    "may resolve to a label" → returns False (no flag), keeping the
    false-positive rate near zero per the #1060 dogfood discipline.
    """
    stripped = inner
    # Template comments carry no rendered content.
    stripped = _TEMPLATE_COMMENT_RE.sub(" ", stripped)
    # A {{ ... }} or {% ... %} could render visible text — bail out
    # (treat as "has a name", no flag).
    if "{{" in stripped or "{%" in stripped:
        return False
    # Recursively unwrap icon-wrapper elements so <span><svg/></span>
    # is still recognised as icon-only.
    prev = None
    while prev != stripped:
        prev = stripped
        stripped = _ICON_WRAPPER_RE.sub(lambda m: " " + m.group("wi") + " ", stripped)
    stripped = _SVG_BLOCK_RE.sub(" ", stripped)
    stripped = _SELF_CLOSING_TAG_RE.sub(" ", stripped)
    stripped = _HTML_ENTITY_RE.sub(" ", stripped)
    # Whatever remains must be whitespace only for the element to be
    # "icon-only" (no accessible name).
    return stripped.strip() == ""


@register("djust")
def check_accessibility(app_configs, **kwargs):
    """Regex-scan template files for ARIA/WCAG accessibility issues.

    Checks:

    - **Y001** — an interactive ``<button>`` / ``<a href>`` whose visible
      content is icon-only (HTML entity, ``<svg>``, ``<i>``/``<span>``
      icon wrapper) and which has no ``aria-label`` / ``aria-labelledby``
      / ``title``. Screen-reader users hear nothing for such a control.
    - **Y002** — an ``<img>`` tag with no ``alt`` attribute (WCAG 1.1.1,
      Level A). ``alt=""`` (decorative image) is correct and not flagged.
    - **Y003** — a form control (``<input>`` / ``<select>`` /
      ``<textarea>``) with no associated label (WCAG 1.3.1 / 3.3.2,
      Level A). Satisfied by a ``<label for>`` pairing the control's
      ``id``, a wrapping ``<label>`` element, ``aria-label``, or
      ``aria-labelledby``. ``<input>`` types ``hidden`` / ``submit`` /
      ``button`` / ``reset`` / ``image`` are not flagged.
    - **Y004** — an element with a positive ``tabindex`` (WCAG 2.4.3,
      Focus Order). ``tabindex="0"`` and ``tabindex="-1"`` are valid and
      not flagged.

    All emit :class:`DjustWarning` (not error) so a stray false positive
    never fails ``manage.py check``; all are suppressible via
    ``DJUST_CONFIG['suppress_checks']``.
    """
    errors = []

    y001_suppressed = _is_check_suppressed("djust.Y001")
    y002_suppressed = _is_check_suppressed("djust.Y002")
    y003_suppressed = _is_check_suppressed("djust.Y003")
    y004_suppressed = _is_check_suppressed("djust.Y004")
    if y001_suppressed and y002_suppressed and y003_suppressed and y004_suppressed:
        return errors

    tpl_dirs = _get_template_dirs()
    if not tpl_dirs:
        return errors

    for filepath in _iter_template_files(tpl_dirs):
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError:
            continue

        relpath = os.path.relpath(filepath)
        # Docs / marketing pages routinely show literal HTML examples
        # inside {% verbatim %} regions — blank those out so they don't
        # false-positive (mirrors the A070 / #1004 fix).
        scan_source = _strip_verbatim_blocks(content)

        # Y001 — interactive element missing an accessible name.
        if not y001_suppressed:
            for match in _INTERACTIVE_EL_RE.finditer(scan_source):
                open_attrs = match.group("open")
                tag = match.group("tag").lower()
                # <a> is only an interactive control when it has an href.
                if tag == "a" and not _HREF_ATTR_RE.search(open_attrs):
                    continue
                # Explicit accessible-name attribute → fine.
                if _ACCESSIBLE_NAME_ATTR_RE.search(open_attrs):
                    continue
                if not _content_is_icon_only(match.group("inner")):
                    continue
                lineno = scan_source[: match.start()].count("\n") + 1
                errors.append(
                    DjustWarning(
                        "%s:%d -- <%s> has no accessible name (icon-only content "
                        "and no aria-label)." % (relpath, lineno, tag),
                        hint=(
                            "Screen-reader users hear nothing for an icon-only "
                            'control. Add aria-label="..." (or aria-labelledby / '
                            "title) to the <%s> element so its purpose is "
                            "announced." % tag
                        ),
                        id="djust.Y001",
                        fix_hint=(
                            'Add an aria-label="..." attribute to the <%s> '
                            "element at line %d in `%s`." % (tag, lineno, relpath)
                        ),
                        file_path=filepath,
                        line_number=lineno,
                    )
                )

        # Y002 — <img> missing an alt attribute.
        if not y002_suppressed:
            for match in _IMG_TAG_RE.finditer(scan_source):
                tag_text = match.group(0)
                if _IMG_HAS_ALT_RE.search(tag_text):
                    continue
                # Dynamic attribute injection ({% ... %} / {{ ... }})
                # may carry the alt — don't flag.
                if _IMG_DYNAMIC_ATTRS_RE.search(tag_text):
                    continue
                lineno = scan_source[: match.start()].count("\n") + 1
                errors.append(
                    DjustWarning(
                        "%s:%d -- <img> tag is missing an 'alt' attribute "
                        "(WCAG 1.1.1)." % (relpath, lineno),
                        hint=(
                            "Every <img> needs an alt attribute. Use "
                            'alt="describe the image" for informative images, '
                            'or alt="" for purely decorative ones.'
                        ),
                        id="djust.Y002",
                        fix_hint=(
                            'Add an alt="..." attribute to the <img> tag at '
                            'line %d in `%s` (use alt="" if decorative).' % (lineno, relpath)
                        ),
                        file_path=filepath,
                        line_number=lineno,
                    )
                )

        # Y003 — form control with no associated label.
        if not y003_suppressed:
            # File-scoped set of every <label for="X"> value — an <input
            # id="X"> whose id is in this set is considered named.
            label_for_ids = set(_LABEL_FOR_RE.findall(scan_source))
            # Spans of every <label>...</label> block — a control whose
            # opening tag starts inside one is wrapped (named by it).
            label_spans = [(m.start(), m.end()) for m in _LABEL_BLOCK_RE.finditer(scan_source)]
            for match in _FORM_CONTROL_RE.finditer(scan_source):
                open_attrs = match.group("open")
                tag = match.group("tag").lower()
                # <input> types that aren't user-named text controls.
                if tag == "input":
                    type_match = _INPUT_TYPE_RE.search(open_attrs)
                    input_type = type_match.group(1).lower() if type_match else "text"
                    if input_type in _Y003_SKIPPED_INPUT_TYPES:
                        continue
                # Dynamic attribute injection ({% ... %} / {{ ... }})
                # may carry id / aria-* — conservatively don't flag.
                if _CONTROL_DYNAMIC_ATTRS_RE.search(open_attrs):
                    continue
                # Explicit accessible-name attribute → named.
                if _ACCESSIBLE_NAME_ATTR_RE.search(open_attrs):
                    continue
                # id paired with a same-file <label for="..."> → named.
                id_match = _CONTROL_ID_RE.search(open_attrs)
                if id_match and id_match.group(1) in label_for_ids:
                    continue
                # Wrapped by a <label>...</label> element → named.
                if any(start <= match.start() < end for start, end in label_spans):
                    continue
                lineno = scan_source[: match.start()].count("\n") + 1
                errors.append(
                    DjustWarning(
                        "%s:%d -- <%s> form control has no associated label "
                        "(WCAG 1.3.1)." % (relpath, lineno, tag),
                        hint=(
                            "Assistive tech announces nothing meaningful for a "
                            "form control with no accessible name. Associate a "
                            'label via <label for="...">, wrap the control in a '
                            "<label>, or add aria-label / aria-labelledby. "
                            "Note: <label for> matching is file-scoped — a "
                            "label in a different template won't be detected."
                        ),
                        id="djust.Y003",
                        fix_hint=(
                            'Add a <label for="..."> (or aria-label) for the '
                            "<%s> control at line %d in `%s`." % (tag, lineno, relpath)
                        ),
                        file_path=filepath,
                        line_number=lineno,
                    )
                )

        # Y004 — positive tabindex (focus-order anti-pattern).
        if not y004_suppressed:
            for match in _POSITIVE_TABINDEX_RE.finditer(scan_source):
                value = match.group(1)
                lineno = scan_source[: match.start()].count("\n") + 1
                errors.append(
                    DjustWarning(
                        '%s:%d -- positive tabindex="%s" overrides natural '
                        "focus order (WCAG 2.4.3)." % (relpath, lineno, value),
                        hint=(
                            "A positive tabindex forces this element to the "
                            "front of the tab order, ahead of earlier DOM "
                            "elements — a confusing, hard-to-maintain focus "
                            'order. Use tabindex="0" to add an element to the '
                            'natural order, or tabindex="-1" to make it '
                            "focusable only programmatically."
                        ),
                        id="djust.Y004",
                        fix_hint=(
                            'Change tabindex="%s" to tabindex="0" (or remove '
                            "it) at line %d in `%s`." % (value, lineno, relpath)
                        ),
                        file_path=filepath,
                        line_number=lineno,
                    )
                )

    return errors


@register("djust")
def check_templates(app_configs, **kwargs):
    """Regex-scan template files for common issues."""
    errors = []
    tpl_dirs = _get_template_dirs()
    if not tpl_dirs:
        return errors

    # A090 — project-wide counter for {% djust_markdown %} usage (v0.7.0).
    # We emit a single info-level check after the per-file loop when at
    # least one template uses the tag, so developers get explicit
    # confirmation the Rust-side safe renderer is active.
    djust_markdown_hits: list[tuple[str, int]] = []

    for filepath in _iter_template_files(tpl_dirs):
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError:
            continue

        relpath = os.path.relpath(filepath)

        # T001 -- deprecated @click/@input syntax
        for match in _DEPRECATED_ATTR_RE.finditer(content):
            lineno = content[: match.start()].count("\n") + 1
            old_attr = match.group(0).rstrip("=")
            new_attr = old_attr.replace("@", "dj-")
            errors.append(
                DjustWarning(
                    "%s:%d -- deprecated '%s' syntax." % (relpath, lineno, old_attr),
                    hint="Use '%s' instead of '%s'." % (new_attr, old_attr),
                    id="djust.T001",
                    fix_hint=(
                        "Replace `%s=` with `%s=` at line %d in `%s`."
                        % (old_attr, new_attr, lineno, relpath)
                    ),
                    file_path=filepath,
                    line_number=lineno,
                )
            )

        # T002 -- LiveView template missing dj-root (informational)
        # Since PR #297, dj-root is auto-inferred from dj-view on both
        # client (autoStampRootAttributes) and server (template.py fallback).
        # This is now an INFO-level hint rather than a warning.
        has_dj_attrs = re.search(r"dj-(click|input|change|submit|model)", content)
        has_djust_view = _DJ_VIEW_RE.search(content)
        has_djust_root = _DJ_ROOT_RE.search(content)
        if (has_dj_attrs or has_djust_view) and not has_djust_root:
            # Check if it extends a base template (in which case root is likely in the base)
            if not re.search(r"\{%\s*extends\s+", content) and not _is_check_suppressed(
                "djust.T002"
            ):
                errors.append(
                    DjustInfo(
                        "%s -- LiveView template does not have explicit 'dj-root' attribute. "
                        "This is OK — dj-root is auto-inferred from dj-view." % relpath,
                        hint=(
                            "You can optionally add dj-root for clarity: "
                            '<div dj-root dj-view="myapp.views.MyView">. '
                            "Suppress this check with DJUST_CONFIG = {'suppress_checks': ['T002']}."
                        ),
                        id="djust.T002",
                        file_path=filepath,
                    )
                )

        # T003 -- wrapper_template uses {% include %} instead of {{ liveview_content|safe }}
        # Only check files that look like wrapper templates
        if _INCLUDE_RE.search(content) and not _LIVEVIEW_CONTENT_RE.search(content):
            # Only flag if file appears to be a wrapper (has a block named "content" or similar)
            if re.search(r"\{%\s*block\s+(content|body|main)\s*%\}", content):
                # Check if any {% include %} path mentions liveview/live_view
                include_paths = re.findall(r'\{%\s*include\s+["\']([^"\']+)["\']', content)
                has_liveview_include = any(
                    re.search(r"liveview|live_view", path, re.IGNORECASE) for path in include_paths
                )
                has_noqa = "{# noqa: T003 #}" in content or "{# noqa #}" in content
                if has_liveview_include and not has_noqa:
                    errors.append(
                        DjustInfo(
                            "%s -- wrapper template may be using {%% include %%} instead of {{ liveview_content|safe }}."
                            % relpath,
                            hint="In wrapper templates, use {{ liveview_content|safe }} to render the LiveView.",
                            id="djust.T003",
                            fix_hint=(
                                "Replace `{%% include ... %%}` with "
                                "`{{ liveview_content|safe }}` in `%s`." % relpath
                            ),
                            file_path=filepath,
                        )
                    )

        # T004 -- document.addEventListener('djust:...') should be window
        for match in _DOC_DJUST_EVENT_RE.finditer(content):
            lineno = content[: match.start()].count("\n") + 1
            errors.append(
                DjustWarning(
                    "%s:%d -- document.addEventListener for djust: event." % (relpath, lineno),
                    hint=(
                        "djust custom events (djust:push_event, djust:navigate, etc.) "
                        "are dispatched on window, not document. "
                        "Change to: window.addEventListener('djust:...')"
                    ),
                    id="djust.T004",
                    fix_hint=(
                        "Replace `document.addEventListener` with "
                        "`window.addEventListener` at line %d in `%s`." % (lineno, relpath)
                    ),
                    file_path=filepath,
                    line_number=lineno,
                )
            )

        # T005 -- dj-view and dj-root on different elements
        if has_djust_view and has_djust_root:
            _check_view_root_same_element(content, relpath, filepath, errors)

        # T010 -- dj-click used for navigation instead of dj-patch
        _check_click_for_navigation(content, relpath, filepath, errors)

        # T011 -- unsupported Django template tags (not implemented in Rust renderer)
        _check_unsupported_tags(content, relpath, filepath, errors)

        # T012 -- template uses dj-* event directives but missing dj-view
        if (
            _DJ_EVENT_DIRECTIVES_RE.search(content)
            and not _DJ_VIEW_RE.search(content)
            # Component templates (dj-component) don't need dj-view
            and not _DJ_COMPONENT_RE.search(content)
            # #1096: partial-template opt-out marker
            and not _DJ_PARTIAL_MARKER_RE.search(content)
            # Global suppression via DJUST_CONFIG['suppress_checks']
            and not _is_check_suppressed("djust.T012")
        ):
            errors.append(
                DjustWarning(
                    "%s -- template uses dj-* event directives but has no dj-view attribute."
                    % relpath,
                    hint=(
                        'Add dj-view="yourapp.views.YourView" to the root element, '
                        "or this template won't be connected to a LiveView. "
                        "If this template is an intentional fragment included from "
                        "a parent LiveView root, add a `{# djust:partial #}` "
                        "comment to silence this check, or suppress globally "
                        "with DJUST_CONFIG = {'suppress_checks': ['T012']}."
                    ),
                    id="djust.T012",
                    file_path=filepath,
                )
            )

        # T013 -- dj-view with empty or invalid value
        for match in re.finditer(r'dj-view="([^"]*)"', content):
            value = match.group(1)
            # {{ ... }} is a valid dynamic injection pattern (base-template use case)
            if re.match(r"^\s*\{\{.*\}\}\s*$", value):
                continue
            if not value or "." not in value:
                lineno = content[: match.start()].count("\n") + 1
                errors.append(
                    DjustWarning(
                        "%s:%d -- dj-view has empty or invalid value '%s'."
                        % (relpath, lineno, value),
                        hint="dj-view should be a dotted Python path like 'myapp.views.MyView'.",
                        id="djust.T013",
                        file_path=filepath,
                        line_number=lineno,
                    )
                )

        # T014 -- deprecated data-dj-id attribute (renamed to dj-id in v1.0)
        _check_deprecated_data_dj_id(content, relpath, filepath, errors)

        # A070 / A071 -- {% dj_activity %} name validation (v0.7.0).
        # A070 (Warning): tag with no name arg — renders a no-op wrapper
        # that never ties back to the server-side activity registry.
        # A071 (Error): two tags in one template share the same name — the
        # later registration silently overwrites the earlier one at render
        # time and all events route to the last-declared state.
        #
        # #1004 — strip {% verbatim %}...{% endverbatim %} regions before
        # the regex scan so literal `{% dj_activity %}` examples on docs /
        # marketing pages (which Django renders as-is, without parsing the
        # tag) don't false-positive. `_strip_verbatim_blocks` preserves
        # line numbers by replacing the body with whitespace.
        _activity_scan_source = _strip_verbatim_blocks(content)
        _seen_activity_names = {}  # type: ignore[var-annotated]
        for match in _DJ_ACTIVITY_TAG_RE.finditer(_activity_scan_source):
            args = match.group(1)
            lineno = content[: match.start()].count("\n") + 1
            name_match = _DJ_ACTIVITY_NAME_RE.match(args)
            # A name is "present" iff ANY of the three groups (double-quoted,
            # single-quoted, bare identifier / dotted path) matched.
            name_literal = None  # str when a string-literal name was given
            if name_match is not None:
                name_literal = name_match.group(1) or name_match.group(2)
                identifier_name = name_match.group(3)
            else:
                identifier_name = None
            if name_match is None or (not name_literal and not identifier_name):
                errors.append(
                    DjustWarning(
                        "%s:%d -- {%% dj_activity %%} is missing a 'name' argument."
                        % (relpath, lineno),
                        hint=(
                            "Every {% dj_activity %} block must have a non-empty name: "
                            '{% dj_activity "my-panel" visible=expr %}. Without a name, '
                            "the server-side ActivityMixin cannot route events or track "
                            "visibility for this region."
                        ),
                        id="djust.A070",
                        fix_hint=(
                            "Add a name argument to the {%% dj_activity %%} tag at line %d in `%s`, "
                            'e.g. `{%% dj_activity "panel-name" %%}`.' % (lineno, relpath)
                        ),
                        file_path=filepath,
                        line_number=lineno,
                    )
                )
                continue
            # Only string-literal names can be statically compared for
            # duplicate detection. Variable-name tags (bare identifiers)
            # resolve at render time — we can't know if two such tags
            # will produce the same name, so we skip A071 for them to
            # avoid false positives.
            if not name_literal:
                continue
            if name_literal in _seen_activity_names:
                first_line = _seen_activity_names[name_literal]
                errors.append(
                    DjustError(
                        "%s:%d -- duplicate {%% dj_activity %%} name %r (first declared at line %d)."
                        % (relpath, lineno, name_literal, first_line),
                        hint=(
                            "Activity names must be unique within one template. "
                            "Rename one of the blocks, or split the template if the "
                            "regions should be tracked independently."
                        ),
                        id="djust.A071",
                        fix_hint=(
                            "Rename one of the two `{%% dj_activity %r %%}` blocks in `%s`."
                            % (name_literal, relpath)
                        ),
                        file_path=filepath,
                        line_number=lineno,
                    )
                )
            else:
                _seen_activity_names[name_literal] = lineno

        # A075 — `{% live_render ... sticky=True lazy=True %}` collision scan
        # (v0.9.1, #1146). The two kwargs are mutually exclusive: sticky
        # preservation requires the slot to exist at mount-frame time so
        # the WS reattach can ``replaceWith`` the stashed subtree, while
        # lazy by definition defers slot rendering until after mount.
        # ``live_tags.live_render`` already raises TemplateSyntaxError at
        # tag-eval time; A075 promotes that runtime check to startup so
        # ``manage.py check`` flags the misuse before any request hits.
        #
        # Re-uses ``_strip_verbatim_blocks`` so docs/marketing pages that
        # show the anti-pattern as a literal example don't false-positive
        # (mirrors the A070/A071 / #1004 fix).
        if not _is_check_suppressed("djust.A075"):
            _live_render_scan_source = _strip_verbatim_blocks(content)
            for match in _LIVE_RENDER_TAG_RE.finditer(_live_render_scan_source):
                args = match.group(1)
                # Reject FALSY assignments first so e.g. ``sticky=False
                # lazy=True`` is silently accepted.
                sticky_falsy = bool(_LIVE_RENDER_STICKY_FALSY_RE.search(args))
                lazy_falsy = bool(_LIVE_RENDER_LAZY_FALSY_RE.search(args))
                sticky_truthy = (
                    bool(_LIVE_RENDER_STICKY_TRUTHY_RE.search(args)) and not sticky_falsy
                )
                lazy_truthy = bool(_LIVE_RENDER_LAZY_TRUTHY_RE.search(args)) and not lazy_falsy
                if sticky_truthy and lazy_truthy:
                    lineno = content[: match.start()].count("\n") + 1
                    errors.append(
                        DjustWarning(
                            "%s:%d -- {%% live_render %%} has both sticky=True and "
                            "lazy=True — these kwargs are mutually exclusive." % (relpath, lineno),
                            hint=(
                                "Sticky preservation requires the slot to exist at "
                                "mount-frame time so the WebSocket reattach can "
                                "replaceWith the stashed subtree. Lazy defers slot "
                                "rendering until after mount, so the stash-target "
                                "doesn't exist when reattach runs. Pick one. "
                                "Suppress with DJUST_CONFIG = "
                                "{'suppress_checks': ['A075']} if you have a "
                                "deliberate reason."
                            ),
                            id="djust.A075",
                            fix_hint=(
                                "Remove either `sticky=True` or `lazy=True` from "
                                "the {%% live_render %%} tag at line %d in `%s`."
                                % (lineno, relpath)
                            ),
                            file_path=filepath,
                            line_number=lineno,
                        )
                    )

        # A090 — tally {% djust_markdown %} occurrences (v0.7.0). The
        # actual Info-level check is emitted once per project after the
        # per-file loop (below).
        for match in _DJ_MARKDOWN_TAG_RE.finditer(content):
            lineno = content[: match.start()].count("\n") + 1
            djust_markdown_hits.append((relpath, lineno))

    if djust_markdown_hits and not _is_check_suppressed("djust.A090"):
        first_relpath, first_lineno = djust_markdown_hits[0]
        count = len(djust_markdown_hits)
        errors.append(
            DjustInfo(
                "{%% djust_markdown %%} is used in %d location(s) (first: %s:%d) — "
                "djust is rendering Markdown server-side via the Rust pulldown-cmark "
                "backend with safe-by-default escaping "
                "(ENABLE_HTML never set, javascript: URLs neutralised, 10 MiB input cap)."
                % (count, first_relpath, first_lineno),
                hint=(
                    "This is informational. Suppress with "
                    "DJUST_CONFIG = {'suppress_checks': ['A090']} if you don't "
                    "want this notice. See the Streaming Markdown guide for "
                    "details: docs/website/guides/streaming-markdown.md."
                ),
                id="djust.A090",
            )
        )

    return errors


def _check_view_root_same_element(content, relpath, filepath, errors):
    """T005: Detect when dj-view and dj-root are on different elements."""
    # Use regex to find HTML tags and check if both attributes co-occur
    # Find all tags that have either attribute
    tag_re = re.compile(r"<[a-zA-Z][^>]*>", re.DOTALL)
    has_combined_tag = False
    has_view_only = False
    has_root_only = False
    view_only_lineno = None
    for match in tag_re.finditer(content):
        tag = match.group(0)
        tag_has_view = "dj-view" in tag
        tag_has_root = "dj-root" in tag
        if tag_has_view and tag_has_root:
            has_combined_tag = True
            break
        if tag_has_view and not tag_has_root:
            has_view_only = True
            if view_only_lineno is None:
                view_only_lineno = content[: match.start()].count("\n") + 1
        if tag_has_root and not tag_has_view:
            has_root_only = True

    if has_view_only and has_root_only and not has_combined_tag:
        errors.append(
            DjustWarning(
                "%s -- dj-view and dj-root are on different elements." % relpath,
                hint=(
                    "dj-view and dj-root must be on the same root element. "
                    'Example: <div dj-root dj-view="myapp.views.MyView">'
                ),
                id="djust.T005",
                fix_hint=("Move dj-view and dj-root onto the same element in `%s`." % relpath),
                file_path=filepath,
                line_number=view_only_lineno,
            )
        )


# Tags still unsupported by the Rust renderer (after implementing widthratio,
# firstof, templatetag, spaceless, cycle, now in v0.3.3).
# Only opening tags are matched — end tags always accompany their openers.
#
# NOTE: {% extends %} and {% block %} are FULLY SUPPORTED since template
# inheritance was implemented (PR #272). Do not add them here.
_UNSUPPORTED_TAGS_RE = re.compile(
    r"\{%\s*(ifchanged|regroup|resetcycle|lorem|debug|filter|autoescape)\b"
)


def _check_unsupported_tags(content, relpath, filepath, errors):
    """T011: Detect unsupported Django template tags in LiveView templates.

    The Rust renderer silently ignores these tags, rendering an HTML comment
    instead. This check warns developers at startup so they can use workarounds.
    """
    has_noqa = "{# noqa: T011 #}" in content or "{# noqa #}" in content
    if has_noqa:
        return

    for match in _UNSUPPORTED_TAGS_RE.finditer(content):
        tag_name = match.group(1)
        lineno = content[: match.start()].count("\n") + 1
        errors.append(
            DjustWarning(
                "%s:%d -- unsupported template tag '{%% %s %%}' will be silently "
                "ignored by Rust renderer." % (relpath, lineno, tag_name),
                hint=(
                    "Pre-compute the value in your view and pass it as a context "
                    "variable, or use a supported alternative."
                ),
                id="djust.T011",
                file_path=filepath,
                line_number=lineno,
            )
        )


def _check_click_for_navigation(content, relpath, filepath, errors):
    """T010: Detect dj-click with navigation-style data attributes.

    Elements with both dj-click and navigation-style data attributes (data-view,
    data-tab, data-page, data-section) should use dj-patch instead for proper URL
    updates and back-button support.
    """
    tag_re = re.compile(r"<[a-zA-Z][^>]*>", re.DOTALL)
    for match in tag_re.finditer(content):
        tag = match.group(0)
        has_dj_click = "dj-click" in tag
        has_nav_data = _NAV_DATA_ATTRS.search(tag)

        if has_dj_click and has_nav_data:
            lineno = content[: match.start()].count("\n") + 1
            # Extract which data attribute was found for better messaging
            nav_match = _NAV_DATA_ATTRS.search(tag)
            nav_attr = nav_match.group(0) if nav_match else "data-*"

            errors.append(
                DjustWarning(
                    "%s:%d -- Element uses dj-click for navigation (%s) — use dj-patch for URL updates and history support."
                    % (relpath, lineno, nav_attr),
                    hint=(
                        "Navigation actions should use dj-patch instead of dj-click. "
                        "dj-patch updates the URL and enables back-button support. "
                        'Example: <button dj-patch="/view?tab=settings">Settings</button>\n'
                        "See: https://docs.djust.dev/guides/navigation"
                    ),
                    id="djust.T010",
                    fix_hint=(
                        "Replace dj-click with dj-patch at line %d in `%s` and handle "
                        "navigation parameters in handle_params() method." % (lineno, relpath)
                    ),
                    file_path=filepath,
                    line_number=lineno,
                )
            )


def _check_deprecated_data_dj_id(content, relpath, filepath, errors):
    """T014: Detect deprecated data-dj-id attribute (renamed to dj-id in v1.0).

    data-dj-id was the internal VDOM tracking attribute in pre-1.0 versions.
    It has been renamed to dj-id to be consistent with all other dj- prefixed
    attributes (dj-view, dj-click, dj-model, etc.).
    """
    for match in _DEPRECATED_DATA_DJ_ID_RE.finditer(content):
        lineno = content[: match.start()].count("\n") + 1
        errors.append(
            DjustWarning(
                "%s:%d -- deprecated 'data-dj-id' attribute (renamed to 'dj-id' in v1.0)."
                % (relpath, lineno),
                hint=(
                    "data-dj-id has been renamed to dj-id for consistency with other dj- attributes. "
                    "If this is hand-authored HTML, replace data-dj-id with dj-id. "
                    "If it is generated by djust, upgrade to v1.0."
                ),
                id="djust.T014",
                fix_hint=(
                    "Replace 'data-dj-id=' with 'dj-id=' at line %d in `%s`." % (lineno, relpath)
                ),
                file_path=filepath,
                line_number=lineno,
            )
        )


# ---------------------------------------------------------------------------
# Code Quality checks (Q0xx)
# ---------------------------------------------------------------------------


@register("djust")
def check_code_quality(app_configs, **kwargs):
    """AST-based code quality checks on project Python files."""
    errors = []
    app_dirs = _get_project_app_dirs()
    if not app_dirs:
        return errors

    for filepath in _iter_python_files(app_dirs):
        tree, source_lines = _parse_python_file(filepath)
        if tree is None:
            continue

        relpath = os.path.relpath(filepath)

        for node in ast.walk(tree):
            # Q001 -- print() in production code
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id == "print":
                    if not _has_noqa(source_lines, node.lineno, "Q001"):
                        errors.append(
                            DjustInfo(
                                "%s:%d -- print() statement found." % (relpath, node.lineno),
                                hint="Use logging module instead of print() in production code.",
                                id="djust.Q001",
                                fix_hint=(
                                    "Replace `print(...)` with `logger.info(...)` "
                                    "at line %d in `%s`." % (node.lineno, relpath)
                                ),
                                file_path=filepath,
                                line_number=node.lineno,
                            )
                        )

            # Q002 -- f-string in logger calls
            if isinstance(node, ast.Call):
                func = node.func
                attr_name = None
                if isinstance(func, ast.Attribute):
                    attr_name = func.attr
                if attr_name in ("debug", "info", "warning", "error", "critical", "exception"):
                    # Check if receiver looks like a logger
                    receiver = func.value if isinstance(func, ast.Attribute) else None
                    is_logger = False
                    if isinstance(receiver, ast.Name) and receiver.id in (
                        "logger",
                        "log",
                        "logging",
                    ):
                        is_logger = True
                    elif isinstance(receiver, ast.Attribute) and receiver.attr in ("logger", "log"):
                        is_logger = True
                    if is_logger and node.args:
                        if isinstance(node.args[0], ast.JoinedStr) and not _has_noqa(
                            source_lines, node.lineno, "Q002"
                        ):
                            errors.append(
                                DjustWarning(
                                    "%s:%d -- f-string in logger call." % (relpath, node.lineno),
                                    hint="Use %%s-style formatting: logger.%s('message %%s', value)"
                                    % attr_name,
                                    id="djust.Q002",
                                    fix_hint=(
                                        "Replace f-string with %%s-style formatting in "
                                        "logger.%s() call at line %d in `%s`."
                                        % (attr_name, node.lineno, relpath)
                                    ),
                                    file_path=filepath,
                                    line_number=node.lineno,
                                )
                            )

    # Q003 -- console.log without djustDebug guard in JS
    for filepath in _iter_js_files(app_dirs):
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except OSError:
            continue

        relpath = os.path.relpath(filepath)
        for i, line in enumerate(lines, 1):
            if "console.log" in line and "djustDebug" not in line:
                # Check previous line for guard
                prev_line = lines[i - 2].strip() if i >= 2 else ""
                if "djustDebug" not in prev_line:
                    errors.append(
                        DjustInfo(
                            "%s:%d -- console.log without djustDebug guard." % (relpath, i),
                            hint="Wrap in: if (globalThis.djustDebug) { console.log(...); }",
                            id="djust.Q003",
                            fix_hint=(
                                "Wrap `console.log(...)` with "
                                "`if (globalThis.djustDebug) { ... }` "
                                "at line %d in `%s`." % (i, relpath)
                            ),
                            file_path=filepath,
                            line_number=i,
                        )
                    )

    # Q010 -- event handlers that set navigation state without patching (heuristic)
    _check_navigation_state_in_handlers(errors)

    return errors


@register("djust")
def check_hot_view_replacement(app_configs, **kwargs):
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

    warnings = []
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
def check_time_travel_debugging(app_configs, **kwargs):
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

    results = []
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
def check_admin_widgets(app_configs, _admin_sites=None, **kwargs):
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
    results = []

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
def check_psycopg3_for_pg_notify(app_configs, **kwargs):
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
