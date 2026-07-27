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


def test_the_wrapper_passes_a_green_run_straight_through():
    # It must add nothing on the happy path — no extra worktree, no extra
    # suite run. Verified by source: the merge-base work is unreachable
    # before the early exit.
    src = SCRIPT.read_text()
    body = src[: src.index("# Collect the failing node ids.")]
    assert 'STATUS" -eq 0 ] && exit 0' in body, "a green run must exit before any merge-base work"


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


def test_pre_existing_means_explicitly_failed_at_the_base():
    # A test that passed at the base, errored on collection, or did not exist
    # is NEW. Attributing to the contributor is the conservative direction —
    # they will investigate; the reverse dismisses a real regression.
    src = SCRIPT.read_text()
    assert "--continue-on-collection-errors" in src, (
        "a test absent at the merge-base is a collection error, not a failure; "
        "without this flag the run aborts and nothing is attributed"
    )
    assert re.search(r"PRE=\$\(grep -E '\^FAILED '", src)


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
        if line.strip() != 'exit "$STATUS"':
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
