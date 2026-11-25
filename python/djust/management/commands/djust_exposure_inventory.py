"""List the names djust 1.3's legacy exposure exported per LiveView, values redacted.

ADR-038 *Compatibility and rollout*: "Provide a values-redacted inventory of
inferred names and their destinations before migration." This is the 2.0
upgrade checker for 1.3 code: for each LiveView class it reports the names
1.3's legacy policy put into template context and ``get_state()``, and a
suggested explicit declaration. Views are never instantiated and
properties or ``state()`` factories are never evaluated; the output contains
names, builtin type names and destinations only, never values.

With ``--fix`` (#4209) it makes the mechanical upgrade changes instead: deletes
``exposure_policy = "legacy"``, supplies the names each template reads from
``get_context_data()``, annotates unannotated handler parameters as ``Any`` and
renames ``data-*`` event arguments to ``dj-value-*`` in templates, then lists what
needs a person (``djust.management._exposure_fix``). ``--dry-run`` prints the diff
and writes nothing; only files under ``--root`` are changed.

2.0 refuses 1.3 code that states ``exposure_policy = "legacy"``,
``@event_handler(parameter_policy="legacy")`` or ``cancel_async_on_disconnect =
True`` when the class or handler is created (#4199). This command imports such code
under ``djust._removed_settings.upgrade_inspection()``, which lets the classes be
created (never constructed) so they can be read and rewritten. A module imported
while Django starts (by an ``AppConfig.ready()``, a ``models.py`` or an
``admin.py`` that admin autodiscovery imports) is imported before the command
runs, so it is refused first: delete its policy line by hand, or move the import.
Order: first delete the removed config keys (``event_parameter_policy``,
``reauth_on_event``, ``reauth_server_turn_interval``), which are refused at
startup, then run ``--fix``.

Usage:
    python manage.py djust_exposure_inventory                  # all user LiveViews
    python manage.py djust_exposure_inventory --app shop       # one app label
    python manage.py djust_exposure_inventory --view shop.views.CartView --json
    python manage.py djust_exposure_inventory --fix --dry-run  # show the upgrade diff
"""

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils.module_loading import import_string


class Command(BaseCommand):
    help = (
        "Values-redacted inventory of the names djust 1.3's legacy exposure exported per "
        "LiveView and where each went (ADR-038 upgrade aid). With --fix, make the mechanical "
        "2.0 upgrade changes and list what needs a person (#4209)"
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
        parser.add_argument(
            "--fix",
            action="store_true",
            help="Rewrite what can be rewritten mechanically (#4209): delete "
            'exposure_policy = "legacy", supply the names each template reads from '
            "get_context_data(), annotate unannotated handler parameters as Any, and rename "
            "data-* event arguments to dj-value-* in templates",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="With --fix: print the changes as a unified diff and write nothing",
        )
        parser.add_argument(
            "--root",
            default=None,
            help="With --fix: only files under this directory are changed "
            "(default: settings.BASE_DIR, else the current directory)",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from djust.live_view import LiveView
        from djust.management._exposure_inventory import (
            DESTINATIONS,
            discover_views,
            format_text,
            inventory_view,
        )

        if (options["dry_run"] or options["root"]) and not options["fix"]:
            raise CommandError("--dry-run and --root need --fix")

        from djust._removed_settings import upgrade_inspection

        # #4199: 1.3 code is refused when its classes are created; only this
        # command may import it, to read and rewrite it (never to run it).
        with upgrade_inspection():
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

            if options["fix"]:
                self._fix(classes, options)
                return

            views = [inventory_view(cls) for cls in classes]
        if options["json"]:
            self.stdout.write(json.dumps({"destinations": DESTINATIONS, "views": views}, indent=2))
        else:
            self.stdout.write(format_text(views))

    def _fix(self, classes: list[type], options: dict[str, Any]) -> None:
        from django.conf import settings

        from djust.management._exposure_fix import fix_views, format_fix_report

        root = options["root"] or getattr(settings, "BASE_DIR", None) or Path.cwd()
        dry_run = bool(options["dry_run"])
        report = fix_views(
            classes,
            root=Path(root).resolve(),
            write=not dry_run,
            scoped=bool(options["view"]) or bool(options["app"]),
        )
        if options["json"]:
            self.stdout.write(json.dumps(report.as_dict(), indent=2))
        else:
            self.stdout.write(format_fix_report(report, dry_run), ending="")
