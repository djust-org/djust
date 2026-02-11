"""
Management command to scaffold a new djust project.

Usage:
    python manage.py djust_new myapp                      # Basic app
    python manage.py djust_new myapp --with-auth           # With login/logout
    python manage.py djust_new myapp --with-db             # With models + admin
    python manage.py djust_new myapp --with-presence       # With presence tracking
    python manage.py djust_new myapp --with-streaming      # With streaming
    python manage.py djust_new myapp --from-schema s.json  # From JSON schema

Also available as a CLI command:
    python -m djust new myapp [options]
"""

import logging

from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Generate a new djust project with optional features"

    def add_arguments(self, parser):
        parser.add_argument(
            "app_name",
            help="Name for the new project (must be a valid Python identifier)",
        )
        parser.add_argument(
            "--with-auth",
            action="store_true",
            dest="with_auth",
            help="Include login/logout views and auth middleware",
        )
        parser.add_argument(
            "--with-db",
            action="store_true",
            dest="with_db",
            help="Include Django models, admin, and database-backed views",
        )
        parser.add_argument(
            "--with-presence",
            action="store_true",
            dest="with_presence",
            help="Include PresenceMixin for online user tracking",
        )
        parser.add_argument(
            "--with-streaming",
            action="store_true",
            dest="with_streaming",
            help="Include StreamingMixin for real-time stream updates",
        )
        parser.add_argument(
            "--from-schema",
            dest="from_schema",
            metavar="SCHEMA_FILE",
            help="Path to a JSON schema file describing models",
        )
        parser.add_argument(
            "--no-setup",
            action="store_true",
            dest="no_setup",
            help="Skip automatic venv/install/migrate setup",
        )
        parser.add_argument(
            "--target-dir",
            dest="target_dir",
            metavar="DIR",
            help="Parent directory to create project in (default: current directory)",
        )

    def handle(self, *args, **options):
        from djust.scaffolding.generator import generate_project

        app_name = options["app_name"]

        try:
            generate_project(
                app_name=app_name,
                target_dir=options.get("target_dir"),
                with_auth=options["with_auth"],
                with_db=options["with_db"],
                with_presence=options["with_presence"],
                with_streaming=options["with_streaming"],
                from_schema=options.get("from_schema"),
                auto_setup=not options["no_setup"],
            )
        except ValueError as e:
            raise CommandError(str(e))

        # Print success message
        features = []
        if options["with_auth"]:
            features.append("auth")
        if options["with_db"]:
            features.append("database")
        if options["with_presence"]:
            features.append("presence")
        if options["with_streaming"]:
            features.append("streaming")
        if options.get("from_schema"):
            features.append("schema-generated models")

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Created djust project '%s'" % app_name))
        if features:
            self.stdout.write("  Features: %s" % ", ".join(features))
        self.stdout.write("")
        self.stdout.write("  Next steps:")
        self.stdout.write("    cd %s" % app_name)
        self.stdout.write("    make dev")
        self.stdout.write("")
