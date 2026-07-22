"""
Management command to output djust's machine-readable surface manifest as JSON.

The manifest covers every ``dj-*`` template directive, every ``djust.js.JS``
command, and the public LiveView API (lifecycle methods, class attributes,
decorators, navigation/stream/push-event methods) — see
:func:`djust.schema.get_surface_manifest` for the full contract and #2064
for why it exists (djust.org's directive reference silently drifted out of
sync with the framework for months; this manifest is what a downstream
consumer's CI diffs its reference doc fixture against).

Usage:
    python manage.py djust_surface_manifest                # compact JSON
    python manage.py djust_surface_manifest --indent 2      # pretty-print
"""

import json
from typing import Any

from django.core.management.base import BaseCommand, CommandParser


class Command(BaseCommand):
    help = "Output djust's machine-readable surface manifest (directives, JS commands, view API) as JSON"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--indent",
            type=int,
            default=0,
            help="JSON indentation level (default: 0, compact single-line output)",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from djust.schema import get_surface_manifest

        indent = options.get("indent", 0) or None

        self.stdout.write(json.dumps(get_surface_manifest(), indent=indent, default=str))
