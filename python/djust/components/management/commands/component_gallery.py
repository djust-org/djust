"""Management command to serve the component gallery for visual QA.

Usage:
    python manage.py component_gallery              # Serve on port 8765
    python manage.py component_gallery --port 9000  # Custom port
    python manage.py component_gallery --dry-run    # List components and exit
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Launch a local server showing a visual gallery of all djust-components."

    def add_arguments(self, parser):
        parser.add_argument(
            "--port",
            type=int,
            default=8765,
            help="Port to serve the gallery on (default: 8765)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print discovered components and exit without starting the server.",
        )

    def handle(self, *args, **options):
        if options["dry_run"]:
            self._print_discovery(options)
            return

        port = options["port"]
        self.stdout.write(
            self.style.SUCCESS(f"Starting component gallery on http://localhost:{port}/")
        )
        self.stdout.write("Press Ctrl+C to stop.\n")

        self._serve(port)

    def _print_discovery(self, options):
        """Print discovered components grouped by category."""
        from djust.components.gallery.registry import get_gallery_data

        data = get_gallery_data()
        categories = data["categories"]

        total = 0
        for cat_label, components in sorted(categories.items()):
            self.stdout.write(self.style.MIGRATE_HEADING(f"\n{cat_label}"))
            for comp in components:
                comp_type = "tag" if comp["type"] == "tag" else "class"
                variant_count = len(comp["variants"])
                self.stdout.write(
                    f"  {comp['label']:<30} [{comp_type}] "
                    f"({variant_count} variant{'s' if variant_count != 1 else ''})"
                )
                total += 1

        self.stdout.write(
            self.style.SUCCESS(f"\nTotal: {total} components across {len(categories)} categories")
        )

    def _serve(self, port):
        """Start a lightweight Django dev server with gallery URL routing."""
        from django.conf import settings
        from django.core.handlers.wsgi import WSGIHandler
        from django.core.servers.basehttp import run
        from django.urls import path

        from django.urls import include

        # Dynamically set ROOT_URLCONF to our gallery URLs
        import types

        urlpatterns_module = types.ModuleType("_gallery_urls")
        urlpatterns_module.urlpatterns = [
            path("", include("djust.components.gallery.urls")),
        ]

        import sys

        sys.modules["_gallery_urls"] = urlpatterns_module

        # Temporarily override ROOT_URLCONF
        original_urlconf = getattr(settings, "ROOT_URLCONF", None)
        settings.ROOT_URLCONF = "_gallery_urls"

        try:
            run(
                addr="127.0.0.1",
                port=port,
                wsgi_handler=WSGIHandler(),
                ipv6=False,
                threading=True,
            )
        except KeyboardInterrupt:
            self.stdout.write("\nGallery server stopped.")
        finally:
            if original_urlconf:
                settings.ROOT_URLCONF = original_urlconf
