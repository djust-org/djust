"""``collectstatic``, then the app SBOM when DJUST_SBOM_PATH is set (ADR-040).

Takes effect only when "djust" precedes "django.contrib.staticfiles" in
INSTALLED_APPS; check djust.B013 errors when it does not and an SBOM path is set.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.contrib.staticfiles.management.commands.collectstatic import Command as BaseCommand
from django.core.management import CommandError


class Command(BaseCommand):
    def check(self, app_configs: Any = None, tags: Any = None, **kwargs: Any) -> None:
        """Django's staticfiles checks, then the djust checks that guard what
        collectstatic publishes. B008 is a deploy check (it lists every static
        file, #3144), so it is run here explicitly rather than on every
        runserver start."""
        from djust.checks.assets import COLLECTSTATIC_TAG

        super().check(app_configs=app_configs, tags=tags, **kwargs)
        kwargs.pop("include_deployment_checks", None)
        super().check(
            app_configs=app_configs,
            tags=[COLLECTSTATIC_TAG],
            include_deployment_checks=True,
            **kwargs,
        )

    def handle(self, **options: Any) -> str | None:
        from djust.assets.sbom import sbom_setting_type_error

        path = getattr(settings, "DJUST_SBOM_PATH", None)
        # Before collecting: a value B012 rejects would otherwise raise a
        # TypeError after the files were already published (#3146).
        problem = sbom_setting_type_error(path)
        if problem is not None:
            raise CommandError(problem)
        result = super().handle(**options)
        if path and not options.get("dry_run"):
            from djust.assets.sbom import write_app_sbom

            write_app_sbom(path)
            if options.get("verbosity", 1) >= 1:
                self.stdout.write(f"Wrote djust asset SBOM to {path}")
        return result
