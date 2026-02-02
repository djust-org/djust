"""
Django management command to run djust smoke tests against a running server.

Usage:
    python manage.py djust_smoke
    python manage.py djust_smoke --port 8089
    python manage.py djust_smoke --url http://example.com
    python manage.py djust_smoke --no-headless
"""

import asyncio
import sys

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run Playwright smoke tests against a running djust app"

    def add_arguments(self, parser):
        parser.add_argument(
            '--port', type=int, default=8000,
            help='Port of the running server (default: 8000)',
        )
        parser.add_argument(
            '--url', type=str, default=None,
            help='Full base URL (overrides --port)',
        )
        parser.add_argument(
            '--headless', action='store_true', default=True,
            help='Run browser headless (default)',
        )
        parser.add_argument(
            '--no-headless', action='store_true',
            help='Run browser with visible UI',
        )
        parser.add_argument(
            '--timeout', type=int, default=10000,
            help='Timeout in ms (default: 10000)',
        )

    def handle(self, *args, **options):
        try:
            from djust.testing.smoke_test import DjustSmokeTest, print_results
        except ImportError:
            self.stderr.write(self.style.ERROR(
                "\nError: Playwright is not installed.\n\n"
                "Install it with:\n"
                "  pip install playwright\n"
                "  playwright install chromium\n"
            ))
            sys.exit(1)

        base_url = options['url'] or f"http://localhost:{options['port']}"
        headless = not options['no_headless']

        smoke = DjustSmokeTest(
            base_url=base_url,
            headless=headless,
            timeout_ms=options['timeout'],
        )

        try:
            results = asyncio.run(smoke.run_all())
        except RuntimeError as e:
            self.stderr.write(self.style.ERROR(f"\n{e}\n"))
            sys.exit(1)

        all_passed = print_results(results)
        if not all_passed:
            sys.exit(1)
