"""List the names legacy exposure infers per LiveView, with values redacted.

ADR-038 *Compatibility and rollout*: "Provide a values-redacted inventory of
inferred names and their destinations before migration." For each LiveView
class this reports the names legacy mode would put into template context,
``get_state``/snapshots and private session persistence, where each would go,
and a suggested explicit declaration. Views are never instantiated and
properties or ``state()`` factories are never evaluated; the output contains
names, builtin type names and destinations only, never values.

Usage:
    python manage.py djust_exposure_inventory                  # all user LiveViews
    python manage.py djust_exposure_inventory --app shop       # one app label
    python manage.py djust_exposure_inventory --view shop.views.CartView --json
"""

import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils.module_loading import import_string


class Command(BaseCommand):
    help = (
        "Values-redacted inventory of the names legacy exposure infers per LiveView "
        "and where each goes (ADR-038 migration aid)"
    )
    # A read-only report; system-check output would interleave with --json.
    requires_system_checks: list[str] = []

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--app", default=None, help="Only views whose module starts with this app label"
        )
        parser.add_argument(
            "--view",
            action="append",
            default=[],
            help="Dotted path of a LiveView class to inventory (repeatable); skips discovery",
        )
        parser.add_argument("--json", action="store_true", help="Emit JSON instead of text")

    def handle(self, *args: Any, **options: Any) -> None:
        from djust.live_view import LiveView
        from djust.management._exposure_inventory import (
            DESTINATIONS,
            discover_views,
            format_text,
            inventory_view,
        )

        classes: list[type] = []
        for path in options["view"]:
            try:
                cls = import_string(path)
            except ImportError as exc:
                raise CommandError(f"Cannot import {path}") from exc
            if not (isinstance(cls, type) and issubclass(cls, LiveView)):
                raise CommandError(f"{path} is not a LiveView class")
            classes.append(cls)
        if not classes:
            classes = discover_views(options["app"])

        views = [inventory_view(cls) for cls in classes]
        if options["json"]:
            self.stdout.write(json.dumps({"destinations": DESTINATIONS, "views": views}, indent=2))
        else:
            self.stdout.write(format_text(views))
