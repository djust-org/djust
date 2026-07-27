"""A blocked push must say WHOSE failures blocked it (#2139).

`main` carried three failing doc-snippet tests until #2134. Because the
pre-push hook runs the full suite, **every** contributor's push was rejected —
on branches that had nothing to do with the failure. Nothing alerted, and the
failing test names sit under ~40 passing hook lines, so the state was
discovered only because a push failed. It took three failed pushes to diagnose.

The cost is not the lost minutes. It is that a persistently red suite trains
people to read red as noise and to reach for ``--no-verify`` — the same
argument as #2124.

These tests exercise the attribution logic in
``scripts/pre-push-pytest.sh`` against a real scratch worktree, because that
is the only way to know it attributes correctly rather than confidently.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/pre-push-pytest.sh"
WORKFLOW = ROOT / ".github/workflows/main-health.yml"


def test_the_hook_uses_the_wrapper():
    # A wrapper nothing calls is decorative. The pre-push hook must route
    # through it, or the attribution never runs where it is needed.
    cfg = (ROOT / ".pre-commit-config.yaml").read_text()
    assert "scripts/pre-push-pytest.sh" in cfg, (
        "the pytest pre-push hook must call the wrapper, or a blocked push "
        "still cannot tell whose failures blocked it"
    )


def test_a_green_run_does_no_merge_base_work():
    # Property, not a string. The first version asserted a literal appeared in
    # a slice of the source bounded by a COMMENT — renaming the comment
    # changed the test's meaning, and work added above the marker went
    # unnoticed. Assert instead that nothing expensive precedes the early exit.
    lines = SCRIPT.read_text().splitlines()
    exit_line = next(i for i, ln in enumerate(lines) if 'STATUS" -eq 0 ] && exit 0' in ln)
    # Non-comment lines only: the header explains the merge-base machinery,
    # and a check that cannot tell mentioning from doing would fail on its own
    # documentation.
    before = "\n".join(ln for ln in lines[:exit_line] if not ln.lstrip().startswith("#"))
    for expensive in ("git worktree", "mktemp -d", "merge-base"):
        assert expensive not in before, (
            f"a green run must not reach `{expensive}` — the happy path has to "
            "stay free, or every contributor pays for a diagnostic they do not need"
        )


def test_it_copies_every_python_abi_not_just_one():
    # The tree carries one .so per ABI (cpython-311/312/314). Copying an
    # arbitrary one lands a mismatched ABI into the scratch worktree, every
    # import fails there, and every failure then looks "pre-existing" — the
    # wrong answer, delivered confidently. This bit the first version.
    src = SCRIPT.read_text()
    assert "cp python/djust/_rust*.so" in src, (
        "the scratch worktree needs every ABI copied; `head -1` picks whichever "
        "ls returns first and may not match the venv"
    )
    assert "head -1" not in src.split("# Collect")[0] or "_rust*.so" in src


def test_each_failing_id_is_checked_individually_at_the_base():
    # This test previously asserted `--continue-on-collection-errors` was
    # present and documented it as making absent tests survivable. It does
    # not: pytest resolves every argument BEFORE collecting, so one
    # unresolvable id aborts the session with "no tests ran" and zero results.
    # The test certified a mechanism that did not work, and the behavioural
    # suite (test_red_main_attribution_behaviour_2139.py) is what caught it.
    #
    # The property that actually holds: one invocation per id, so no single
    # id can poison the others.
    src = SCRIPT.read_text()
    assert 'for _id in "${FAILED_IDS[@]}"' in src, (
        "each failing id must be checked on its own; passing the whole set in "
        "one pytest invocation lets one absent id abort the run and report "
        "every pre-existing failure as new"
    )
    assert '"$_id" -q' in src, "the per-id run must pass exactly that one id"


def test_the_failing_ids_are_held_in_an_array():
    # `$FAILED` unquoted meant a parametrized id containing a space
    # (`test_x[a b]`) split into two unresolvable args and poisoned the whole
    # merge-base run. pytest does not sanitise ids.
    src = SCRIPT.read_text()
    assert "FAILED_IDS=()" in src and 'FAILED_IDS+=("$_line")' in src, (
        "failing ids must be read into an array; word-splitting a string of "
        "ids breaks on any id containing a space"
    )


def test_every_early_exit_either_answers_or_says_it_cannot():
    # The property, asserted directly rather than as an arithmetic identity.
    # A first attempt asserted `count("NOT attributed") >= 3`, which tolerates
    # losing one — and the path you lose is exactly the one that would then
    # guess silently. A second attempt tied the count to the number of
    # `exit "$STATUS"` lines, which is also wrong: two of those exits give a
    # real ANSWER ("HEAD is the merge-base, so every failure is pre-existing")
    # rather than bailing.
    #
    # What must hold: every early exit is preceded by a line that either
    # attributes the failures or says it cannot.
    lines = SCRIPT.read_text().splitlines()
    ANSWERS = ("NOT attributed", "pre-existing", "no FAILED lines")
    unexplained = []
    for i, line in enumerate(lines[:-1]):  # the final exit ends a full report
        # Match any `exit`, not the exact literal `exit "$STATUS"` — a bare
        # `exit $STATUS` with no message would otherwise slip past the check
        # designed to catch exactly that.
        if not re.match(r"^\s*exit\b", line):
            continue
        window = " ".join(lines[max(0, i - 6) : i])
        if not any(a in window for a in ANSWERS):
            unexplained.append(i + 1)
    assert not unexplained, (
        f"early exit(s) at line(s) {unexplained} neither attribute the failures "
        f"nor say they cannot — a silent exit leaves the pusher guessing, which "
        f"is the state this script exists to end"
    )


def test_it_does_not_suggest_no_verify():
    # The whole point is to keep the gate while making it legible. Telling a
    # blocked contributor to bypass it would trade the diagnosis for the
    # habit the gate exists to prevent.
    src = SCRIPT.read_text()
    assert "rather than reaching for --no-verify" in src
    assert "This push is still blocked, which is deliberate" in src


# --- the scheduled half ---------------------------------------------------


def test_the_workflow_runs_on_a_schedule_and_can_be_triggered():
    import yaml

    wf = yaml.safe_load(WORKFLOW.read_text())
    on = wf.get("on") or wf.get(True)  # PyYAML parses bare `on:` as True
    assert "schedule" in on, "a red main must be found before someone pushes into it"
    assert "workflow_dispatch" in on, "and be checkable on demand"


def test_the_workflow_can_write_issues():
    import yaml

    wf = yaml.safe_load(WORKFLOW.read_text())
    assert wf["permissions"]["issues"] == "write", (
        "it opens/updates the tracking issue; without this it fails silently"
    )


def test_the_workflow_closes_the_issue_when_main_goes_green():
    # An alert that only ever opens becomes noise, and a stale open issue is
    # indistinguishable from a current one.
    src = WORKFLOW.read_text()
    assert "state: 'closed'" in src
    assert "main is green again" in src


def test_the_issue_body_leads_with_the_blast_radius():
    # The reader most likely to open it is someone whose unrelated push just
    # bounced. The first thing they need is that it was not them.
    src = WORKFLOW.read_text()
    assert "Every push is blocked while this is open" in src
    assert "scripts/pre-push-pytest.sh" in src, (
        "the issue must point at the tool that answers 'is this mine?'"
    )


@pytest.mark.parametrize("marker", ["FAILED ", "summary"])
def test_the_workflow_reports_which_tests_failed(marker):
    # A summary line alone does not say WHICH tests — that is the same
    # information the pre-push hook buries.
    assert marker in WORKFLOW.read_text()
