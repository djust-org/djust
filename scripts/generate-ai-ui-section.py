"""Generate the UI building blocks sections in AI-facing docs (ADR-043 D4).

Default: fail if stale. Use --write to regenerate, --print to print the block,
or repeated --doc PATH arguments to check specific documents.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_DOCS = [
    REPO / "docs" / name for name in ("llms.txt", "llms-full.txt", "BEST_PRACTICES_AI.md")
]
BEGIN = "<!-- BEGIN GENERATED: UI building blocks (scripts/generate-ai-ui-section.py) -->"
END = "<!-- END GENERATED: UI building blocks -->"


def _setup_django() -> None:
    from django.conf import settings

    if not settings.configured:
        settings.configure(SECRET_KEY="ai-ui-section", INSTALLED_APPS=[])
        import django

        django.setup()


def render_block() -> str:
    _setup_django()
    from djust.ai_discovery.manifest import render_ui_building_blocks

    return BEGIN + "\n\n" + render_ui_building_blocks() + "\n" + END + "\n"


def current_block(text: str) -> str:
    if text.count(BEGIN) > 1 or text.count(END) > 1:
        raise ValueError("expected exactly one BEGIN and END generated block marker")
    start, end = text.find(BEGIN), text.find(END)
    if start == -1 or end == -1 or end < start:
        raise ValueError("generated block markers not found")
    return text[start : end + len(END)] + "\n"


def splice(text: str, block: str) -> str:
    current_block(text)
    start, end = text.find(BEGIN), text.find(END)
    return text[:start] + block.rstrip("\n") + text[end + len(END) :]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--doc", type=Path, action="append")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--print", action="store_true")
    args = parser.parse_args(argv)
    try:
        block = render_block()
    except ValueError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2
    if args.print:
        sys.stdout.write(block)
        return 0
    status = 0
    for doc in args.doc or DEFAULT_DOCS:
        text = doc.read_text(encoding="utf-8")
        try:
            old = current_block(text)
        except ValueError as exc:
            print("ERROR: %s: %s" % (doc, exc), file=sys.stderr)
            status = max(status, 2)
            continue
        if old == block:
            print("%s: unchanged" % os.path.relpath(doc, REPO))
        elif args.write:
            doc.write_text(splice(text, block), encoding="utf-8")
            print("%s: generated block rewritten" % os.path.relpath(doc, REPO))
        else:
            print(
                "ERROR: %s: the UI building blocks section is stale; run `make ai-ui-section`"
                % os.path.relpath(doc, REPO),
                file=sys.stderr,
            )
            status = max(status, 1)
    return status


if __name__ == "__main__":
    sys.exit(main())
