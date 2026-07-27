#!/usr/bin/env python3
"""Report Action Tracker rows whose GitHub issue has already been closed (#2143).

Nothing closed a tracker row when its issue closed, so `RETRO.md`'s open count
drifted upward and every retro's Stage 5 had to re-derive the true state by
hand. Row 325 sat at `Open` against an issue closed a full milestone earlier;
it was found by chance, because that sweep only looks at rows it has reason to
suspect.

The count matters because retros quote it as a health signal. An open count
that silently includes resolved work is worse than no count.

**Report-only by default, and that is the design.** Flipping the status is the
easy half; the Notes column — *why* it closed, which PR did it — is the half
that makes the row useful later, and a bot cannot write it. `--fix` exists for
the mechanical flip when a human is going to fill in the reasons anyway.

Usage:
    python scripts/check-action-tracker.py           # report drift, exit 1 if any
    python scripts/check-action-tracker.py --quiet   # exit code only
    python scripts/check-action-tracker.py --json    # machine-readable
    python scripts/check-action-tracker.py --fix     # flip Status to Closed
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RETRO = ROOT / "RETRO.md"

# `| 325 | Action text | PR #2093 | #2082 | Open | notes |`
ROW_RE = re.compile(r"^\|\s*(\d+)\s*\|(.*?)\|(.*?)\|(.*?)\|\s*([A-Za-z-]+)\s*\|(.*?)\|\s*$")
# The GitHub column may hold "#2082", "—", or several refs.
ISSUE_RE = re.compile(r"#(\d+)")


class Row:
    __slots__ = ("num", "action", "source", "issues", "status", "notes", "lineno", "raw")

    def __init__(self, m: re.Match, lineno: int, raw: str):
        self.num = int(m.group(1))
        self.action = m.group(2).strip()
        self.source = m.group(3).strip()
        self.issues = [int(n) for n in ISSUE_RE.findall(m.group(4))]
        self.status = m.group(5).strip()
        self.notes = m.group(6).strip()
        self.lineno = lineno
        self.raw = raw


def parse_rows(text: str) -> list[Row]:
    rows = []
    for i, line in enumerate(text.splitlines(), start=1):
        m = ROW_RE.match(line)
        if m:
            rows.append(Row(m, i, line))
    return rows


def issue_states() -> tuple[dict[int, str], dict[int, int]]:
    """Every issue's state AND its closing PR, in ONE call.

    Per-row `gh issue view` would be ~70 round trips; the repo's own canon says
    not to poll `gh` in a loop when a single query answers the question.

    The closing PR is fetched because it IS the reason the row can be closed.
    Without it `--fix` would only flip a status and drop the half of the row
    that makes it useful later.
    """
    out = subprocess.run(
        [
            "gh",
            "issue",
            "list",
            "--state",
            "all",
            "--limit",
            "5000",
            "--json",
            "number,state,closedByPullRequestsReferences",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        raise RuntimeError(f"gh issue list failed: {out.stderr.strip()}")
    data = json.loads(out.stdout)
    states = {i["number"]: i["state"] for i in data}
    closers = {}
    for i in data:
        refs = i.get("closedByPullRequestsReferences") or []
        if refs:
            closers[i["number"]] = refs[0]["number"]
    return states, closers


def find_drift(rows: list[Row], states: dict[int, str]) -> tuple[list[dict], list[dict]]:
    """Rows that disagree with GitHub, in both directions."""
    (
        stale_open,
        stale_closed,
    ) = [], []
    for r in rows:
        known = {n: states[n] for n in r.issues if n in states}
        if not known:
            # Nothing to compare against: the row cites no issue (a one-time
            # task, or an OUT-OF-REPO row), or cites one GitHub does not
            # return (transferred, deleted, or in another repo). Neither is
            # drift, and guessing either way would be worse than the drift.
            #
            # A separate `if not r.issues` fast path used to sit above this.
            # It was removed rather than kept: it made the two guards mask each
            # other, so deleting either one alone left the suite green.
            continue
        if r.status == "Open" and all(s == "CLOSED" for s in known.values()):
            stale_open.append(
                {
                    "row": r.num,
                    "line": r.lineno,
                    "issues": r.issues,
                    "action": r.action[:70],
                    "notes": r.notes,
                }
            )
        elif r.status == "Closed" and any(s == "OPEN" for s in known.values()):
            stale_closed.append(
                {"row": r.num, "line": r.lineno, "issues": r.issues, "action": r.action[:70]}
            )
    return stale_open, stale_closed


# The Notes column renders an em-dash when there is nothing to say. Treating
# that as prose would make `--fix` refuse to write the reason it just looked up.
_EMPTY_NOTES = {"", "—", "-", "–", "n/a", "N/A"}


def apply_fix(text: str, stale_open: list[dict], closers: dict[int, int]) -> str:
    """Flip Status to Closed and supply the closing PR as the reason.

    Edits parsed CELLS rather than regex-patching the line: the Notes column
    contains free prose, including pipes inside code spans in some rows, and a
    positional edit is the only way to be sure which cell is being changed.
    """
    by_line = {d["line"]: d for d in stale_open}
    out = []
    for lineno, line in enumerate(text.splitlines(keepends=True), start=1):
        d = by_line.get(lineno)
        if d is None:
            out.append(line)
            continue
        eol = "\n" if line.endswith("\n") else ""
        parts = line.rstrip("\n").split("|")
        # ['', num, action, source, github, status, notes, ''] — anything else
        # is a shape this function does not understand, so leave it alone.
        if len(parts) < 8 or parts[5].strip() != "Open":
            out.append(line)
            continue
        parts[5] = " Closed "
        prs = sorted({closers[n] for n in d["issues"] if n in closers})
        if prs and parts[6].strip() in _EMPTY_NOTES:
            # Never overwrite a reason a human already wrote.
            parts[6] = " Resolved by " + ", ".join(f"PR #{n}" for n in prs) + " "
        out.append("|".join(parts) + eol)
    return "".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fix", action="store_true", help="flip drifted rows to Closed")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    text = RETRO.read_text()
    rows = parse_rows(text)
    if not rows:
        print("No Action Tracker rows found in RETRO.md — has the table format changed?")
        return 2

    try:
        states, closers = issue_states()
    except (RuntimeError, FileNotFoundError) as exc:
        # No network / no gh is not a failure of the tracker. Say so plainly
        # rather than reporting zero drift, which would read as "all clean".
        print(f"Could not query GitHub, so nothing was checked: {exc}", file=sys.stderr)
        return 2

    stale_open, stale_closed = find_drift(rows, states)

    if args.as_json:
        print(
            json.dumps(
                {
                    "rows": len(rows),
                    "open_rows": sum(1 for r in rows if r.status == "Open"),
                    "stale_open": stale_open,
                    "stale_closed": stale_closed,
                },
                indent=2,
            )
        )
    elif not args.quiet:
        open_rows = sum(1 for r in rows if r.status == "Open")
        print(f"Action Tracker: {len(rows)} rows, {open_rows} marked Open.")
        if stale_open:
            print(f"\n{len(stale_open)} row(s) read Open but their issue is CLOSED:")
            for d in stale_open:
                refs = ", ".join(f"#{n}" for n in d["issues"])
                print(f"  row {d['row']:>3} (RETRO.md:{d['line']}) {refs}  {d['action']}")
            print(
                "\nThese need a closing REASON in the Notes column, which is why this\n"
                "reports rather than edits. `--fix` flips the status if you are about\n"
                "to write the reasons anyway."
            )
        if stale_closed:
            print(f"\n{len(stale_closed)} row(s) read Closed but their issue is OPEN:")
            for d in stale_closed:
                refs = ", ".join(f"#{n}" for n in d["issues"])
                print(f"  row {d['row']:>3} (RETRO.md:{d['line']}) {refs}  {d['action']}")
        if not stale_open and not stale_closed:
            print("No drift: every row with an issue agrees with GitHub.")

    if args.fix and stale_open:
        RETRO.write_text(apply_fix(text, stale_open, closers))
        print(f"\nFlipped {len(stale_open)} row(s) to Closed. Fill in the Notes column.")
        return 0

    return 1 if (stale_open or stale_closed) else 0


if __name__ == "__main__":
    sys.exit(main())
