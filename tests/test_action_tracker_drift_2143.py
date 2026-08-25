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


# --- Open Items checkboxes (#2200) ----------------------------------------
#
# The same drift, in RETRO.md's other structure. 164 of 197 unchecked boxes
# referenced issues that had all closed — including, at RETRO.md:533, the row
# asking for exactly this automation. The row about closing rows was itself a
# stale row.
#
# Offline like the table cases above: synthetic markdown, injected issue
# states, so these pin the logic rather than today's GitHub.


def _items(*lines: str) -> str:
    return "## Open Items\n\n" + "".join(lines)


def test_an_unchecked_item_whose_issue_closed_is_drift(mod):
    items = mod.parse_items(_items("- [ ] Do the thing (GitHub #42)\n"))
    stale_open, stale_closed = mod.find_item_drift(items, {42: "CLOSED"})
    assert len(stale_open) == 1 and not stale_closed


def test_an_unchecked_item_with_an_open_issue_is_left_alone(mod):
    items = mod.parse_items(_items("- [ ] Do the thing (GitHub #42)\n"))
    assert mod.find_item_drift(items, {42: "OPEN"}) == ([], [])


def test_one_open_issue_among_several_blocks_the_tick(mod):
    # Partly-done work must never be marked finished.
    items = mod.parse_items(_items("- [ ] A (#41) and B (#42)\n"))
    assert mod.find_item_drift(items, {41: "CLOSED", 42: "OPEN"}) == ([], [])


def test_an_item_with_no_issue_reference_is_left_alone(mod):
    # 28 real items are like this — carried OUT-OF-REPO work, or prose headers.
    # There is nothing to check them against, and guessing is worse than drift.
    items = mod.parse_items(_items("- [ ] Something with no reference at all\n"))
    assert mod.find_item_drift(items, {42: "CLOSED"}) == ([], [])


def test_a_github_ref_beats_a_colliding_tracker_number(mod):
    """The bug that nearly wrote a wrong PR number into a document (#1197).

    RETRO.md carries two numbering schemes on one line, and they collide: an
    "Action Tracker #329" is a different thing from GitHub issue #329, which
    also exists. 38 of the 94 unchecked items naming a GitHub issue also carry
    a tracker number resolving to a real, unrelated issue.

    A first pass took the first `#NNN` and reported #2142 as closed by PR #362
    — issue #329's closer. Here #329 is OPEN and #2142 CLOSED, so a parser
    reading the tracker number gets the answer exactly backwards.
    """
    items = mod.parse_items(_items("- [ ] Thing — Action Tracker #329 (GitHub #2142)\n"))
    assert items[0].issues == [2142], "the GitHub ref is authoritative"
    stale_open, _ = mod.find_item_drift(items, {329: "OPEN", 2142: "CLOSED"})
    assert len(stale_open) == 1, "the tracker number must not veto the decision"


def test_a_human_written_resolution_is_left_alone_in_both_directions(mod):
    """RETRO.md:682 is correct and a naive rule calls it drift.

    `bug-capture iter B (#1562) — **resolved in v1.1.0-11 (PR #2083)**; iter C
    (#1561) still deferred` — ticked, precise, and citing an issue that is
    legitimately still open. One permanent false alarm is enough to make a
    check worth ignoring.
    """
    body = "- [x] iter B (#41) — **resolved in v1 (PR #99)**; iter C (#42) deferred\n"
    items = mod.parse_items(_items(body))
    assert mod.find_item_drift(items, {41: "CLOSED", 42: "OPEN"}) == ([], [])


def test_fix_ticks_the_box_and_cites_the_real_closing_pr(mod):
    text = _items("- [ ] Do the thing (GitHub #42)\n")
    items = mod.parse_items(text)
    stale_open, _ = mod.find_item_drift(items, {42: "CLOSED"})
    out = mod.apply_item_fix(text, stale_open, {42: 1234})
    assert "- [x] Do the thing (GitHub #42) — **closed (PR #1234)**" in out


def test_fix_says_only_closed_when_github_reports_no_pr(mod):
    # Never a guessed PR number (#1197).
    text = _items("- [ ] Do the thing (GitHub #42)\n")
    items = mod.parse_items(text)
    stale_open, _ = mod.find_item_drift(items, {42: "CLOSED"})
    out = mod.apply_item_fix(text, stale_open, {})
    assert "— **closed**" in out
    assert "PR #" not in out.split("**closed**")[0].split("- [x]")[1]


def test_fix_preserves_indentation_and_body_verbatim(mod):
    text = _items("    - [ ] Nested item with `code | pipes` (GitHub #42)\n")
    items = mod.parse_items(text)
    stale_open, _ = mod.find_item_drift(items, {42: "CLOSED"})
    out = mod.apply_item_fix(text, stale_open, {42: 7})
    assert "    - [x] Nested item with `code | pipes` (GitHub #42) — **closed (PR #7)**" in out


def test_fix_is_idempotent(mod):
    text = _items("- [ ] Do the thing (GitHub #42)\n")
    once = mod.apply_item_fix(
        text, mod.find_item_drift(mod.parse_items(text), {42: "CLOSED"})[0], {42: 7}
    )
    twice = mod.apply_item_fix(
        once, mod.find_item_drift(mod.parse_items(once), {42: "CLOSED"})[0], {42: 7}
    )
    assert once == twice
    assert once.count("**closed") == 1


def test_item_drift_exits_nonzero_even_when_the_table_is_clean(mod, monkeypatch, tmp_path):
    # The two structures are checked together, so a clean table must not mask a
    # drifted checklist — which is exactly how the Open Items reached 164.
    p = tmp_path / "R.md"
    p.write_text(_table("| 5 | X | PR #1 | #41 | Open | — |\n") + "\n- [ ] Item (GitHub #42)\n")
    monkeypatch.setattr(mod, "RETRO", p)
    monkeypatch.setattr(mod, "issue_states", lambda: ({41: "OPEN", 42: "CLOSED"}, {}))
    monkeypatch.setattr("sys.argv", ["x", "--quiet"])
    assert mod.main() == 1


def test_the_real_open_items_still_parse():
    # Same reasoning as the table parser: a format change would silently yield
    # zero items, which reads identically to "no drift".
    import importlib.util

    spec = importlib.util.spec_from_file_location("cat3", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    items = m.parse_items((ROOT / "RETRO.md").read_text())
    assert len(items) > 100, f"only {len(items)} items parsed — has the format changed?"
    assert any(i.issues for i in items), "no item carries an issue reference"


def test_fix_cites_every_closing_pr_when_an_item_names_several_issues(mod):
    """No arbitrary "primary" ref.

    An item citing two closed issues has two equally defensible primaries, and
    a gate-off mutation swapping first for last survived the suite because the
    choice is arbitrary. Citing every closer removes the choice rather than
    pinning a guess — and matches the table path, which already writes
    `Resolved by PR #a, PR #b`.
    """
    text = _items("- [ ] Both halves (#41 and #42)\n")
    items = mod.parse_items(text)
    stale_open, _ = mod.find_item_drift(items, {41: "CLOSED", 42: "CLOSED"})
    out = mod.apply_item_fix(text, stale_open, {41: 100, 42: 200})
    assert "— **closed (PR #100, PR #200)**" in out


def test_fix_deduplicates_a_shared_closing_pr(mod):
    # Two issues closed by one PR must not read `PR #7, PR #7`.
    text = _items("- [ ] Both halves (#41 and #42)\n")
    items = mod.parse_items(text)
    stale_open, _ = mod.find_item_drift(items, {41: "CLOSED", 42: "CLOSED"})
    out = mod.apply_item_fix(text, stale_open, {41: 7, 42: 7})
    assert "— **closed (PR #7)**" in out


def test_a_boundary_misparse_fails_loudly_instead_of_rewriting_prose(mod, monkeypatch):
    """The post-condition guard, exercised rather than trusted.

    `apply_item_fix` reassembles each line from the regex's captured groups, so
    a parser that mis-identified the body boundary would silently rewrite prose
    — the failure mode `strike-shipped-roadmap-rows.py` hit on ROADMAP.md:3926,
    where a naive split ate half a description.

    The guard has no trigger with well-formed input, which makes it exactly the
    kind of assertion that can rot into decoration (#1859). Forcing a mis-parse
    proves it still fires.
    """
    text = _items("- [ ] Do the thing (GitHub #42)\n")
    items = mod.parse_items(text)
    stale_open, _ = mod.find_item_drift(items, {42: "CLOSED"})

    # A regex that drops the last character of the body — the shape of an
    # off-by-one boundary bug.
    monkeypatch.setattr(mod, "ITEM_RE", __import__("re").compile(r"^(\s*)- \[( |x)\] (.*).$"))
    with pytest.raises(AssertionError, match="would be mutated"):
        mod.apply_item_fix(text, stale_open, {42: 7})
