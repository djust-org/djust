"""
Django management command to validate djust project configuration.

Usage:
    python manage.py djust_check
    python manage.py djust_check --verbose
    python manage.py djust_check --fix  (show fix suggestions)
"""

import importlib
import os
import re
import glob

from django.core.management.base import BaseCommand
from django.conf import settings
from django.contrib.staticfiles.finders import find as find_static


class Command(BaseCommand):
    help = "Validate djust project configuration and report issues"

    def add_arguments(self, parser):
        parser.add_argument(
            "--verbose", action="store_true", help="Show detailed information for passing checks"
        )
        parser.add_argument(
            "--fix", action="store_true", help="Show fix suggestions for each issue"
        )

    def handle(self, *args, **options):
        self.verbose = options["verbose"]
        self.show_fix = options["fix"]
        self.errors = 0
        self.warnings = 0
        self.passed = 0

        self.stdout.write("")
        self.stdout.write(self.style.HTTP_INFO("=" * 60))
        self.stdout.write(self.style.HTTP_INFO("  djust Configuration Check"))
        self.stdout.write(self.style.HTTP_INFO("=" * 60))
        self.stdout.write("")

        self._check_settings()
        self._check_static_files()
        self._check_websocket_routing()
        self._check_templates()

        # Summary
        self.stdout.write("")
        self.stdout.write(self.style.HTTP_INFO("-" * 60))
        total = self.errors + self.warnings + self.passed
        summary = f"  {total} checks: ✅ {self.passed} passed"
        if self.warnings:
            summary += f", ⚠️  {self.warnings} warnings"
        if self.errors:
            summary += f", ❌ {self.errors} errors"
        self.stdout.write(summary)
        self.stdout.write(self.style.HTTP_INFO("-" * 60))
        self.stdout.write("")

        if self.errors:
            self.stdout.write(self.style.ERROR(
                "  Some checks failed. Fix the errors above to ensure djust works correctly."
            ))
            if not self.show_fix:
                self.stdout.write("  Run with --fix for suggested solutions.\n")
        elif self.warnings:
            self.stdout.write(self.style.WARNING(
                "  Configuration looks okay but has warnings."
            ))
        else:
            self.stdout.write(self.style.SUCCESS("  ✅ All checks passed! djust is configured correctly."))
        self.stdout.write("")

    def _pass(self, msg):
        self.passed += 1
        if self.verbose:
            self.stdout.write(f"  ✅ {msg}")

    def _warn(self, msg, fix=None):
        self.warnings += 1
        self.stdout.write(self.style.WARNING(f"  ⚠️  {msg}"))
        if fix and self.show_fix:
            self.stdout.write(self.style.NOTICE(f"     Fix: {fix}"))

    def _error(self, msg, fix=None):
        self.errors += 1
        self.stdout.write(self.style.ERROR(f"  ❌ {msg}"))
        if fix and self.show_fix:
            self.stdout.write(self.style.NOTICE(f"     Fix: {fix}"))

    def _section(self, title):
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING(f"  [{title}]"))

    # ── Settings ────────────────────────────────────────────────

    def _check_settings(self):
        self._section("Settings")

        installed = getattr(settings, "INSTALLED_APPS", [])

        if "djust" in installed:
            self._pass("'djust' is in INSTALLED_APPS")
        else:
            self._error(
                "'djust' is not in INSTALLED_APPS",
                "Add 'djust' to INSTALLED_APPS in settings.py",
            )

        if "channels" in installed:
            self._pass("'channels' is in INSTALLED_APPS")
        else:
            self._error(
                "'channels' is not in INSTALLED_APPS",
                "pip install channels && add 'channels' to INSTALLED_APPS",
            )

        if "django.contrib.staticfiles" in installed:
            self._pass("'django.contrib.staticfiles' is in INSTALLED_APPS")
        else:
            self._error(
                "'django.contrib.staticfiles' is not in INSTALLED_APPS",
                "Add 'django.contrib.staticfiles' to INSTALLED_APPS",
            )

        if getattr(settings, "CHANNEL_LAYERS", None):
            self._pass("CHANNEL_LAYERS is configured")
        else:
            self._warn(
                "CHANNEL_LAYERS is not configured (single-process InMemory will be used)",
                "Add CHANNEL_LAYERS with a Redis backend for production",
            )

        if getattr(settings, "ASGI_APPLICATION", None):
            self._pass(f"ASGI_APPLICATION = '{settings.ASGI_APPLICATION}'")
        else:
            self._error(
                "ASGI_APPLICATION is not set",
                "Add ASGI_APPLICATION = 'myproject.asgi.application' to settings.py",
            )

    # ── Static Files ────────────────────────────────────────────

    def _check_static_files(self):
        self._section("Static Files")

        required_files = [
            "djust/client.js",
            "djust/debug-panel.js",
            "djust/debug-panel.css",
        ]

        all_found = True
        for static_path in required_files:
            result = find_static(static_path)
            if result:
                self._pass(f"Found: {static_path}")
            else:
                all_found = False
                self._error(
                    f"Static file not found: {static_path}",
                    "Ensure 'djust' is in INSTALLED_APPS and run 'python manage.py collectstatic'",
                )

        # Check for daphne usage
        installed = getattr(settings, "INSTALLED_APPS", [])
        if "daphne" in installed:
            # Check if ASGIStaticFilesHandler is being used
            asgi_app_path = getattr(settings, "ASGI_APPLICATION", "")
            self._warn(
                "Daphne detected — static files won't be served unless using ASGIStaticFilesHandler",
                "Use djust.asgi.get_application() which auto-wraps with ASGIStaticFilesHandler in DEBUG mode, "
                "or manually wrap: ASGIStaticFilesHandler(get_asgi_application())",
            )

    # ── WebSocket Routing ───────────────────────────────────────

    def _check_websocket_routing(self):
        self._section("WebSocket Routing")

        asgi_path = getattr(settings, "ASGI_APPLICATION", None)
        if not asgi_path:
            self._error("Cannot check WebSocket routing: ASGI_APPLICATION not set")
            return

        # Try to import the ASGI application
        try:
            module_path, attr_name = asgi_path.rsplit(".", 1)
            module = importlib.import_module(module_path)
            application = getattr(module, attr_name)
        except Exception as e:
            self._error(
                f"Cannot import ASGI application '{asgi_path}': {e}",
                "Check that ASGI_APPLICATION points to a valid module and attribute",
            )
            return

        # Check for ProtocolTypeRouter with websocket
        app = application
        # Unwrap ASGIStaticFilesHandler if present
        if hasattr(app, "application"):
            app = app.application

        has_websocket = False
        if hasattr(app, "application_mapping"):
            has_websocket = "websocket" in app.application_mapping
        elif isinstance(app, dict):
            has_websocket = "websocket" in app

        if has_websocket:
            self._pass("ASGI application has 'websocket' protocol handler")
        else:
            self._error(
                "ASGI application missing 'websocket' protocol handler",
                "Use djust.asgi.get_application() or add 'websocket' to ProtocolTypeRouter",
            )

        # Check if LiveViewConsumer is routed
        # This is a best-effort check - we look at the ASGI module source
        try:
            source = open(module.__file__).read()
            if "LiveViewConsumer" in source or "djust.asgi" in source or "get_application" in source:
                self._pass("LiveViewConsumer appears to be routed")
            else:
                self._warn(
                    "LiveViewConsumer not found in ASGI configuration",
                    "Add: path('ws/live/', LiveViewConsumer.as_asgi()) to your WebSocket routing",
                )
        except Exception:
            self._warn("Could not inspect ASGI module source for LiveViewConsumer routing")

    # ── Templates ───────────────────────────────────────────────

    def _check_templates(self):
        self._section("Templates")

        # Scan template directories for djust usage
        template_dirs = []
        for engine in getattr(settings, "TEMPLATES", []):
            template_dirs.extend(engine.get("DIRS", []))
            # Also check APP_DIRS
            if engine.get("APP_DIRS", False):
                for app_name in getattr(settings, "INSTALLED_APPS", []):
                    try:
                        app_module = importlib.import_module(app_name)
                        app_dir = os.path.dirname(app_module.__file__)
                        templates_dir = os.path.join(app_dir, "templates")
                        if os.path.isdir(templates_dir):
                            template_dirs.append(templates_dir)
                    except (ImportError, AttributeError, TypeError):
                        pass

        if not template_dirs:
            self._warn("No template directories found to check")
            return

        djust_templates = []
        for tdir in template_dirs:
            for ext in ("*.html", "**/*.html"):
                for filepath in glob.glob(os.path.join(tdir, ext), recursive=True):
                    try:
                        content = open(filepath).read()
                    except Exception:
                        continue
                    # Check for djust usage markers
                    has_djust = (
                        "dj-click" in content
                        or "dj-submit" in content
                        or "dj-change" in content
                        or "dj-input" in content
                        or "dj-keydown" in content
                        or "dj-value-" in content
                        or "live_tags" in content
                        or "{% djust" in content
                    )
                    if has_djust:
                        djust_templates.append((filepath, content))

        if not djust_templates:
            self._pass("No templates with djust directives found (nothing to check)")
            return

        self._pass(f"Found {len(djust_templates)} template(s) using djust directives")

        for filepath, content in djust_templates:
            rel_path = os.path.basename(filepath)
            if 'data-djust-root' not in content and 'data-djust-view' not in content:
                # Check if it might be a partial/include (no <html> tag)
                if "<html" in content.lower() or "<!doctype" in content.lower():
                    self._warn(
                        f"Template '{rel_path}' uses djust directives but missing "
                        "data-djust-root or data-djust-view attribute",
                        "Add data-djust-root to your root container and data-djust-view to the view container",
                    )
