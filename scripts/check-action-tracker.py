#!/usr/bin/env python3
"""Report RETRO.md rows whose GitHub issue has already been closed (#2143, #2200).

Two structures, one problem. The Action Tracker TABLE has a `Status` column;
the per-milestone **Open Items** are `- [ ]` checklists. Both drift the same
way, so both are checked here rather than by two tools with two fetches and
two ideas of what "closed" means.

Nothing closed a tracker row when its issue closed, so `RETRO.md`'s open count
drifted upward and every retro's Stage 5 had to re-derive the true state by
hand. Row 325 sat at `Open` against an issue closed a full milestone earlier;
it was found by chance, because that sweep only looks at rows it has reason to
suspect.

The Open Items measured worse: **164 of 197** unchecked boxes referenced issues
that had all closed. Including, at `RETRO.md:533`, the row asking for exactly
this automation — the row about closing rows was itself a stale row.

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
    python scripts/check-action-tracker.py --fix     # flip Status/checkbox
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

# `- [ ] Some action — Action Tracker #330 (GitHub #2143)`
ITEM_RE = re.compile(r"^(\s*)- \[( |x)\] (.*)$")
# RETRO.md carries TWO numbering schemes on one line, and they COLLIDE: an
# "Action Tracker #329" is a different thing from GitHub issue #329, which also
# exists. 38 of the 94 unchecked items naming a GitHub issue also carry a
# tracker number that resolves to a real, unrelated issue.
#
# A first pass took the first `#NNN` on the line and reported #2142 as closed by
# PR #362 — issue #329's closer. A plausible, verifiable-looking, WRONG citation
# about to be written into a document (#1197). So when a line says
# "GitHub #NNNN", that is authoritative and every other number on the line is
# ignored: for the DECISION as well as the citation, since ANDing over a
# colliding tracker number can mask an open issue as easily as invent a closed
# one.
GITHUB_REF_RE = re.compile(r"GitHub #(\d+)")
# What `--fix` appends, so a second run does not annotate the same item twice.
ITEM_MARKER = "— **closed"
# Markers a HUMAN writes when they have already judged an item. An annotated
# line is left alone in BOTH directions, mirroring the table path's refusal to
# overwrite a Notes cell someone filled in.
#
# The ticked direction needs this more than the unchecked one. RETRO.md:682
# reads `bug-capture iter B (#1562) — **resolved in v1.1.0-11 (PR #2083)**;
# iter C (#1561) still deferred` — correct, precise, and flagged as drift by a
# naive rule, because it cites an issue that is legitimately still open. One
# permanent false alarm is enough to make a check worth ignoring.
HUMAN_MARKERS = ("**resolved", "**closed", "**superseded", "**deferred", "**wontfix")


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


class Item:
    """One `- [ ]` / `- [x]` Open-Items checkbox."""

    __slots__ = ("indent", "checked", "body", "issues", "lineno", "raw")

    def __init__(self, m: re.Match, lineno: int, raw: str):
        self.indent = m.group(1)
        self.checked = m.group(2) == "x"
        self.body = m.group(3)
        github = GITHUB_REF_RE.findall(self.body)
        refs = github or ISSUE_RE.findall(self.body)
        self.issues = [int(n) for n in refs]
        self.lineno = lineno
        self.raw = raw


def parse_items(text: str) -> list[Item]:
    items = []
    for i, line in enumerate(text.splitlines(), start=1):
        m = ITEM_RE.match(line)
        if m:
            items.append(Item(m, i, line))
    return items


def find_item_drift(items: list[Item], states: dict[int, str]) -> tuple[list[dict], list[dict]]:
    """Checkboxes that disagree with GitHub, in both directions."""
    stale_open, stale_closed = [], []
    for it in items:
        if any(mark in it.body for mark in HUMAN_MARKERS):
            continue
        known = {n: states[n] for n in it.issues if n in states}
        if not known:
            # Same reasoning as the table path: an item citing no issue, or one
            # GitHub does not return, is not drift. 28 of the unchecked items
            # are like this — carried OUT-OF-REPO work, or prose headers — and
            # guessing either way is worse than leaving them.
            continue
        if not it.checked and all(s == "CLOSED" for s in known.values()):
            stale_open.append({"line": it.lineno, "issues": it.issues, "action": it.body[:70]})
        elif it.checked and any(s == "OPEN" for s in known.values()):
            stale_closed.append({"line": it.lineno, "issues": it.issues, "action": it.body[:70]})
    return stale_open, stale_closed


def apply_item_fix(text: str, stale_open: list[dict], closers: dict[int, int]) -> str:
    """Tick the box and append the closing PR as the reason.

    Appends rather than rewrites, for the same reason the table path edits
    parsed cells: item bodies are free prose, several already carry their own
    em-dash clause, and inserting into the middle of one would be guessing.
    """
    by_line = {d["line"]: d for d in stale_open}
    out = []
    for lineno, line in enumerate(text.splitlines(keepends=True), start=1):
        d = by_line.get(lineno)
        if d is None:
            out.append(line)
            continue
        m = ITEM_RE.match(line.rstrip("\n"))
        if m is None or m.group(2) != " ":
            out.append(line)
            continue
        eol = "\n" if line.endswith("\n") else ""
        indent, body = m.group(1), m.group(3)
        # ALL distinct closers, not a chosen one. An item citing two closed
        # issues has two equally defensible "primary" refs, and a gate-off
        # mutation swapping first for last survived the suite precisely
        # because the choice is arbitrary. Citing every closer removes the
        # choice instead of testing a guess, and matches the table path, which
        # already writes `Resolved by PR #a, PR #b`.
        prs = sorted({closers[n] for n in d["issues"] if n in closers})
        if prs:
            note = f" {ITEM_MARKER} ({', '.join(f'PR #{n}' for n in prs)})**"
        else:
            note = f" {ITEM_MARKER}**"
        new = f"{indent}- [x] {body}{note}{eol}"

        # Post-condition: the ONLY change is the checkbox and the appended
        # note. A boundary mis-parse must fail loudly, not rewrite prose.
        restored = new.rstrip("\n")[: len(line.rstrip("\n"))].replace("- [x] ", "- [ ] ", 1)
        if restored != line.rstrip("\n"):
            raise AssertionError(f"RETRO.md:{lineno}: item body would be mutated")
        out.append(new)
    return "".join(out)


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
    items = parse_items(text)
    item_open, item_closed = find_item_drift(items, states)

    if args.as_json:
        print(
            json.dumps(
                {
                    "rows": len(rows),
                    "open_rows": sum(1 for r in rows if r.status == "Open"),
                    "stale_open": stale_open,
                    "stale_closed": stale_closed,
                    "items": len(items),
                    "unchecked_items": sum(1 for i in items if not i.checked),
                    "item_stale_open": item_open,
                    "item_stale_closed": item_closed,
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
        unchecked = sum(1 for i in items if not i.checked)
        print(f"\nOpen Items: {len(items)} checkboxes, {unchecked} unchecked.")
        if item_open:
            print(f"\n{len(item_open)} unchecked item(s) whose issues are ALL closed:")
            for d in item_open[:20]:
                refs = ", ".join(f"#{n}" for n in d["issues"])
                print(f"  RETRO.md:{d['line']:>5} {refs}  {d['action']}")
            if len(item_open) > 20:
                print(f"  ... and {len(item_open) - 20} more")
        if item_closed:
            print(f"\n{len(item_closed)} ticked item(s) whose issue is still OPEN:")
            for d in item_closed[:20]:
                refs = ", ".join(f"#{n}" for n in d["issues"])
                print(f"  RETRO.md:{d['line']:>5} {refs}  {d['action']}")
        if not stale_open and not stale_closed and not item_open and not item_closed:
            print("\nNo drift: every row and item with an issue agrees with GitHub.")

    if args.fix and (stale_open or item_open):
        fixed = text
        if stale_open:
            fixed = apply_fix(fixed, stale_open, closers)
        if item_open:
            # Line numbers were computed against the ORIGINAL text, and the
            # table fix rewrites lines in place without adding or removing any,
            # so they stay valid. Asserted rather than assumed: an off-by-one
            # here would tick the wrong box.
            if len(fixed.splitlines()) != len(text.splitlines()):
                raise AssertionError("table fix changed the line count; item line numbers stale")
            fixed = apply_item_fix(fixed, item_open, closers)
        RETRO.write_text(fixed)
        if stale_open:
            print(f"\nFlipped {len(stale_open)} row(s) to Closed. Fill in the Notes column.")
        if item_open:
            print(f"Ticked {len(item_open)} Open Item(s) with their closing PR.")
        return 0

    return 1 if (stale_open or stale_closed or item_open or item_closed) else 0


if __name__ == "__main__":
    sys.exit(main())
