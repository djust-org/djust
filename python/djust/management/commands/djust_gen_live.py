"""
Management command ``djust_gen_live`` — Model-to-LiveView scaffolding.

Generates a complete CRUD LiveView from a model definition, including:
- ``views.py`` — LiveView class with list/show/create/edit/delete handlers
- ``urls.py`` — URL patterns with live_session routing
- ``templates/<app>/<model>_list.html`` — List view with inline show/edit panel
- ``tests/test_<model>_crud.py`` — LiveViewTestClient smoke tests

Usage::

    python manage.py djust_gen_live posts Post title:string body:text published:boolean

    # With foreign key
    python manage.py djust_gen_live comments Comment post:fk:Post author:fk:User body:text

    # Dry run (preview without writing)
    python manage.py djust_gen_live posts Post title:string --dry-run

    # Overwrite existing files
    python manage.py djust_gen_live posts Post title:string --force
"""

import logging
import re

from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Generate a CRUD LiveView from a model definition"

    def add_arguments(self, parser):
        parser.add_argument(
            "app_name",
            help=(
                "Django app name (e.g., ``blog``). "
                "The app directory must exist with a ``models.py`` file."
            ),
        )
        parser.add_argument(
            "model_name",
            help=(
                "Model class name in PascalCase (e.g., ``Post``, ``BlogPost``). "
                "Must be a valid Python identifier starting with an uppercase letter."
            ),
        )
        parser.add_argument(
            "fields",
            nargs="*",
            help=(
                "Field definitions as ``name:type`` pairs. "
                "Example: ``title:string`` ``body:text`` ``published:boolean``. "
                "Use ``fk:ModelName`` for foreign key fields. "
                "Valid types: string, text, integer, float, boolean, date, "
                "datetime, email, url, slug, decimal."
            ),
        )
        parser.add_argument(
            "--belongs-to",
            dest="belongs_to",
            metavar="MODEL",
            help=(
                "Add a foreign key to the specified model (e.g., ``User``). "
                "Creates a ``fk:ModelName`` field automatically."
            ),
        )
        parser.add_argument(
            "--model",
            dest="model",
            metavar="MODEL_NAME",
            help=(
                "Introspect an existing Django model and use its field definitions. "
                "Takes the model class name (e.g., ``Post``) and inspects the model's fields. "
                "Use this instead of passing field definitions as positional arguments."
            ),
        )
        parser.add_argument(
            "--no-tests",
            action="store_true",
            dest="no_tests",
            help="Skip generating the test file.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            dest="force",
            help="Overwrite existing files without prompting.",
        )
        parser.add_argument(
            "--api",
            action="store_true",
            dest="api",
            help="Generate JSON API LiveView using render_json() instead of templates.",
        )

    def handle(self, *args, **options):
        app_name = options["app_name"]
        model_name = options["model_name"]
        field_defs = options["fields"] or []
        belongs_to = options.get("belongs_to")
        no_tests = options["no_tests"]
        force = options["force"]
        dry_run = options["dry_run"]
        api = options["api"]
        model_introspect = options.get("model")

        # Validate app_name
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", app_name):
            raise CommandError(
                "'%s' is not a valid app name. "
                "Use letters, numbers, and underscores (e.g., ``blog``, ``my_app``)." % app_name
            )

        # Add belongs_to FK if specified
        if belongs_to:
            fk_field = "fk:%s" % belongs_to
            field_defs = list(field_defs)
            field_defs.append(fk_field)

        # Parse field definitions or introspect model
        try:
            from djust.scaffolding.gen_live import (
                parse_field_defs,
                introspect_model,
                generate_liveview,
                GenerationError,
            )

            if model_introspect:
                fields = introspect_model(model_introspect, app_name)
            else:
                fields = parse_field_defs(field_defs)
        except ValueError as e:
            raise CommandError(str(e))

        # Build options dict
        gen_options = {
            "force": force,
            "dry_run": dry_run,
            "no_tests": no_tests,
            "api": api,
        }

        # Generate
        try:
            generate_liveview(
                app_name=app_name,
                model_name=model_name,
                fields=fields,
                options=gen_options,
            )
        except GenerationError as e:
            raise CommandError(str(e))
        except OSError as e:
            raise CommandError("Failed to write files: %s" % e)

        # Print success message
        if dry_run:
            self.stdout.write(self.style.WARNING("[DRY RUN] Files not written."))
            self.stdout.write("")
            self.stdout.write("To generate, run without --dry-run:")
            self.stdout.write(
                "  python manage.py djust_gen_live %s %s %s"
                % (app_name, model_name, " ".join(field_defs))
            )
        else:
            self.stdout.write("")
            self.stdout.write(
                self.style.SUCCESS("Generated LiveView for %s.%s" % (app_name, model_name))
            )
            self.stdout.write("")
            self.stdout.write(
                "  views.py                — LiveView class (%sListView)" % model_name
            )
            self.stdout.write("  urls.py                 — URL routing")
            self.stdout.write(
                "  templates/%s/%s_list.html — List template" % (app_name, model_name.lower())
            )
            if not no_tests:
                self.stdout.write("  tests/test_%s_crud.py  — Smoke tests" % model_name.lower())
            self.stdout.write("")
            self.stdout.write("  Next steps:")
            self.stdout.write("    1. Review the generated files")
            self.stdout.write("    2. Add the model to your app's models.py:")
            self.stdout.write("         from django.db import models")
            self.stdout.write("         class %s(models.Model):" % model_name)
            for f in fields:
                type_to_model = {
                    "string": "    %s = models.CharField(max_length=255)" % f["name"],
                    "text": "    %s = models.TextField()" % f["name"],
                    "integer": "    %s = models.IntegerField()" % f["name"],
                    "boolean": "    %s = models.BooleanField(default=False)" % f["name"],
                    "fk": "    %s = models.ForeignKey(%s, on_delete=models.CASCADE)"
                    % (f["name"], f["related_model"]),
                }
                model_line = type_to_model.get(
                    f["type"], "    %s = models.CharField(max_length=255)" % f["name"]
                )
                self.stdout.write("         " + model_line)
            self.stdout.write("    3. Run: python manage.py makemigrations %s" % app_name)
            self.stdout.write("    4. Run: python manage.py migrate")
            self.stdout.write("    5. Add to your app's urls.py:")
            self.stdout.write("         from django.urls import path")
            self.stdout.write("         from .views import %sListView" % model_name)
            self.stdout.write(
                "         urlpatterns += [path('%s/', %sListView.as_view())]"
                % (model_name.lower() + "s", model_name + "ListView")
            )
            self.stdout.write("")
