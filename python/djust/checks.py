"""
Django system checks for the djust framework.

Registers checks with Django's check framework that also run via
``python manage.py check``. Categories:

- Configuration (C0xx) -- settings validation
- LiveView (V0xx) -- LiveView subclass validation
- Security (S0xx) -- AST-based security checks
- Templates (T0xx) -- template file scanning
- Code Quality (Q0xx) -- AST-based quality checks
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


def _check_missing_compiled_css(errors):
    """Warn if Tailwind is configured but compiled CSS is missing."""
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
        # Check if output.css exists
        output_exists = False
        for static_dir in static_dirs:
            if os.path.exists(os.path.join(static_dir, "css", "output.css")):
                output_exists = True
                break

        if not output_exists:
            if settings.DEBUG:
                errors.append(
                    DjustInfo(
                        "Tailwind CSS configured but output.css not found (development mode).",
                        hint=(
                            "djust will use Tailwind CDN as fallback in development. "
                            "For better performance, compile CSS:\n"
                            "  python manage.py djust_setup_css tailwind --watch"
                        ),
                        id="djust.C011",
                    )
                )
            else:
                errors.append(
                    DjustWarning(
                        "Tailwind CSS configured but output.css not found.",
                        hint=(
                            "Run: tailwindcss -i static/css/input.css -o static/css/output.css --minify\n"
                            "Or: python manage.py djust_setup_css tailwind"
                        ),
                        id="djust.C011",
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
_DJ_ROOT_RE = re.compile(r"dj-root")
_INCLUDE_RE = re.compile(r"\{%\s*include\s+")
_LIVEVIEW_CONTENT_RE = re.compile(r"\{\{\s*liveview_content\s*\|\s*safe\s*\}\}")
_DOC_DJUST_EVENT_RE = re.compile(r"""document\s*\.\s*addEventListener\s*\(\s*['"]djust:""")
_NAV_DATA_ATTRS = re.compile(r"data-(view|tab|page|section)")  # Navigation-style data attributes
_DJ_EVENT_DIRECTIVES_RE = re.compile(
    r"dj-(click|input|change|submit|blur|focus|keydown|keyup|mouseenter|mouseleave|window-\w+|document-\w+|click-away|shortcut)="
)
_DJ_COMPONENT_RE = re.compile(r"dj-component")
_DEPRECATED_DATA_DJ_ID_RE = re.compile(r"""data-dj-id\s*=\s*["'][^"']*["']""")


@register("djust")
def check_templates(app_configs, **kwargs):
    """Regex-scan template files for common issues."""
    errors = []
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
        if _DJ_EVENT_DIRECTIVES_RE.search(content) and not _DJ_VIEW_RE.search(content):
            # Only fire if this isn't a component template (components don't need dj-view)
            if not _DJ_COMPONENT_RE.search(content):
                errors.append(
                    DjustWarning(
                        "%s -- template uses dj-* event directives but has no dj-view attribute."
                        % relpath,
                        hint=(
                            'Add dj-view="yourapp.views.YourView" to the root element, '
                            "or this template won't be connected to a LiveView."
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
