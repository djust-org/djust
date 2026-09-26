"""Print or write the app's CycloneDX SBOM of declared browser assets (ADR-040)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandParser


class Command(BaseCommand):
    help = "Print the CycloneDX SBOM of every declared third-party browser asset."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("-o", "--output", type=Path, help="write here instead of stdout")

    def handle(self, *args: Any, **options: Any) -> None:
        from djust.assets.sbom import app_document, dumps, write_app_sbom

        if options["output"]:
            write_app_sbom(options["output"])
        else:
            self.stdout.write(dumps(app_document()), ending="")
