"""
Proposed CSS Framework System Checks for djust

Add these checks to python/djust/checks.py in the check_configuration() function.

These checks help developers avoid common CSS pitfalls:
- C010: Tailwind CDN usage in production (performance issue)
- C011: Missing compiled CSS file when Tailwind is configured
- C012: Manual client.js in base templates (double-loading)
"""

from django.core.checks import Warning, Info
from django.conf import settings
import os


def check_css_framework_config(errors):
    """Check CSS framework configuration and warn about common issues."""

    # C010 -- Tailwind CDN in production (scan base templates)
    if not settings.DEBUG:  # Production mode
        template_dirs = _get_template_dirs()
        for template_dir in template_dirs:
            for root, dirs, files in os.walk(template_dir):
                for filename in files:
                    if filename.endswith((".html", ".htm")):
                        filepath = os.path.join(root, filename)
                        # Check if it's a base/layout template (commonly named)
                        if "base" in filename.lower() or "layout" in filename.lower():
                            try:
                                with open(filepath, "r", encoding="utf-8") as f:
                                    content = f.read()
                                    if "cdn.tailwindcss.com" in content:
                                        errors.append(
                                            Warning(
                                                f"Tailwind CDN detected in production template: {filename}",
                                                hint=(
                                                    "Using Tailwind CDN in production is slow and shows console warnings. "
                                                    "Compile Tailwind CSS instead:\n"
                                                    "1. Create static/css/input.css with '@import \"tailwindcss\"'\n"
                                                    "2. Run: tailwindcss -i static/css/input.css -o static/css/output.css --minify\n"
                                                    '3. Use: <link rel="stylesheet" href="{% static \'css/output.css\' %}">'
                                                ),
                                                id="djust.C010",
                                                obj=filepath,
                                            )
                                        )
                            except Exception:
                                pass  # Skip files that can't be read

    # C011 -- Tailwind configured but compiled CSS missing
    css_framework = getattr(settings, "DJUST_CSS_FRAMEWORK", None)
    if css_framework == "tailwind":
        # Check if compiled CSS exists in common locations
        static_dirs = getattr(settings, "STATICFILES_DIRS", [])
        static_root = getattr(settings, "STATIC_ROOT", None)

        check_paths = []
        for static_dir in static_dirs:
            check_paths.append(os.path.join(static_dir, "css", "output.css"))
        if static_root:
            check_paths.append(os.path.join(static_root, "css", "output.css"))

        css_exists = any(os.path.exists(path) for path in check_paths)

        if not css_exists and not settings.DEBUG:
            errors.append(
                Warning(
                    "Tailwind CSS is configured but compiled CSS file not found.",
                    hint=(
                        "You've set DJUST_CSS_FRAMEWORK='tailwind' but static/css/output.css doesn't exist. "
                        "Run: tailwindcss -i static/css/input.css -o static/css/output.css --minify"
                    ),
                    id="djust.C011",
                )
            )
        elif not css_exists and settings.DEBUG:
            errors.append(
                Info(
                    "Tailwind CSS configured but compiled file not found (development mode).",
                    hint=(
                        "Run: tailwindcss -i static/css/input.css -o static/css/output.css\n"
                        'Or use Tailwind CDN for development only: <script src="https://cdn.tailwindcss.com"></script>'
                    ),
                    id="djust.C011",
                )
            )

    # C012 -- Manual client.js in base templates (causes double-loading)
    template_dirs = _get_template_dirs()
    for template_dir in template_dirs:
        for root, dirs, files in os.walk(template_dir):
            for filename in files:
                if filename.endswith((".html", ".htm")):
                    filepath = os.path.join(root, filename)
                    # Check base/layout templates (most likely to have the issue)
                    if "base" in filename.lower() or "layout" in filename.lower():
                        try:
                            with open(filepath, "r", encoding="utf-8") as f:
                                content = f.read()
                                # Look for manual client.js loading
                                if "djust/client.js" in content and "<script" in content:
                                    # Make sure it's not just a comment
                                    if not all(
                                        line.strip().startswith("<!--")
                                        or line.strip().startswith("*")
                                        for line in content.split("\n")
                                        if "djust/client.js" in line
                                    ):
                                        errors.append(
                                            Warning(
                                                f"Manual client.js detected in base template: {filename}",
                                                hint=(
                                                    "djust automatically injects client.js for LiveView pages. "
                                                    "Remove the manual <script src=\"{% static 'djust/client.js' %}\"> tag "
                                                    "to avoid double-loading and race conditions."
                                                ),
                                                id="djust.C012",
                                                obj=filepath,
                                            )
                                        )
                        except Exception:
                            pass  # Skip files that can't be read


# Development mode: Allow Tailwind CDN with a helpful info message
def check_tailwind_cdn_in_dev(errors):
    """In development, inform about Tailwind CDN but don't warn."""
    if settings.DEBUG:
        template_dirs = _get_template_dirs()
        cdn_found = False
        for template_dir in template_dirs:
            for root, dirs, files in os.walk(template_dir):
                for filename in files:
                    if filename.endswith(".html"):
                        filepath = os.path.join(root, filename)
                        try:
                            with open(filepath, "r", encoding="utf-8") as f:
                                if "cdn.tailwindcss.com" in f.read():
                                    cdn_found = True
                                    break
                        except Exception:
                            pass
                if cdn_found:
                    break
            if cdn_found:
                break

        if cdn_found:
            errors.append(
                Info(
                    "Tailwind CDN detected (development mode).",
                    hint=(
                        "Using Tailwind CDN is fine for development, but remember to compile CSS before production:\n"
                        "tailwindcss -i static/css/input.css -o static/css/output.css --minify"
                    ),
                    id="djust.C013",
                )
            )


def _get_template_dirs():
    """Helper to get all template directories."""
    dirs = []
    for backend in settings.TEMPLATES:
        for d in backend.get("DIRS", []):
            if os.path.isdir(d):
                dirs.append(d)
        # Also check APP_DIRS templates
        if backend.get("APP_DIRS"):
            from django.apps import apps

            for config in apps.get_app_configs():
                path = config.path
                if "site-packages" not in path:
                    tpl_dir = os.path.join(path, "templates")
                    if os.path.isdir(tpl_dir):
                        dirs.append(tpl_dir)
    return dirs
