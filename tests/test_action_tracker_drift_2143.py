"""The Action Tracker's open count must mean something (#2143).

Nothing closed a tracker row when its GitHub issue closed, so `RETRO.md`'s open
count drifted upward and every retro's Stage 5 re-derived the true state by
hand. Row 325 sat at `Open` against an issue closed a full milestone earlier,
and was found only by chance.

When `scripts/check-action-tracker.py` was first run against the real file it
reported **65 of 70** rows marked `Open` whose issue was already closed. The
number retros quote as a health signal was wrong by an order of magnitude.

These are offline: they exercise the parsing, the drift comparison, and the
`--fix` edit against synthetic tables, so they pin the logic rather than
today's GitHub state.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check-action-tracker.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("cat", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


HEADER = "| # | Action | Source | GitHub | Status | Notes |\n|---|---|---|---|---|---|\n"


def _table(*rows: str) -> str:
    return HEADER + "".join(rows)


# --- parsing --------------------------------------------------------------


def test_it_parses_a_row(mod):
    rows = mod.parse_rows(_table("| 7 | Do a thing | PR #1 | #2 | Open | — |\n"))
    assert len(rows) == 1
    r = rows[0]
    assert (r.num, r.issues, r.status) == (7, [2], "Open")


def test_a_row_with_several_issue_refs_is_understood(mod):
    rows = mod.parse_rows(_table("| 8 | X | Retro v1 | #10, #11 | Open | — |\n"))
    assert rows[0].issues == [10, 11]


def test_a_row_with_no_issue_is_not_drift(mod):
    # One-time tasks and OUT-OF-REPO rows have no issue to disagree with.
    rows = mod.parse_rows(_table("| 9 | One-time task | Retro v1 | — | Open | — |\n"))
    stale_open, stale_closed = mod.find_drift(rows, {})
    assert stale_open == [] and stale_closed == []


def test_an_issue_absent_from_github_is_not_drift(mod):
    # A transferred or deleted issue must not be reported as resolved.
    rows = mod.parse_rows(_table("| 9 | X | PR #1 | #999 | Open | — |\n"))
    assert mod.find_drift(rows, {1: "OPEN"}) == ([], [])


# --- the comparison -------------------------------------------------------


def test_an_open_row_with_a_closed_issue_is_reported(mod):
    rows = mod.parse_rows(_table("| 5 | X | PR #1 | #42 | Open | — |\n"))
    stale_open, _ = mod.find_drift(rows, {42: "CLOSED"})
    assert [d["row"] for d in stale_open] == [5]


def test_an_open_row_with_an_open_issue_is_silent(mod):
    rows = mod.parse_rows(_table("| 5 | X | PR #1 | #42 | Open | — |\n"))
    assert mod.find_drift(rows, {42: "OPEN"}) == ([], [])


def test_a_row_is_only_closed_when_ALL_its_issues_are(mod):
    # A row tracking two issues is not resolved until both are. Reporting it
    # early is the same class of wrong answer as not reporting it late.
    rows = mod.parse_rows(_table("| 5 | X | PR #1 | #42, #43 | Open | — |\n"))
    stale_open, _ = mod.find_drift(rows, {42: "CLOSED", 43: "OPEN"})
    assert stale_open == []


def test_the_reverse_drift_is_reported_too(mod):
    # A row marked Closed whose issue was REOPENED is equally misleading, and
    # in the more dangerous direction: it hides live work.
    rows = mod.parse_rows(_table("| 5 | X | PR #1 | #42 | Closed | done |\n"))
    _, stale_closed = mod.find_drift(rows, {42: "OPEN"})
    assert [d["row"] for d in stale_closed] == [5]


def test_an_out_of_repo_row_is_left_alone(mod):
    # OUT-OF-REPO rows are blocked on another repository; their local issue
    # state says nothing about whether the work is done.
    rows = mod.parse_rows(_table("| 5 | X | PR #1 | #42 | OUT-OF-REPO | see upstream |\n"))
    assert mod.find_drift(rows, {42: "CLOSED"}) == ([], [])


# --- the edit -------------------------------------------------------------


def test_fix_flips_the_status_and_writes_the_closing_pr(mod):
    # The reason is the half that makes the row worth keeping. A bare status
    # flip loses it, which is why this looks the closing PR up.
    text = _table("| 5 | X | PR #1 | #42 | Open | — |\n")
    rows = mod.parse_rows(text)
    stale, _ = mod.find_drift(rows, {42: "CLOSED"})
    out = mod.apply_fix(text, stale, {42: 1234})
    assert "| Closed |" in out
    assert "Resolved by PR #1234" in out


def test_fix_never_overwrites_a_human_written_reason(mod):
    text = _table("| 5 | X | PR #1 | #42 | Open | superseded by the rewrite |\n")
    rows = mod.parse_rows(text)
    stale, _ = mod.find_drift(rows, {42: "CLOSED"})
    out = mod.apply_fix(text, stale, {42: 1234})
    assert "superseded by the rewrite" in out
    assert "Resolved by PR" not in out
    assert "| Closed |" in out


def test_fix_touches_only_the_drifted_row(mod):
    text = _table(
        "| 5 | X | PR #1 | #42 | Open | — |\n",
        "| 6 | Y | PR #2 | #43 | Open | — |\n",
    )
    rows = mod.parse_rows(text)
    stale, _ = mod.find_drift(rows, {42: "CLOSED", 43: "OPEN"})
    out = mod.apply_fix(text, stale, {42: 1234})
    assert out.count("| Closed |") == 1
    assert "| 6 | Y | PR #2 | #43 | Open | — |" in out


def test_fix_leaves_a_row_it_does_not_understand_untouched(mod):
    # Better to skip a row than to write into the wrong cell of it.
    text = HEADER + "| 5 | X | PR #1 | #42 | Open |\n"  # one cell short
    stale = [{"line": 3, "issues": [42], "notes": "—", "row": 5, "action": "X"}]
    assert mod.apply_fix(text, stale, {42: 1}) == text


def test_a_notes_cell_containing_a_pipe_survives_the_edit(mod):
    # Notes are free prose and some rows contain code spans. This is why the
    # edit works on split cells rather than a regex over the line.
    text = _table("| 5 | X | PR #1 | #42 | Open | — |\n")
    rows = mod.parse_rows(text)
    stale, _ = mod.find_drift(rows, {42: "CLOSED"})
    out = mod.apply_fix(text, stale, {42: 7})
    assert out.rstrip().endswith("| Resolved by PR #7 |")


# --- it must not report "all clean" when it checked nothing ---------------


def test_a_missing_table_is_an_error_not_a_pass(mod, monkeypatch, capsys):
    monkeypatch.setattr(mod, "RETRO", ROOT / "pyproject.toml")
    monkeypatch.setattr("sys.argv", ["x"])
    assert mod.main() == 2, "no parsed rows must not exit 0 — that reads as 'no drift'"


def test_an_unreachable_github_is_an_error_not_a_pass(mod, monkeypatch, capsys):
    def boom():
        raise RuntimeError("no network")

    monkeypatch.setattr(mod, "issue_states", boom)
    monkeypatch.setattr("sys.argv", ["x"])
    assert mod.main() == 2, (
        "failing to reach GitHub must not exit 0 — reporting zero drift when "
        "nothing was checked is the failure this script exists to end"
    )
    assert "nothing was checked" in capsys.readouterr().err


def test_drift_exits_nonzero(mod, monkeypatch, tmp_path):
    p = tmp_path / "R.md"
    p.write_text(_table("| 5 | X | PR #1 | #42 | Open | — |\n"))
    monkeypatch.setattr(mod, "RETRO", p)
    monkeypatch.setattr(mod, "issue_states", lambda: ({42: "CLOSED"}, {}))
    monkeypatch.setattr("sys.argv", ["x", "--quiet"])
    assert mod.main() == 1


def test_no_drift_exits_zero(mod, monkeypatch, tmp_path):
    p = tmp_path / "R.md"
    p.write_text(_table("| 5 | X | PR #1 | #42 | Open | — |\n"))
    monkeypatch.setattr(mod, "RETRO", p)
    monkeypatch.setattr(mod, "issue_states", lambda: ({42: "OPEN"}, {}))
    monkeypatch.setattr("sys.argv", ["x", "--quiet"])
    assert mod.main() == 0


# --- the real file --------------------------------------------------------


def test_the_real_tracker_still_parses():
    # The parser is regex-based, so a format change to RETRO.md would silently
    # produce zero rows — which would look exactly like "no drift".
    import importlib.util

    spec = importlib.util.spec_from_file_location("cat2", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    rows = m.parse_rows((ROOT / "RETRO.md").read_text())
    assert len(rows) > 100, f"only {len(rows)} rows parsed — has the table format changed?"
    assert all(r.status for r in rows)
