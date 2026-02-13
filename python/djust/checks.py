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
                                    # Look for manual client.js loading
                                    if "djust/client.js" in line and "<script" in line:
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
    elif not has_daphne:
        errors.append(
            DjustInfo(
                "'daphne' is not in INSTALLED_APPS.",
                hint="Consider adding 'daphne' to INSTALLED_APPS for ASGI support.",
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
    asgi_path = getattr(settings, "ASGI_APPLICATION", None)
    if asgi_path:
        try:
            from importlib import import_module

            module_path, attr = asgi_path.rsplit(".", 1)
            asgi_app = getattr(import_module(module_path), attr)
            # ProtocolTypeRouter stores routes in .application_mapping
            app_map = getattr(asgi_app, "application_mapping", None)
            if app_map and "websocket" in app_map:
                ws_app = app_map["websocket"]
                # Walk the middleware chain looking for Auth/DjustMiddlewareStack
                has_middleware = False
                current = ws_app
                for _ in range(10):  # bounded walk
                    cls_name = type(current).__name__
                    mod_name = type(current).__module__ or ""
                    if "auth" in cls_name.lower() or "auth" in mod_name.lower():
                        has_middleware = True
                        break
                    if "session" in cls_name.lower() or "session" in mod_name.lower():
                        # DjustMiddlewareStack wraps SessionMiddlewareStack
                        has_middleware = True
                        break
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
        except Exception:
            pass  # Don't fail the check if ASGI app can't be introspected

    # C006 -- daphne without whitenoise for static file serving
    if has_daphne:
        middleware = list(getattr(settings, "MIDDLEWARE", []))
        has_whitenoise = any("whitenoise" in m.lower() for m in middleware)
        if not has_whitenoise:
            errors.append(
                DjustWarning(
                    "Daphne does not serve static files. "
                    "Without WhiteNoise, djust's client JS and CSS will return 404.",
                    hint=(
                        "Add 'whitenoise.middleware.WhiteNoiseMiddleware' to MIDDLEWARE "
                        "after SecurityMiddleware, add 'django.contrib.staticfiles' to "
                        "INSTALLED_APPS, set STATIC_ROOT, and run 'collectstatic'."
                    ),
                    id="djust.C006",
                    fix_hint=(
                        "Add `'whitenoise.middleware.WhiteNoiseMiddleware'` to MIDDLEWARE "
                        "after `'django.middleware.security.SecurityMiddleware'` in your "
                        "Django settings file."
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
            if login_req or perm_req:
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
                cls_file = inspect.getfile(cls) if hasattr(cls, "__module__") else ""
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
                    pass
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
                    pass
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
                    pass
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
            if name in ("mount", "get_context_data", "dispatch", "setup", "get", "post"):
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
                    pass
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
                    pass
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

    # V006 -- service instance in mount() (AST-based scan of project files)
    _check_service_instances_in_mount(errors)

    return errors


def _check_service_instances_in_mount(errors):
    """V006: Detect service/client/session instantiation in mount() methods via AST."""
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
                            and isinstance(node.body[0].value, (ast.Constant, ast.Str))
                        ):
                            doc = (
                                node.body[0].value.value
                                if isinstance(node.body[0].value, ast.Constant)
                                else node.body[0].value.s
                            )
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

        # T002 -- LiveView template missing dj-root
        # Flag if file has dj-* directives or dj-view but no dj-root
        has_dj_attrs = re.search(r"dj-(click|input|change|submit|model)", content)
        has_djust_view = _DJ_VIEW_RE.search(content)
        has_djust_root = _DJ_ROOT_RE.search(content)
        if (has_dj_attrs or has_djust_view) and not has_djust_root:
            # Check if it extends a base template (in which case root is likely in the base)
            if not re.search(r"\{%\s*extends\s+", content):
                errors.append(
                    DjustWarning(
                        "%s -- LiveView template missing 'dj-root' attribute. "
                        "Without dj-root, djust cannot identify the root element "
                        "for DOM patching." % relpath,
                        hint=(
                            "Add dj-root to the root element of your LiveView template. "
                            'Example: <div dj-root dj-view="myapp.views.MyView">'
                        ),
                        id="djust.T002",
                        fix_hint=(
                            "Add `dj-root` attribute to the root element " "in `%s`." % relpath
                        ),
                        file_path=filepath,
                    )
                )

        # T003 -- wrapper_template uses {% include %} instead of {{ liveview_content|safe }}
        # Only check files that look like wrapper templates
        if _INCLUDE_RE.search(content) and not _LIVEVIEW_CONTENT_RE.search(content):
            # Only flag if file appears to be a wrapper (has a block named "content" or similar)
            if re.search(r"\{%\s*block\s+(content|body|main)\s*%\}", content):
                # Check if it's actually wrapping liveview content
                if re.search(r"liveview|live_view|djust", content, re.IGNORECASE):
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
                fix_hint=("Move dj-view and dj-root onto the same element " "in `%s`." % relpath),
                file_path=filepath,
                line_number=view_only_lineno,
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

    return errors
