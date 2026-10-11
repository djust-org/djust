"""Discover installed capabilities, bundled UI components and framework guidance."""

from typing import Any
from django.core.management.base import BaseCommand, CommandParser


class Command(BaseCommand):
    help = "AI capability discovery: inventory, suggest components for an intent, manifest"
    requires_system_checks: list[str] = []

    def add_arguments(self, parser: CommandParser) -> None:
        from djust.ai_discovery.runner import add_subcommands

        add_subcommands(parser)

    def handle(self, *args: Any, **options: Any) -> None:
        from djust.ai_discovery.runner import run

        run(options, self.stdout, project=True)
