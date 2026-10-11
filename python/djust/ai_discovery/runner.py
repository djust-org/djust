"""Shared argparse wiring and output rendering for CLI and management command."""

import argparse
from collections.abc import Mapping
import json
import sys
from typing import Any, TextIO


def add_subcommands(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(
        dest="subcommand", required=True, metavar="{inventory,suggest,manifest}"
    )
    inv = sub.add_parser("inventory", help="List installed project capabilities")
    inv.add_argument("--json", action="store_true", dest="json_output", help="Print versioned JSON")
    sug = sub.add_parser("suggest", help="Rank UI components for an intent")
    sug.add_argument("intent")
    sug.add_argument("--json", action="store_true", dest="json_output", help="Print versioned JSON")
    sug.add_argument("--limit", type=int, default=5)
    sub.add_parser("manifest", help="Print a capabilities document")


def render_suggest(intent: str, limit: int, *, as_json: bool) -> str:
    from djust.ai_discovery.catalog import CATALOG_VERSION, search

    results = search(intent, limit)
    if as_json:
        return json.dumps(
            {
                "version": CATALOG_VERSION,
                "kind": "suggest",
                "intent": intent,
                "results": [e.to_dict() | {"score": score} for e, score in results],
            },
            indent=2,
        )
    if not results:
        return f'No component matched "{intent}". Browse the catalog: python manage.py djust_ai inventory'
    lines = []
    for index, (entry, _) in enumerate(results, 1):
        lines.append(f"{index}. {entry.name} ({entry.category_label}) — {entry.purpose}")
        lines.append("   " + entry.load)
        lines.extend("   " + line for line in entry.snippet.splitlines())
        required = entry.required_props()
        if required:
            lines.append("   required: " + ", ".join(required))
        elif entry.props_source == "snippet" and entry.props:
            lines.append("   props: " + ", ".join(p.name for p in entry.props) + " (from example)")
        if entry.related:
            lines.append("   related: " + ", ".join(entry.related))
    return "\n".join(lines)


def run(options: Mapping[str, Any], stdout: TextIO, *, project: bool) -> int:
    from django.core.management.base import CommandError

    subcommand = options["subcommand"]
    if subcommand == "inventory":
        if not project:
            print(
                "djust ai inventory needs a Django project: run it where manage.py is, or set "
                "DJANGO_SETTINGS_MODULE. suggest and manifest work anywhere.",
                file=sys.stderr,
            )
            return 2
        from djust.ai_discovery.inventory import build_inventory, render_inventory

        data = build_inventory()
        content = (
            json.dumps(data, indent=2) if options.get("json_output") else render_inventory(data)
        )
    elif subcommand == "suggest":
        try:
            content = render_suggest(
                options["intent"], options.get("limit", 5), as_json=bool(options.get("json_output"))
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
    else:
        from djust.ai_discovery.manifest import render_manifest

        content = render_manifest()
    stdout.write(content + "\n")
    return 0
