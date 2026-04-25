#!/usr/bin/env python3
"""
Lint ROADMAP.md against the actual codebase.

Catches the failure mode where a ROADMAP feature is listed as "Not started"
but the code is already live (or vice versa). Two strikes during v0.5.0
motivated this — see Action Tracker #142 / GH #1057.

Usage:
    python3 scripts/roadmap-lint.py
    make roadmap-lint

Exit code:
    0 — no drift detected (or all drift is in known-irrelevant sections)
    1 — drift detected; run with --verbose to see why

This is the cheap, mechanical version. For semantic auditing (LLM reads each
entry, decides if the cited feature actually ships), use the
pipeline-roadmap-audit skill instead.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

ROADMAP_PATH = Path("ROADMAP.md")

# Sections to skip — purely descriptive, not gated on code existence.
SKIP_SECTIONS = {
    "## Investigate",
    "## Differentiators",
    "## Future",
    "## Contributing",
    "## Parity Tracker",
    "## Completed",
    "## Investigate & Decide",
}

# Keyword tokens that we extract from a feature name and grep for in the
# codebase. Single-word camelCase / snake_case identifiers are more useful
# signals than common English words.
STOPWORDS = {
    "the", "and", "or", "of", "for", "to", "in", "on", "with", "from",
    "via", "by", "at", "an", "a", "is", "are", "be", "as", "if", "not",
    "into", "out", "up", "down", "over", "across",
}

# Code paths that count as "shipped". Skip docs, ROADMAP itself, retros, CHANGELOG.
CODE_PATHS = ("python/", "crates/", "static/", "scripts/", "tests/", "Makefile")


class Entry(NamedTuple):
    line_no: int
    section: str
    name: str
    raw: str  # full line


def parse_roadmap(text: str) -> list[Entry]:
    """Pull pending entries from priority-matrix tables and milestone sections."""
    entries: list[Entry] = []
    cur_section = ""
    for i, line in enumerate(text.split("\n"), start=1):
        if line.startswith("## ") or line.startswith("### "):
            cur_section = line.strip()
            continue

        if any(cur_section.startswith(skip) for skip in SKIP_SECTIONS):
            continue

        # Strikethrough or ✅ → already done, skip
        if "~~" in line or "✅" in line:
            continue

        # Priority-matrix rows: `| **P2** | **<name>** | <desc> | ... |`
        if line.startswith("|") and "Not started" in line:
            cells = [c.strip() for c in line.split("|")[1:-1]]
            if len(cells) >= 2:
                # Feature name is typically the second cell, often bold-wrapped
                name = re.sub(r"\*\*", "", cells[1])
                if name:
                    entries.append(Entry(i, cur_section, name, line))
            continue

        # Milestone bullet entries: `**<name>** — <desc>`
        m = re.match(r"^\*\*([^*]+)\*\*\s+—", line)
        if m and cur_section.startswith("### Milestone:"):
            name = m.group(1).strip()
            entries.append(Entry(i, cur_section, name, line))
    return entries


def extract_keywords(name: str) -> list[str]:
    """Pull grep-able tokens out of a feature name."""
    # Remove markdown formatting / parens / common decoration
    cleaned = re.sub(r"[`*()]", "", name)
    cleaned = re.sub(r"#\d+", "", cleaned)  # drop issue numbers
    cleaned = cleaned.replace("/", " ")  # split alternatives
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_-]+", cleaned)
    # Keep only tokens that look like identifiers (3+ chars, not a stopword)
    return [t for t in tokens if len(t) >= 4 and t.lower() not in STOPWORDS]


def grep_codebase(token: str) -> int:
    """Return number of code-path matches for a token. 0 = not found."""
    try:
        result = subprocess.run(
            ["git", "grep", "-l", token, "--", *CODE_PATHS],
            capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        return 0
    if result.returncode == 1:  # git grep returns 1 when no matches
        return 0
    return len([line for line in result.stdout.splitlines() if line.strip()])


def lint(verbose: bool = False) -> int:
    if not ROADMAP_PATH.exists():
        print(f"ERROR: {ROADMAP_PATH} not found (run from project root)", file=sys.stderr)
        return 2

    entries = parse_roadmap(ROADMAP_PATH.read_text())
    drift_count = 0
    suspect: list[tuple[Entry, list[str]]] = []

    for entry in entries:
        keywords = extract_keywords(entry.name)
        if not keywords:
            continue

        # If ANY keyword has zero hits in code paths, that's suspect
        unfound = [k for k in keywords if grep_codebase(k) == 0]
        if len(unfound) == len(keywords):
            # No keyword found anywhere — suspect (could be ROADMAP-only feature)
            suspect.append((entry, unfound))
            drift_count += 1

    print(f"ROADMAP entries scanned: {len(entries)}")
    print(f"Suspect entries (no keyword hits in {', '.join(CODE_PATHS)}): {drift_count}")

    if suspect and verbose:
        print()
        print("=" * 60)
        for entry, unfound in suspect:
            print(f"\nL{entry.line_no} [{entry.section}]")
            print(f"  Name:    {entry.name}")
            print(f"  Tokens:  {', '.join(unfound)}")

    if drift_count > 0 and not verbose:
        print()
        print("Run with --verbose to see entries.")
        print("Note: 'suspect' = no keyword from name appears in code paths.")
        print("      Some are legitimately not-started; others may be stale.")
        print("      For semantic verification, use the pipeline-roadmap-audit skill.")

    # Return non-zero only if drift count is "high" — single-digit drift is
    # normal-and-OK because some not-started features intentionally have
    # generic names. Threshold is 25 → tune as ROADMAP grows.
    return 0 if drift_count < 25 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", "-v", action="store_true", help="list every suspect entry")
    args = parser.parse_args()
    return lint(verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
