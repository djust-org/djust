"""
Management command: ``python manage.py djust_gen_live``

Generate a complete CRUD LiveView scaffold for a Django model.

Usage::

    python manage.py djust_gen_live blog Post title:string body:text
    python manage.py djust_gen_live blog Post title:string --dry-run
    python manage.py djust_gen_live blog Post title:string --force
    python manage.py djust_gen_live blog Post title:string --api
    python manage.py djust_gen_live blog Post title:string --no-tests
    python manage.py djust_gen_live blog Post author:fk:User title:string body:text
"""

import logging

from django.core.management.base import BaseCommand, CommandError

from djust.scaffolding.gen_live import generate_liveview, parse_field_defs

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Generate a CRUD LiveView scaffold for a Django model. "
        "Creates views.py, urls.py, HTML template, and tests."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "app_name",
            type=str,
            help="Django app name (directory must exist).",
        )
        parser.add_argument(
            "model_name",
            type=str,
            help="PascalCase model name (e.g. Post, BlogPost).",
        )
        parser.add_argument(
            "fields",
            nargs="*",
            type=str,
            help=(
                "Field definitions as name:type (e.g. title:string body:text). "
                "Supported types: string, text, integer, float, decimal, boolean, "
                "date, datetime, email, url, slug, fk:ModelName."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Preview files that would be created without writing them.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            default=False,
            help="Overwrite existing files.",
        )
        parser.add_argument(
            "--no-tests",
            action="store_true",
            default=False,
            help="Skip generating the test file.",
        )
        parser.add_argument(
            "--api",
            action="store_true",
            default=False,
            help="Generate a JSON API (render_json) instead of HTML templates.",
        )

    def handle(self, *args, **options):
        app_name = options["app_name"]
        model_name = options["model_name"]
        field_specs = options.get("fields", []) or []

        # Parse field definitions
        try:
            fields = parse_field_defs(field_specs)
        except ValueError as e:
            raise CommandError(str(e)) from e

        # Build options dict for generator
        gen_options = {
            "dry_run": options["dry_run"],
            "force": options["force"],
            "no_tests": options["no_tests"],
            "api": options["api"],
        }

        # Run generator
        try:
            result = generate_liveview(
                app_name=app_name,
                model_name=model_name,
                fields=fields,
                options=gen_options,
            )
        except (ValueError, FileNotFoundError, FileExistsError) as e:
            raise CommandError(str(e)) from e

        # Output
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry run — no files written.\n"))
            self.stdout.write("Would create:\n")
            for path in result:
                self.stdout.write("  %s\n" % path)
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "Generated %s LiveView scaffold in '%s'.\n" % (model_name, app_name)
                )
            )
            self.stdout.write(
                "\nNext steps:\n"
                "  1. Add '%s' to INSTALLED_APPS if not already there\n"
                "  2. Add '%s.views' to LIVEVIEW_ALLOWED_MODULES\n"
                "  3. Include '%s.urls' in your root urlconf\n"
                "  4. Create the %s model in %s/models.py\n"
                "  5. Run 'python manage.py makemigrations && python manage.py migrate'\n"
                % (app_name, app_name, app_name, model_name, app_name)
            )
