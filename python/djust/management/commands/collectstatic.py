"""``collectstatic``, then the app SBOM when DJUST_SBOM_PATH is set (ADR-040).

Takes effect only when "djust" precedes "django.contrib.staticfiles" in
INSTALLED_APPS; check djust.B013 errors when it does not and an SBOM path is set.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.contrib.staticfiles.management.commands.collectstatic import Command as BaseCommand


class Command(BaseCommand):
    def handle(self, **options: Any) -> str | None:
        result = super().handle(**options)
        path = getattr(settings, "DJUST_SBOM_PATH", None)
        if path and not options.get("dry_run"):
            from djust.assets.sbom import write_app_sbom

            write_app_sbom(path)
            if options.get("verbosity", 1) >= 1:
                self.stdout.write(f"Wrote djust asset SBOM to {path}")
        return result
