#!/usr/bin/env python3
"""
Lint docs/**/*.md for stale .md cross-references.

Catches the failure mode where a doc references a sibling .md file by
relative path that no longer exists (the file was moved, renamed, or
deleted without updating the referrer). docs.djust.org's link_check.py
surfaces these too — but only for the rendered subset; this catches the
internal refs that don't make it into the rendered site.

Usage:
    python3 scripts/docs-lint.py
    make docs-lint

Exit code:
    0 — no stale refs detected
    1 — stale refs found; run with --verbose for the list

Filed as part of #1075 / paired with `make roadmap-lint` (#142). Same
shape as scripts/roadmap-lint.py — pre-push hook prevents regression.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


DOCS_ROOT = Path("docs")

# Skip the rendered site dir — it has its own link checker.
SKIP_DIRS = {"website"}

# Standard markdown link with .md target. We deliberately ignore # fragment
# correctness because GitHub auto-generates anchor IDs from headings and
# there's no clean way to validate that without rendering the markdown.
MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)#]+\.md)(#[^)]*)?\)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="list every stale ref (default: count + first 10)"
    )
    parser.add_argument(
        "--changed-only", action="store_true",
        help="only check files modified vs origin/main (pre-push mode)"
    )
    args = parser.parse_args()

    if not DOCS_ROOT.exists():
        print(f"ERROR: {DOCS_ROOT}/ not found (run from project root)", file=sys.stderr)
        return 2

    # Collect MD files to check
    if args.changed_only:
        import subprocess

        try:
            # Use ``docs/`` pathspec + post-filter on .md suffix so depth-1
            # files like ``docs/README.md`` are caught — git's
            # ``docs/**/*.md`` glob silently skips them. (Stage 11 review
            # finding on PR #1083.)
            result = subprocess.run(
                ["git", "diff", "--name-only", "--diff-filter=ACMR",
                 "origin/main..HEAD", "--", "docs/"],
                capture_output=True, text=True, check=False,
            )
            files = [
                Path(p) for p in result.stdout.splitlines()
                if p.strip() and p.endswith(".md")
            ]
        except FileNotFoundError:
            files = []
    else:
        files = list(DOCS_ROOT.rglob("*.md"))

    files = [f for f in files if not any(skip in f.parts for skip in SKIP_DIRS)]

    stale: list[tuple[str, int, str]] = []  # (file, line_no, link)
    for md in files:
        try:
            text = md.read_text()
        except OSError:
            continue
        for line_no, line in enumerate(text.split("\n"), start=1):
            for m in MD_LINK.finditer(line):
                target = m.group(2)
                # Skip URLs and absolute paths
                if target.startswith("http") or target.startswith("/"):
                    continue
                try:
                    resolved = (md.parent / target).resolve()
                except (OSError, ValueError):
                    continue
                if not resolved.exists():
                    stale.append((str(md), line_no, m.group(0)))

    print(f"docs/ MD files scanned: {len(files)}")
    print(f"Stale references: {len(stale)}")

    if stale:
        if args.verbose:
            print()
            for src, line_no, link in stale:
                print(f"  {src}:{line_no}: {link}")
        else:
            print()
            for src, line_no, link in stale[:10]:
                print(f"  {src}:{line_no}: {link}")
            if len(stale) > 10:
                print(f"  ... ({len(stale) - 10} more — run with --verbose)")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
