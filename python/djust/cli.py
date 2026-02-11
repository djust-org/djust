#!/usr/bin/env python3
"""
djust CLI - Command-line tools for djust developers

Usage:
    python -m djust startproject <name>   Create a new djust project
    python -m djust startapp <name>       Create a new djust app

    python -m djust.cli stats             Show state backend statistics
    python -m djust.cli health            Run health checks on backends
    python -m djust.cli profile           Show profiling statistics
    python -m djust.cli analyze <path>    Analyze LiveView templates
    python -m djust.cli clear             Clear state backend caches

Examples:
    python -m djust startproject mysite
    python -m djust startapp dashboard
    python -m djust.cli stats
"""

import argparse
import os
import re
import sys


def setup_django():
    """Set up Django environment if not already configured."""
    try:
        import django
        from django.conf import settings

        if not settings.configured:
            raise django.core.exceptions.ImproperlyConfigured
        return True
    except Exception:
        print("Warning: Django not configured. Some features may be limited.")
        return False


def cmd_stats(args):
    """Show state backend statistics."""
    setup_django()

    try:
        from djust.state_backend import get_backend

        backend = get_backend()

        print("\n=== djust State Backend Statistics ===\n")

        # Basic stats
        stats = backend.get_stats()
        print(f"Backend Type: {stats.get('backend', 'unknown')}")
        print(f"Total Sessions: {stats.get('total_sessions', 0)}")

        if "oldest_session_age" in stats:
            print(f"Oldest Session: {stats['oldest_session_age']:.1f}s ago")
        if "newest_session_age" in stats:
            print(f"Newest Session: {stats['newest_session_age']:.1f}s ago")
        if "average_age" in stats:
            print(f"Average Age: {stats['average_age']:.1f}s")

        # Memory stats
        print("\n--- Memory Usage ---")
        memory_stats = backend.get_memory_stats()
        if "total_state_bytes" in memory_stats:
            print(f"Total State: {memory_stats.get('total_state_kb', 0):.2f} KB")
            print(f"Average State: {memory_stats.get('average_state_kb', 0):.2f} KB")

        if "largest_sessions" in memory_stats and memory_stats["largest_sessions"]:
            print("\nLargest Sessions:")
            for session in memory_stats["largest_sessions"][:5]:
                print(f"  - {session['key']}: {session.get('size_kb', 0):.2f} KB")

        # Compression stats (Redis only)
        if hasattr(backend, "get_compression_stats"):
            compression = backend.get_compression_stats()
            if compression.get("enabled"):
                print("\n--- Compression ---")
                print(f"Compressed: {compression.get('compressed_count', 0)} states")
                print(f"Uncompressed: {compression.get('uncompressed_count', 0)} states")
                print(f"Bytes Saved: {compression.get('total_kb_saved', 0):.2f} KB")
                print(f"Compression Rate: {compression.get('compression_rate_percent', 0):.1f}%")

        print()

    except ImportError as e:
        print(f"Error: Could not import djust modules: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error getting stats: {e}")
        sys.exit(1)


def cmd_health(args):
    """Run health checks."""
    setup_django()

    try:
        from djust.state_backend import get_backend

        backend = get_backend()

        print("\n=== djust Health Check ===\n")

        health = backend.health_check()
        status = health.get("status", "unknown")

        # Status with color
        status_icon = "+" if status == "healthy" else "!"
        print(f"[{status_icon}] Backend Status: {status.upper()}")
        print(f"    Backend Type: {health.get('backend', 'unknown')}")
        print(f"    Latency: {health.get('latency_ms', 0):.2f}ms")

        if health.get("error"):
            print(f"    Error: {health['error']}")

        if health.get("details"):
            print("    Details:")
            for key, value in health["details"].items():
                print(f"      {key}: {value}")

        # Additional checks
        print("\n--- Additional Checks ---")

        # Check Rust extension
        try:
            from djust._rust import RustLiveView  # noqa: F401

            print("[+] Rust extension: Available")
        except ImportError:
            print("[!] Rust extension: Not available (performance degraded)")

        # Check optional dependencies
        try:
            import zstandard  # noqa: F401

            print("[+] zstd compression: Available")
        except ImportError:
            print("[ ] zstd compression: Not installed (pip install zstandard)")

        try:
            import orjson  # noqa: F401

            print("[+] orjson: Available (faster JSON)")
        except ImportError:
            print("[ ] orjson: Not installed (pip install orjson)")

        print()

        return 0 if status == "healthy" else 1

    except Exception as e:
        print(f"Error running health check: {e}")
        return 1


def cmd_profile(args):
    """Show profiling statistics."""
    setup_django()

    try:
        from djust.profiler import profiler

        print("\n=== djust Profiler Statistics ===\n")

        metrics = profiler.get_metrics()

        if not metrics.get("enabled"):
            print("Profiler is currently DISABLED.")
            print("Enable it in Django settings:")
            print("  DJUST_CONFIG = {'profiler_enabled': True}")
            print("\nOr programmatically:")
            print("  from djust.profiler import profiler")
            print("  profiler.enable()")
            return

        # Summary
        summary = metrics.get("summary", {})
        if summary.get("message"):
            print(summary["message"])
            return

        print(f"Total Operations: {summary.get('total_operations', 0)}")
        print(f"Total Time: {summary.get('total_time_ms', 0):.2f}ms")
        print(f"Unique Operations: {summary.get('unique_operations', 0)}")

        # Slowest operations
        if summary.get("slowest_operations"):
            print("\nSlowest Operations (avg):")
            for op in summary["slowest_operations"]:
                print(f"  {op['name']}: {op['avg_ms']:.2f}ms")

        # Most frequent
        if summary.get("most_frequent"):
            print("\nMost Frequent Operations:")
            for op in summary["most_frequent"]:
                print(f"  {op['name']}: {op['count']} calls")

        # Detailed metrics by category
        if args.verbose:
            for category in ["rendering", "state_management", "event_handling", "other"]:
                ops = metrics.get(category, {})
                if ops:
                    print(f"\n--- {category.replace('_', ' ').title()} ---")
                    for name, data in ops.items():
                        print(f"  {name}:")
                        print(
                            f"    count: {data['count']}, avg: {data['avg_ms']:.2f}ms, "
                            f"p95: {data['p95_ms']:.2f}ms, max: {data['max_ms']:.2f}ms"
                        )

        print()

    except ImportError as e:
        print(f"Error: Could not import profiler: {e}")
        sys.exit(1)


def cmd_analyze(args):
    """Analyze LiveView templates for optimization opportunities."""
    if not args.path:
        print("Error: Please provide a path to analyze")
        print("Usage: python -m djust.cli analyze <path>")
        sys.exit(1)

    path = args.path
    if not os.path.exists(path):
        print(f"Error: Path does not exist: {path}")
        sys.exit(1)

    print(f"\n=== djust Template Analysis: {path} ===\n")

    issues = []
    suggestions = []

    # Read the file
    if os.path.isfile(path):
        files_to_check = [path]
    else:
        files_to_check = []
        for root, dirs, files in os.walk(path):
            for f in files:
                if f.endswith(".py") or f.endswith(".html"):
                    files_to_check.append(os.path.join(root, f))

    for filepath in files_to_check:
        with open(filepath, "r") as f:
            content = f.read()
            lines = content.split("\n")

        # Check for potential issues
        for i, line in enumerate(lines, 1):
            # Large list rendering without dj-update
            if "for " in line and "in " in line and "{% for" in line:
                if (
                    "dj-update"
                    not in content[max(0, content.find(line) - 200) : content.find(line) + 200]
                ):
                    suggestions.append(
                        {
                            "file": filepath,
                            "line": i,
                            "issue": "Loop without dj-update",
                            "suggestion": 'Consider adding dj-update="append" for large lists to enable efficient updates',
                        }
                    )

            # Missing temporary_assigns
            if "class " in line and "LiveView" in line:
                class_end = content.find("\n\nclass", content.find(line))
                if class_end == -1:
                    class_end = len(content)
                class_content = content[content.find(line) : class_end]
                if "temporary_assigns" not in class_content and (
                    "items" in class_content or "messages" in class_content
                ):
                    suggestions.append(
                        {
                            "file": filepath,
                            "line": i,
                            "issue": "Large collection without temporary_assigns",
                            "suggestion": "Consider using temporary_assigns for collections to free memory after render",
                        }
                    )

            # Inefficient queryset in template
            if ".all" in line or ".filter" in line:
                if ".html" in filepath:
                    issues.append(
                        {
                            "file": filepath,
                            "line": i,
                            "issue": "QuerySet evaluation in template",
                            "suggestion": "Move QuerySet evaluation to Python code for better performance",
                        }
                    )

    # Print results
    if issues:
        print("ISSUES FOUND:")
        for issue in issues:
            print(f"  [{issue['file']}:{issue['line']}] {issue['issue']}")
            print(f"    Suggestion: {issue['suggestion']}\n")

    if suggestions:
        print("SUGGESTIONS:")
        for s in suggestions:
            print(f"  [{s['file']}:{s['line']}] {s['issue']}")
            print(f"    Suggestion: {s['suggestion']}\n")

    if not issues and not suggestions:
        print("No issues or suggestions found!")

    print(f"\nAnalyzed {len(files_to_check)} file(s)")


def cmd_startproject(args):
    """Create a new djust project with all boilerplate pre-configured."""
    name = args.name
    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", name):
        print(f"Error: '{name}' is not a valid Python identifier.")
        sys.exit(1)

    if os.path.exists(name):
        print(f"Error: Directory '{name}' already exists.")
        sys.exit(1)

    project_dir = os.path.join(os.getcwd(), name)
    package_dir = os.path.join(project_dir, name)
    os.makedirs(package_dir)

    # manage.py
    _write(
        os.path.join(project_dir, "manage.py"),
        f'''#!/usr/bin/env python
"""Django\'s command-line utility for administrative tasks."""
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "{name}.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn\'t import Django. Are you sure it\'s installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
''',
    )

    # settings.py
    import secrets

    secret_key = secrets.token_urlsafe(50)
    _write(
        os.path.join(package_dir, "settings.py"),
        f'''"""
Django settings for {name} project.
Generated by `python -m djust startproject`.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("SECRET_KEY", "django-insecure-{secret_key}")  # noqa: S105

DEBUG = True

ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "djust",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "{name}.urls"

TEMPLATES = [
    {{
        "BACKEND": "djust.template_backend.DjustTemplateBackend",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {{
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        }},
    }},
]

ASGI_APPLICATION = "{name}.asgi.application"

DATABASES = {{
    "default": {{
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }}
}}

CHANNEL_LAYERS = {{
    "default": {{
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }}
}}

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LIVEVIEW_ALLOWED_MODULES = []
''',
    )

    # asgi.py
    _write(
        os.path.join(package_dir, "asgi.py"),
        f'''"""
ASGI config for {name} project.
"""

import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "{name}.settings")
django.setup()

from djust.routing import live_session  # noqa: E402

application = live_session()
''',
    )

    # wsgi.py
    _write(
        os.path.join(package_dir, "wsgi.py"),
        f'''"""
WSGI config for {name} project.
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "{name}.settings")

application = get_wsgi_application()
''',
    )

    # urls.py
    _write(
        os.path.join(package_dir, "urls.py"),
        f'''"""URL configuration for {name} project."""

from django.contrib import admin
from django.urls import path

urlpatterns = [
    path("admin/", admin.site.urls),
]
''',
    )

    # __init__.py
    _write(os.path.join(package_dir, "__init__.py"), "")

    # templates directory
    os.makedirs(os.path.join(project_dir, "templates"))

    print(f"\nCreated djust project '{name}/'")
    print("\nNext steps:")
    print(f"  cd {name}")
    print("  pip install djust daphne whitenoise channels")
    print("  python manage.py migrate")
    print("  python -m djust startapp home")
    print("  python manage.py runserver")
    print()


def cmd_startapp(args):
    """Create a new djust app with a LiveView and template."""
    name = args.name
    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", name):
        print(f"Error: '{name}' is not a valid Python identifier.")
        sys.exit(1)

    if os.path.exists(name):
        print(f"Error: Directory '{name}' already exists.")
        sys.exit(1)

    app_dir = os.path.join(os.getcwd(), name)
    template_dir = os.path.join(app_dir, "templates", name)
    os.makedirs(template_dir)

    class_name = name.replace("_", " ").title().replace(" ", "") + "View"

    # __init__.py
    _write(os.path.join(app_dir, "__init__.py"), "")

    # apps.py
    app_class = name.replace("_", " ").title().replace(" ", "") + "Config"
    _write(
        os.path.join(app_dir, "apps.py"),
        f"""from django.apps import AppConfig


class {app_class}(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "{name}"
""",
    )

    # models.py
    _write(os.path.join(app_dir, "models.py"), "")

    # views.py
    _write(
        os.path.join(app_dir, "views.py"),
        f"""from djust import LiveView
from djust.decorators import event_handler


class {class_name}(LiveView):
    template_name = "{name}/index.html"

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def increment(self, **kwargs):
        self.count += 1

    @event_handler()
    def decrement(self, **kwargs):
        self.count -= 1

    def get_context_data(self, **kwargs):
        return {{"count": self.count}}
""",
    )

    # template
    _write(
        os.path.join(template_dir, "index.html"),
        f"""<div data-djust-view="{name}.views.{class_name}">
  <h1>{name.replace("_", " ").title()}</h1>
  <p>Count: {{{{ count }}}}</p>
  <button dj-click="increment">+</button>
  <button dj-click="decrement">-</button>
</div>
""",
    )

    # urls.py
    _write(
        os.path.join(app_dir, "urls.py"),
        f"""from django.urls import path
from .views import {class_name}

urlpatterns = [
    path("", {class_name}.as_view(), name="{name}_index"),
]
""",
    )

    print(f"\nCreated djust app '{name}/'")
    print("\nNext steps:")
    print(f'  1. Add "{name}" to INSTALLED_APPS in settings.py')
    print(f'  2. Add "{name}.views" to LIVEVIEW_ALLOWED_MODULES in settings.py')
    print("  3. Add to urls.py:")
    print(f'     path("{name}/", include("{name}.urls"))')
    print()


def _write(filepath, content):
    """Write content to a file, creating parent directories if needed."""
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    with open(filepath, "w") as f:
        f.write(content)


def cmd_clear(args):
    """Clear state backend caches."""
    setup_django()

    try:
        from djust.state_backend import get_backend

        backend = get_backend()

        if not args.force:
            print("WARNING: This will clear all LiveView session state.")
            response = input("Are you sure? (yes/no): ")
            if response.lower() != "yes":
                print("Aborted.")
                return

        # Get stats before clearing
        stats_before = backend.get_stats()
        sessions_before = stats_before.get("total_sessions", 0)

        # Clear expired sessions
        cleaned = backend.cleanup_expired(ttl=0 if args.all else None)

        print(f"\nCleared {cleaned} session(s)")
        print(f"Sessions before: {sessions_before}")

        stats_after = backend.get_stats()
        print(f"Sessions after: {stats_after.get('total_sessions', 0)}")

    except Exception as e:
        print(f"Error clearing cache: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="djust CLI - Developer tools for djust framework",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # startproject command
    sp_parser = subparsers.add_parser("startproject", help="Create a new djust project")
    sp_parser.add_argument("name", help="Project name")

    # startapp command
    sa_parser = subparsers.add_parser("startapp", help="Create a new djust app with LiveView")
    sa_parser.add_argument("name", help="App name")

    # stats command
    subparsers.add_parser("stats", help="Show state backend statistics")

    # health command
    subparsers.add_parser("health", help="Run health checks")

    # profile command
    profile_parser = subparsers.add_parser("profile", help="Show profiling statistics")
    profile_parser.add_argument(
        "-v", "--verbose", action="store_true", help="Show detailed metrics"
    )

    # analyze command
    analyze_parser = subparsers.add_parser("analyze", help="Analyze templates for optimization")
    analyze_parser.add_argument("path", nargs="?", help="Path to analyze")

    # clear command
    clear_parser = subparsers.add_parser("clear", help="Clear state backend caches")
    clear_parser.add_argument("-f", "--force", action="store_true", help="Skip confirmation prompt")
    clear_parser.add_argument(
        "-a", "--all", action="store_true", help="Clear all sessions (not just expired)"
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    # Execute command
    commands = {
        "startproject": cmd_startproject,
        "startapp": cmd_startapp,
        "stats": cmd_stats,
        "health": cmd_health,
        "profile": cmd_profile,
        "analyze": cmd_analyze,
        "clear": cmd_clear,
    }

    if args.command in commands:
        result = commands[args.command](args)
        sys.exit(result if result else 0)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
