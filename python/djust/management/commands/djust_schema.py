"""
Management command to output the djust framework and project schema as JSON.

Generates a machine-readable registry of all directives, lifecycle methods,
decorators, and project-specific LiveViews.

Usage:
    python manage.py djust_schema                   # full schema (framework + project)
    python manage.py djust_schema --framework-only  # static framework schema only
    python manage.py djust_schema --project-only    # project introspection only
    python manage.py djust_schema --indent 4        # pretty-print with indent
"""

import json

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Output the djust framework and project schema as JSON"

    def add_arguments(self, parser):
        parser.add_argument(
            "--framework-only",
            action="store_true",
            dest="framework_only",
            help="Output only the static framework schema (no project introspection)",
        )
        parser.add_argument(
            "--project-only",
            action="store_true",
            dest="project_only",
            help="Output only the project-specific schema (requires Django apps loaded)",
        )
        parser.add_argument(
            "--indent",
            type=int,
            default=2,
            help="JSON indentation level (default: 2, use 0 for compact)",
        )

    def handle(self, *args, **options):
        from djust.schema import get_framework_schema, get_project_schema

        framework_only = options.get("framework_only", False)
        project_only = options.get("project_only", False)
        indent = options.get("indent", 2) or None

        output = {}

        if not project_only:
            output["framework"] = get_framework_schema()

        if not framework_only:
            output["project"] = get_project_schema()

        self.stdout.write(json.dumps(output, indent=indent, default=str))
