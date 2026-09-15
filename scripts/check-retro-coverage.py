#!/usr/bin/env python3
"""Milestone retro coverage — every completed drain bucket has a RETRO.md entry.

Filed as #2848 (v1.2.0-6 retro). The pipeline has a per-PR retro artifact gate
(`gate_retro_artifact`: a merged PR must carry a retrospective comment) and a
per-PR bypass audit (`scripts/audit-pipeline-bypass.py`), but nothing ties a
*completed milestone* to a *milestone* retro entry. A bucket can therefore reach
completion with every issue closed and no `RETRO.md` entry, and nothing notices.

That is not hypothetical: 14 buckets drifted that way, and #2140 was a *previous*
backfill for the same reason — so the gap recurs after being fixed by hand.

Completion signal: the ROADMAP's own matrix rows. A bucket counts as complete
when it has at least one row and EVERY row carries a completion marker
(strikethrough, or the check emoji). Heading-level markers are deliberately not
used — none of the drifted buckets had one.

KNOWN LIMITATION, measured on the case that motivated this check: it can only see
buckets whose rows were struck through. Against the real drift it reported 6 of
14; the other 8 were completed without striking their rows, so no static signal
records them as done. That is a second-order finding rather than a bug — a bucket
whose completion nobody recorded is invisible to every reader, human or script —
and striking the rows is part of marking a bucket done. With network access the
authoritative signal is "every issue in the bucket is closed" (one call:
`gh issue list --state all --json number,state,closedByPullRequestsReferences`).

Exit 0 clean, 1 on a missing entry. No network, no git, no gh — CI-fast.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HEADING = re.compile(r"^#{2,3}\s+(v\d[^\s—]*)\s*—")
MATRIX_ROW = re.compile(r"^\|.*#\d{3,4}")
DONE_MARKERS = ("~~", "✅")


def completed_buckets(roadmap_path: Path) -> list[str]:
    """Bucket names whose matrix rows all carry a completion marker."""
    buckets: list[str] = []
    name: str | None = None
    rows = done = 0

    def flush() -> None:
        if name and rows and rows == done:
            buckets.append(name)

    for line in roadmap_path.read_text(encoding="utf-8").splitlines():
        m = HEADING.match(line)
        if m:
            flush()
            name, rows, done = m.group(1), 0, 0
            continue
        if name and MATRIX_ROW.match(line):
            rows += 1
            if any(marker in line for marker in DONE_MARKERS):
                done += 1
    flush()
    return buckets


def retro_entries(retro_path: Path) -> set[str]:
    names = set()
    for line in retro_path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^##\s+(v\d[^\s—]*)", line)
        if m:
            names.add(m.group(1))
    return names


def main(argv: list[str]) -> int:
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0
    # Partition flags from positional paths FIRST: a flag must never be mistaken
    # for a path just because of where it sits on the command line.
    flags = [a for a in argv if a.startswith("-")]
    paths = [a for a in argv if not a.startswith("-")]
    unknown = [f for f in flags if f != "--backfill-needed"]
    if unknown:
        print(f"check-retro-coverage: unknown flag(s) {unknown}", file=sys.stderr)
        return 2
    roadmap = Path(paths[0]) if len(paths) > 0 else Path("ROADMAP.md")
    retro = Path(paths[1]) if len(paths) > 1 else Path("RETRO.md")
    for p in (roadmap, retro):
        if not p.is_file():
            print(f"check-retro-coverage: {p} not found", file=sys.stderr)
            return 1

    buckets = completed_buckets(roadmap)
    have = retro_entries(retro)
    missing = [b for b in buckets if b not in have]

    if "--backfill-needed" in flags:
        for b in missing:
            print(b)
        return 1 if missing else 0

    if missing:
        print(f"check-retro-coverage: {len(missing)} of {len(buckets)} completed "
              f"bucket(s) have no {retro} entry:")
        for b in missing:
            print(f"  - {b}")
        print("Run /pipeline-retro for each (its Backfill Mode covers a bucket whose "
              "per-PR retros have decayed), then re-run this check.")
        return 1
    print(f"check-retro-coverage: all {len(buckets)} completed bucket(s) have a {retro} entry")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
