"""
Management command for djust environment health checks.

Performs runtime diagnostics to catch common configuration issues:
Rust extension, Python/Django versions, Channels, Redis, templates,
static files, routing, and ASGI server availability.

Usage:
    python manage.py djust_doctor             # Pretty output
    python manage.py djust_doctor --json      # JSON output for CI
    python manage.py djust_doctor --quiet     # Exit code only (0=pass, 1=warn, 2=fail)
    python manage.py djust_doctor --check rust  # Run a single check
    python manage.py djust_doctor --verbose   # Include timing and extra detail
"""

import json as json_module
import logging
import sys
import time

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

# Probe timeout in seconds for network checks (e.g., Redis)
_PROBE_TIMEOUT = 2


class _CheckResult:
    """Result of a single diagnostic check."""

    OK = "OK"
    INFO = "INFO"
    WARN = "WARN"
    FAIL = "FAIL"

    __slots__ = ("name", "category", "status", "message", "detail", "elapsed_ms")

    def __init__(self, name, category, status, message, detail="", elapsed_ms=0.0):
        self.name = name
        self.category = category
        self.status = status
        self.message = message
        self.detail = detail
        self.elapsed_ms = elapsed_ms

    def to_dict(self):
        d = {
            "name": self.name,
            "category": self.category,
            "status": self.status,
            "message": self.message,
        }
        if self.detail:
            d["detail"] = self.detail
        if self.elapsed_ms:
            d["elapsed_ms"] = round(self.elapsed_ms, 2)
        return d


def _timed(fn):
    """Measure wall-clock time of a check function (returns elapsed_ms)."""
    start = time.monotonic()
    result = fn()
    if result is not None:
        elapsed = (time.monotonic() - start) * 1000
        result.elapsed_ms = elapsed
    return result


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_djust_version():
    """Display djust version."""
    try:
        import djust

        ver = getattr(djust, "__version__", "unknown")
        return _CheckResult("djust_version", "versions", _CheckResult.OK, "djust %s" % ver)
    except ImportError:
        return _CheckResult(
            "djust_version",
            "versions",
            _CheckResult.FAIL,
            "djust package not installed",
        )


def check_python_version():
    """Check Python version."""
    vi = sys.version_info
    ver = "%d.%d.%d" % (vi[0], vi[1], vi[2])
    if vi < (3, 10):
        status = _CheckResult.FAIL
        msg = "Python %s (>= 3.10 required)" % ver
    else:
        status = _CheckResult.OK
        msg = "Python %s" % ver
    return _CheckResult("python_version", "versions", status, msg)


def check_django_version():
    """Check Django version."""
    import django

    ver = "%d.%d.%d" % django.VERSION[:3]
    if django.VERSION < (4, 0):
        return _CheckResult(
            "django_version", "versions", _CheckResult.WARN, "Django %s (>= 4.0 recommended)" % ver
        )
    return _CheckResult("django_version", "versions", _CheckResult.OK, "Django %s" % ver)


def check_rust_extension():
    """Check if the Rust extension is importable."""
    try:
        from djust._rust import render_template  # noqa: F401

        # Try to get Rust core version if available
        try:
            from djust._rust import version as rust_version

            ver = rust_version()
        except (ImportError, AttributeError):
            ver = "loaded"
        return _CheckResult(
            "rust_extension",
            "versions",
            _CheckResult.OK,
            "Rust extension loaded (%s)" % ver,
        )
    except ImportError as exc:
        return _CheckResult(
            "rust_extension",
            "versions",
            _CheckResult.FAIL,
            "Rust extension not loaded",
            detail="ImportError: %s\nRun 'make build' or 'maturin develop' to compile." % exc,
        )


def check_channels_installed():
    """Check if Django Channels is installed."""
    try:
        import channels  # noqa: F401

        ver = getattr(channels, "__version__", "unknown")
        return _CheckResult(
            "channels_installed",
            "infrastructure",
            _CheckResult.OK,
            "Django Channels %s" % ver,
        )
    except ImportError:
        return _CheckResult(
            "channels_installed",
            "infrastructure",
            _CheckResult.FAIL,
            "Django Channels not installed",
            detail="pip install channels",
        )


def check_asgi_configured():
    """Check ASGI_APPLICATION setting."""
    from django.conf import settings

    val = getattr(settings, "ASGI_APPLICATION", None)
    if not val:
        return _CheckResult(
            "asgi_configured",
            "infrastructure",
            _CheckResult.FAIL,
            "ASGI_APPLICATION not set in settings",
            detail="Add ASGI_APPLICATION = 'myproject.asgi.application' to settings.py",
        )
    return _CheckResult(
        "asgi_configured",
        "infrastructure",
        _CheckResult.OK,
        "ASGI_APPLICATION configured",
    )


def check_channel_layers():
    """Check CHANNEL_LAYERS setting."""
    from django.conf import settings

    layers = getattr(settings, "CHANNEL_LAYERS", None)
    if not layers:
        return _CheckResult(
            "channel_layers",
            "infrastructure",
            _CheckResult.FAIL,
            "CHANNEL_LAYERS not configured",
            detail="Add CHANNEL_LAYERS to settings.py (InMemoryChannelLayer for dev, Redis for production).",
        )
    default = layers.get("default", {})
    backend = default.get("BACKEND", "")
    if "InMemory" in backend:
        return _CheckResult(
            "channel_layers",
            "infrastructure",
            _CheckResult.INFO,
            "CHANNEL_LAYERS configured (InMemoryChannelLayer)",
            detail="Using InMemoryChannelLayer -- switch to Redis for production.",
        )
    return _CheckResult(
        "channel_layers",
        "infrastructure",
        _CheckResult.OK,
        "CHANNEL_LAYERS configured (%s)" % backend.rsplit(".", 1)[-1],
    )


def check_redis():
    """If CHANNEL_LAYERS uses Redis, attempt a ping."""
    from django.conf import settings

    layers = getattr(settings, "CHANNEL_LAYERS", None)
    if not layers:
        return None  # skip: channel_layers check already fails
    default = layers.get("default", {})
    backend = default.get("BACKEND", "")
    if "Redis" not in backend:
        return None  # Not using Redis; skip

    hosts = default.get("CONFIG", {}).get("hosts", [("localhost", 6379)])
    host_label = str(hosts[0]) if hosts else "unknown"

    try:
        import redis as redis_lib

        if isinstance(hosts[0], (list, tuple)):
            r = redis_lib.Redis(host=hosts[0][0], port=hosts[0][1], socket_timeout=_PROBE_TIMEOUT)
        elif isinstance(hosts[0], str):
            r = redis_lib.from_url(hosts[0], socket_timeout=_PROBE_TIMEOUT)
        else:
            r = redis_lib.Redis(socket_timeout=_PROBE_TIMEOUT)
        r.ping()
        return _CheckResult(
            "redis",
            "infrastructure",
            _CheckResult.OK,
            "Redis connected (%s)" % host_label,
        )
    except ImportError:
        return _CheckResult(
            "redis",
            "infrastructure",
            _CheckResult.WARN,
            "redis-py not installed; cannot verify Redis connectivity",
            detail="pip install redis",
        )
    except Exception as exc:
        return _CheckResult(
            "redis",
            "infrastructure",
            _CheckResult.FAIL,
            "Redis connection failed (%s)" % host_label,
            detail=str(exc),
        )


def check_template_dirs():
    """Check that configured template directories exist."""
    from django.conf import settings
    import os

    templates = getattr(settings, "TEMPLATES", [])
    if not templates:
        return _CheckResult(
            "template_dirs",
            "templates",
            _CheckResult.WARN,
            "No TEMPLATES configured",
        )

    issues = []
    ok_dirs = []
    for engine in templates:
        for d in engine.get("DIRS", []):
            if os.path.isdir(d):
                count = sum(
                    1
                    for _root, _dirs, files in os.walk(d)
                    for f in files
                    if f.endswith((".html", ".txt"))
                )
                ok_dirs.append("%s (%d templates)" % (d, count))
            else:
                issues.append(d)

    if issues:
        return _CheckResult(
            "template_dirs",
            "templates",
            _CheckResult.WARN,
            "Template directory missing: %s" % ", ".join(issues),
        )
    if ok_dirs:
        return _CheckResult(
            "template_dirs",
            "templates",
            _CheckResult.OK,
            "Template directories OK (%d dirs)" % len(ok_dirs),
            detail="; ".join(ok_dirs),
        )
    return _CheckResult(
        "template_dirs",
        "templates",
        _CheckResult.OK,
        "No explicit DIRS (using app_directories loader)",
    )


def check_rust_render():
    """Render a trivial template through the Rust engine."""
    try:
        from djust._rust import render_template

        start = time.monotonic()
        result = render_template("<p>{{ greeting }}</p>", {"greeting": "hello"})
        elapsed = (time.monotonic() - start) * 1000
        if "hello" in result:
            return _CheckResult(
                "rust_render",
                "templates",
                _CheckResult.OK,
                "Rust template render: success (%.1fms)" % elapsed,
            )
        return _CheckResult(
            "rust_render",
            "templates",
            _CheckResult.FAIL,
            "Rust template render returned unexpected output",
            detail="Got: %s" % result[:200],
        )
    except ImportError:
        return _CheckResult(
            "rust_render",
            "templates",
            _CheckResult.FAIL,
            "Rust extension not available for render test",
        )
    except Exception as exc:
        return _CheckResult(
            "rust_render",
            "templates",
            _CheckResult.FAIL,
            "Rust template render failed",
            detail=str(exc),
        )


def check_static_files():
    """Check that djust/client.js is findable via staticfiles."""
    try:
        from django.contrib.staticfiles.finders import find

        result = find("djust/client.js")
        if result:
            return _CheckResult(
                "static_files",
                "static",
                _CheckResult.OK,
                "djust/client.js found via staticfiles finders",
            )
        return _CheckResult(
            "static_files",
            "static",
            _CheckResult.FAIL,
            "djust/client.js not found via staticfiles finders",
            detail="Ensure 'djust' is in INSTALLED_APPS and STATICFILES_FINDERS includes AppDirectoriesFinder.",
        )
    except Exception as exc:
        return _CheckResult(
            "static_files",
            "static",
            _CheckResult.FAIL,
            "Static file check error",
            detail=str(exc),
        )


def check_asgi_server():
    """Check if daphne or uvicorn is installed."""
    servers = []
    try:
        import daphne  # noqa: F401

        servers.append("daphne")
    except ImportError:
        pass  # daphne not installed
    try:
        import uvicorn  # noqa: F401

        servers.append("uvicorn")
    except ImportError:
        pass  # uvicorn not installed

    if servers:
        return _CheckResult(
            "asgi_server",
            "routing",
            _CheckResult.OK,
            "%s installed (ASGI server)" % ", ".join(servers),
        )
    return _CheckResult(
        "asgi_server",
        "routing",
        _CheckResult.WARN,
        "No ASGI server found (install daphne or uvicorn)",
        detail="pip install daphne  # or: pip install uvicorn",
    )


# Registry of all checks (name -> callable). Order matters for output.
_ALL_CHECKS = [
    ("djust_version", check_djust_version),
    ("python_version", check_python_version),
    ("django_version", check_django_version),
    ("rust_extension", check_rust_extension),
    ("channels_installed", check_channels_installed),
    ("asgi_configured", check_asgi_configured),
    ("channel_layers", check_channel_layers),
    ("redis", check_redis),
    ("template_dirs", check_template_dirs),
    ("rust_render", check_rust_render),
    ("static_files", check_static_files),
    ("asgi_server", check_asgi_server),
]


class Command(BaseCommand):
    help = "Run djust environment health checks"

    def add_arguments(self, parser):
        parser.add_argument(
            "--json",
            action="store_true",
            help="Output results as JSON",
        )
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Exit code only (0=pass, 1=warn, 2=fail)",
        )
        parser.add_argument(
            "--check",
            type=str,
            default=None,
            help="Run a single check by name (e.g., --check redis)",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Include timing and extra detail",
        )

    def handle(self, *args, **options):
        use_json = options["json"]
        quiet = options["quiet"]
        single = options.get("check")
        verbose = options.get("verbose", False)

        results = []
        for name, fn in _ALL_CHECKS:
            if single and name != single:
                continue
            result = _timed(fn)
            if result is not None:
                results.append(result)

        if single and not results:
            # Unknown check name
            known = [n for n, _ in _ALL_CHECKS]
            self.stderr.write("Unknown check: %s\nAvailable: %s" % (single, ", ".join(known)))
            return

        # Determine overall status
        has_fail = any(r.status == _CheckResult.FAIL for r in results)
        has_warn = any(r.status == _CheckResult.WARN for r in results)

        if quiet:
            # Exit code only
            pass
        elif use_json:
            output = {
                "status": "fail" if has_fail else ("warn" if has_warn else "pass"),
                "checks": [r.to_dict() for r in results],
            }
            self.stdout.write(json_module.dumps(output, indent=2))
        else:
            self._pretty_output(results, verbose)

        # Set exit code
        if has_fail:
            raise SystemExit(2)
        if has_warn:
            raise SystemExit(1)

    def _pretty_output(self, results, verbose):
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("djust doctor"))
        self.stdout.write(self.style.MIGRATE_HEADING("============"))
        self.stdout.write("")

        # Group by category
        categories = []
        seen = set()
        for r in results:
            if r.category not in seen:
                categories.append(r.category)
                seen.add(r.category)

        pass_count = 0
        total_count = 0

        for cat in categories:
            cat_results = [r for r in results if r.category == cat]
            self.stdout.write("  [%s]" % cat.upper())

            for r in cat_results:
                total_count += 1
                label_style = self._status_style(r.status)
                timing = ""
                if verbose and r.elapsed_ms:
                    timing = " (%.1fms)" % r.elapsed_ms

                line = "  %s  %s%s" % (label_style, r.message, timing)
                self.stdout.write(line)

                if r.detail and (verbose or r.status in (_CheckResult.FAIL, _CheckResult.WARN)):
                    for detail_line in r.detail.split("\n"):
                        self.stdout.write("        %s" % detail_line)

                if r.status in (_CheckResult.OK, _CheckResult.INFO):
                    pass_count += 1

            self.stdout.write("")

        self.stdout.write("  " + "-" * 58)
        if pass_count == total_count:
            self.stdout.write(self.style.SUCCESS("  All %d checks passed." % total_count))
        else:
            failed = total_count - pass_count
            self.stdout.write(
                "  %d/%d checks passed, %d issue(s)." % (pass_count, total_count, failed)
            )
        self.stdout.write("")

    def _status_style(self, status):
        if status == _CheckResult.OK:
            return self.style.SUCCESS("OK  ")
        if status == _CheckResult.INFO:
            return self.style.HTTP_INFO("INFO")
        if status == _CheckResult.WARN:
            return self.style.WARNING("WARN")
        return self.style.ERROR("FAIL")
