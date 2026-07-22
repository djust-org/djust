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
    python manage.py djust_surface_manifest                       # compact JSON to stdout
    python manage.py djust_surface_manifest --indent 2            # pretty-print to stdout
    python manage.py djust_surface_manifest --output manifest.json  # write clean JSON to a file

The ``--output <file>`` form is the phase-2-consumer path: in a ``DEBUG=True``
environment the HVR ``[HotReload]`` banner writes to stdout, so
``... > manifest.json`` would capture that banner ahead of the JSON and
corrupt the file. ``--output`` writes ONLY the JSON (no banner, no trailing
newline noise) directly to the given path.
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
        parser.add_argument(
            "--output",
            type=str,
            default=None,
            help=(
                "Write the JSON manifest to this file instead of stdout. "
                "Use this for machine consumption — it avoids the DEBUG-mode "
                "[HotReload] stdout banner that would corrupt a `> file` redirect."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from djust.schema import get_surface_manifest

        indent = options.get("indent", 0) or None
        output_path = options.get("output")

        payload = json.dumps(get_surface_manifest(), indent=indent, default=str)

        if output_path:
            with open(output_path, "w", encoding="utf-8") as fh:
                fh.write(payload)
            self.stderr.write("Surface manifest written to %s" % output_path)
        else:
            self.stdout.write(payload)
