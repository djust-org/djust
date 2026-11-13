#!/usr/bin/env python3
"""Maintain the promotion ledger: tests that failed late run early (``tests/early_tier.py``).

    promote  --junit report.xml [...] | --failed-ids ids.txt
             A late run failed. Every failing test that is NOT already in the pre-merge tier
             joins the ledger; a failing entry has its green streak reset. The nightly
             ``main-health`` suite runs it when red.
    demote   --junit report.xml [...]
             A late run was green. Every entry that passed gains one green nightly; an unpinned
             entry leaves after ``DEMOTE_AFTER_GREEN`` of them. The nightly runs it when green.
    check    The ledger is within its budget and every entry names a test that exists.
    early-files
             The test files ``make test-early`` collects: ledger entries, ``pr_tier`` tests, and
             the pins the current change's path rules select.

Both ``promote`` and ``demote`` enforce the budget (``BUDGET_SECONDS`` of recorded duration):
over it, unpinned entries leave oldest-last-failure first; pinned entries alone over it exit 2.

``--state`` carries the green streaks between nightly runs without writing to the repository:
streaks for entries present in both the ledger and the state file come from the state file, and
the updated ledger is written back to it. The workflow keeps that file in the actions cache.

Exit status: 0 done, 1 ``check`` found a problem, 2 the pinned entries alone exceed the budget.
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import json
import os
import sys
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import early_tier  # noqa: E402

PASSED, FAILED, SKIPPED = "passed", "failed", "skipped"


class BudgetError(RuntimeError):
    """The pinned entries alone exceed the early-tier budget."""


@dataclass
class Result:
    status: str
    tier: str  # early / late / nightly / unknown


@dataclass
class Report:
    promoted: list[str] = field(default_factory=list)
    refreshed: list[str] = field(default_factory=list)
    already_early: list[str] = field(default_factory=list)
    rule_covered: dict[str, str] = field(default_factory=dict)
    nightly_only: list[str] = field(default_factory=list)
    demoted: list[str] = field(default_factory=list)
    evicted: list[str] = field(default_factory=list)
    credited: list[str] = field(default_factory=list)

    @property
    def membership_changed(self) -> bool:
        return bool(self.promoted or self.demoted or self.evicted)

    def markdown(self) -> str:
        lines: list[str] = []

        def section(title: str, rows: Iterable[str]) -> None:
            rows = list(rows)
            if rows:
                lines.append(f"**{title}** ({len(rows)})")
                lines.extend(f"- `{r}`" for r in rows)
                lines.append("")

        section("Promoted to the early tier", self.promoted)
        section("Failed again (green streak reset)", self.refreshed)
        section("Left the ledger: green long enough", self.demoted)
        section("Left the ledger: over the early-tier budget", self.evicted)
        section(
            "Not promoted: a path rule already runs it early for the change shape it guards. "
            "If the breaking change did not match that rule, extend the rule",
            (f"{t} (rule {r})" for t, r in sorted(self.rule_covered.items())),
        )
        section("Not promoted: nightly-only by declaration", self.nightly_only)
        section("Not promoted: already in the pre-merge tier", self.already_early)
        return "\n".join(lines).rstrip() + "\n" if lines else "No ledger change.\n"


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #


def parse_junit(paths: Iterable[Path]) -> dict[str, Result]:
    """Node id -> result, from junit files written with ``tests/early_tier.py`` active.

    A test case without a ``djust_nodeid`` property (a collection error) is skipped: there is no
    collected test to promote. A node id seen more than once (a call failure plus a teardown
    error) is failed if any record failed.
    """
    out: dict[str, Result] = {}
    for path in paths:
        root = ET.parse(path).getroot()
        for case in root.iter("testcase"):
            props = {
                p.get("name"): p.get("value", "")
                for p in case.iter("property")
                if p.get("name", "").startswith("djust_")
            }
            nodeid = props.get("djust_nodeid")
            if not nodeid:
                continue
            if case.find("failure") is not None or case.find("error") is not None:
                status = FAILED
            elif case.find("skipped") is not None:
                status = SKIPPED
            else:
                status = PASSED
            prev = out.get(nodeid)
            if prev is not None and FAILED in (prev.status, status):
                status = FAILED
            out[nodeid] = Result(status, props.get("djust_tier", "unknown"))
    return out


def parse_failed_ids(path: Path) -> dict[str, Result]:
    """Node ids, one per line (``#`` comments allowed); the tier is not known."""
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split(" - ", 1)[0].strip()
        if line and not line.startswith("#"):
            out[line] = Result(FAILED, "unknown")
    return out


def by_entry(results: dict[str, Result]) -> dict[str, str]:
    """Ledger key -> failed / passed / skipped, over every parametrization of the function."""
    agg: dict[str, str] = {}
    for nodeid, res in results.items():
        key = early_tier.entry_key(nodeid)
        prev = agg.get(key)
        if res.status == FAILED or prev == FAILED:
            agg[key] = FAILED
        elif res.status == PASSED or prev == PASSED:
            agg[key] = PASSED
        else:
            agg[key] = SKIPPED
    return agg


# --------------------------------------------------------------------------- #
# Operations (pure: they change the ledger document in place and report)
# --------------------------------------------------------------------------- #


def _entries(ledger: dict) -> list[dict]:
    return ledger.setdefault("entries", [])


def merge_state(ledger: dict, state: dict | None) -> None:
    """Take green streaks / last failures from the previous nightly's state for the same entry.

    The state is newer than the checked-in ledger (a patch someone applied was cut from it), so
    its streak wins, including a reset to 0. A state that is missing (the cache was evicted)
    falls back to the checked-in values: an entry then stays early longer, never shorter.
    """
    if not state:
        return
    known = {
        (e.get("test"), e.get("promoted")): e for e in state.get("entries", []) if e.get("test")
    }
    for entry in _entries(ledger):
        prev = known.get((entry.get("test"), entry.get("promoted")))
        if prev is None:
            continue
        entry["green_streak"] = int(prev.get("green_streak", 0))
        entry["last_failed"] = max(
            str(entry.get("last_failed", "")), str(prev.get("last_failed", ""))
        )


def _late(nodeid: str, res: Result, slow: set[str]) -> str:
    """The pre-merge tier of a failing test: the junit tier, else derived from durations."""
    if res.tier in ("early", "late", "nightly"):
        return res.tier
    return "late" if nodeid in slow else "early"


def promote(
    ledger: dict,
    results: dict[str, Result],
    *,
    date: str,
    reason: str,
    durations: dict[str, float],
    rule_targets: dict[str, str],
) -> Report:
    report = Report()
    entries = {e["test"]: e for e in _entries(ledger)}
    slow = early_tier_slow(durations)
    for nodeid, res in sorted(results.items()):
        if res.status != FAILED:
            continue
        key = early_tier.entry_key(nodeid)
        if key in entries:
            entry = entries[key]
            if key not in report.refreshed:
                entry["last_failed"] = date
                entry["green_streak"] = 0
                report.refreshed.append(key)
            continue
        tier = _late(nodeid, res, slow)
        if tier == "early":
            report.already_early.append(nodeid)
            continue
        if tier == "nightly":
            report.nightly_only.append(nodeid)
            continue
        rule = next(
            (r for p, r in rule_targets.items() if early_tier.matches_prefix(nodeid, p)), None
        )
        if rule is not None:
            report.rule_covered[key] = rule
            continue
        entry = {
            "test": key,
            "promoted": date,
            "last_failed": date,
            "reason": reason,
            "green_streak": 0,
            "pinned": False,
        }
        _entries(ledger).append(entry)
        entries[key] = entry
        report.promoted.append(key)
    report.evicted = enforce_budget(ledger, durations)
    return report


def demote(
    ledger: dict, results: dict[str, Result], *, date: str, durations: dict[str, float]
) -> Report:
    report = Report()
    outcome = by_entry(results)
    keep = []
    for entry in _entries(ledger):
        status = outcome.get(entry["test"])
        if status == FAILED:
            entry["green_streak"] = 0
            entry["last_failed"] = date
        elif status == PASSED:
            entry["green_streak"] = int(entry.get("green_streak", 0)) + 1
            report.credited.append(entry["test"])
        if (
            not entry.get("pinned")
            and int(entry.get("green_streak", 0)) >= early_tier.DEMOTE_AFTER_GREEN
        ):
            report.demoted.append(entry["test"])
            continue
        keep.append(entry)
    ledger["entries"] = keep
    report.evicted = enforce_budget(ledger, durations)
    return report


def early_tier_slow(durations: dict[str, float]) -> set[str]:
    from tests.slow_tier import slow_ids

    return slow_ids(durations)


def entry_cost(test: str, durations: dict[str, float]) -> float:
    return sum(s for nid, s in durations.items() if early_tier.entry_key(nid) == test)


def enforce_budget(
    ledger: dict, durations: dict[str, float], budget: float = early_tier.BUDGET_SECONDS
) -> list[str]:
    """Evict unpinned entries, oldest last failure first, until the ledger fits the budget."""
    entries = _entries(ledger)
    cost = {e["test"]: entry_cost(e["test"], durations) for e in entries}
    pinned = sum(cost[e["test"]] for e in entries if e.get("pinned"))
    if pinned > budget:
        raise BudgetError(
            "the pinned ledger entries alone cost %.1fs of recorded duration, over the %.0fs "
            "early-tier budget (tests/early_tier.py BUDGET_SECONDS): unpin one or raise the "
            "budget on purpose" % (pinned, budget)
        )
    total = sum(cost.values())
    evicted: list[str] = []
    order = sorted(
        (e for e in entries if not e.get("pinned")),
        key=lambda e: (str(e.get("last_failed", "")), str(e.get("promoted", "")), e["test"]),
    )
    for entry in order:
        if total <= budget:
            break
        total -= cost[entry["test"]]
        evicted.append(entry["test"])
    if evicted:
        gone = set(evicted)
        ledger["entries"] = [e for e in entries if e["test"] not in gone]
    return evicted


def missing_tests(ledger: dict, root: Path) -> list[str]:
    """Problems with entries that do not name an existing test function, one message each."""
    problems = []
    for entry in _entries(ledger):
        test = entry.get("test", "")
        path, _, rest = test.partition("::")
        names = rest.split("::") if rest else []
        file = root / path
        if not names:
            problems.append(f"{test!r}: not a test node id (expected path::[Class::]test_name)")
            continue
        if not file.is_file():
            problems.append(f"{test!r}: {path} does not exist (renamed or deleted?)")
            continue
        if not _defines(ast.parse(file.read_text(encoding="utf-8")).body, names):
            problems.append(
                f"{test!r}: {path} defines no {'::'.join(names)} (renamed? update the ledger entry "
                f"or remove it)"
            )
    return problems


def _defines(body: list[ast.stmt], names: list[str]) -> bool:
    head, rest = names[0], names[1:]
    for node in body:
        if rest and isinstance(node, ast.ClassDef) and node.name == head:
            if _defines(node.body, rest):
                return True
        if (
            not rest
            and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == head
        ):
            return True
    return False


def pr_tier_files(root: Path) -> list[str]:
    out = []
    for base in early_tier.TEST_ROOTS:
        for path in (root / base).rglob("test_*.py"):
            if "pr_tier" in path.read_text(encoding="utf-8", errors="replace"):
                out.append(path.relative_to(root).as_posix())
    return out


def early_files(root: Path, ledger: dict, changes: set[tuple[str, str]]) -> list[str]:
    files = {e["test"].split("::", 1)[0] for e in _entries(ledger)}
    files.update(pr_tier_files(root))
    files.update(p.split("::", 1)[0] for p in early_tier.selected_by_rules(root, changes))
    return sorted(f for f in files if (root / f).is_file())


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _write(path: Path, ledger: dict) -> None:
    ledger["entries"] = sorted(_entries(ledger), key=lambda e: e["test"])
    path.write_text(json.dumps(ledger, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--ledger", type=Path, help=f"default: <root>/{early_tier.LEDGER_FILE}")
    parser.add_argument(
        "--durations", type=Path, help=f"default: <root>/{early_tier.DURATIONS_FILE}"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("promote", "demote"):
        p = sub.add_parser(name)
        src = p.add_mutually_exclusive_group(required=True)
        src.add_argument("--junit", type=Path, nargs="+")
        if name == "promote":
            src.add_argument("--failed-ids", type=Path)
            p.add_argument("--reason", default="late failure")
        p.add_argument("--date", default=dt.date.today().isoformat())
        p.add_argument("--state", type=Path, help="carry green streaks between runs")
        p.add_argument("--summary", type=Path, help="append the markdown report here")
    sub.add_parser("check")
    sub.add_parser("early-files")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    ledger_path = args.ledger or root / early_tier.LEDGER_FILE
    durations = early_tier.load_durations(args.durations or root / early_tier.DURATIONS_FILE)
    ledger = early_tier.load_ledger(ledger_path)

    if args.command == "check":
        problems = missing_tests(ledger, root)
        try:
            evicted = enforce_budget(json.loads(json.dumps(ledger)), durations)
        except BudgetError as exc:
            problems.append(str(exc))
            evicted = []
        if evicted:
            problems.append(
                f"over the {early_tier.BUDGET_SECONDS:.0f}s budget; would evict {evicted}"
            )
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1 if problems else 0

    if args.command == "early-files":
        print("\n".join(early_files(root, ledger, early_tier.changed_status(root))))
        return 0

    state = None
    if args.state is not None and args.state.is_file():
        state = early_tier.load_ledger(args.state)
    merge_state(ledger, state)
    # Every entry this run started with, by identity: the operations mutate these dicts and only
    # drop them from the ledger's list, so the state file keeps a removed entry's streak. A
    # demotion is a proposal until someone applies the patch; until then the checked-in ledger
    # still has the entry, and its streak must not restart from the checked-in value.
    seen = list(_entries(ledger))
    results = parse_junit(args.junit) if args.junit else parse_failed_ids(args.failed_ids)
    try:
        if args.command == "promote":
            report = promote(
                ledger,
                results,
                date=args.date,
                reason=args.reason,
                durations=durations,
                rule_targets=early_tier.rule_targets(root),
            )
        else:
            report = demote(ledger, results, date=args.date, durations=durations)
    except BudgetError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 2
    _write(ledger_path, ledger)
    if args.state is not None:
        args.state.parent.mkdir(parents=True, exist_ok=True)
        carried = list(_entries(ledger)) + [
            e for e in seen if all(e is not k for k in _entries(ledger))
        ]
        _write(args.state, json.loads(json.dumps({"entries": carried})))
    text = report.markdown()
    print(text, end="")
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as fh:
            fh.write(f"membership_changed={'true' if report.membership_changed else 'false'}\n")
    if args.summary is not None:
        with args.summary.open("a", encoding="utf-8") as fh:
            fh.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
