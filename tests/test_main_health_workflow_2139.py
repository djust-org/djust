"""The main-health workflow's decision logic, EXECUTED (#2139).

The workflow's `github-script` block decides whether to open, comment on, or
close a tracking issue, and which of two issues it is talking about. None of
that had any coverage: removing `if: always()`, collapsing the three states
back to two, or re-finding the issue by LABEL instead of TITLE all left the
suite green, because every test was a `grep` over the YAML.

A YAML file cannot be run, but the JavaScript inside it can. These extract the
`script:` block and execute it under stubbed `github`/`context` objects, so the
branch that files an issue is the branch under test.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/main-health.yml"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


def _script() -> str:
    wf = yaml.safe_load(WORKFLOW.read_text())
    for step in wf["jobs"]["report"]["steps"]:
        if "github-script" in str(step.get("uses", "")):
            return step["with"]["script"]
    raise AssertionError("no github-script step found in the report job")


def _run_script(suite_result: str, failed: str, open_issues: list[dict]) -> dict:
    """Execute the workflow's script and return every API call it made."""
    harness = textwrap.dedent("""
        const calls = [];
        const OPEN = %s;
        const github = {
          paginate: async (_fn, _opts) => OPEN,
          rest: {
            issues: {
              listForRepo: () => {},
              create: async (o) => { calls.push({op: 'create', ...o}); },
              createComment: async (o) => { calls.push({op: 'comment', ...o}); },
              update: async (o) => { calls.push({op: 'update', ...o}); },
            },
          },
        };
        const context = {
          serverUrl: 'https://github.com',
          repo: {owner: 'djust-org', repo: 'djust'},
          runId: 1,
        };
        process.env.SUITE_RESULT = %s;
        process.env.FAILED = %s;
        process.env.SUMMARY = '1 failed, 2 passed';
        (async () => {
        %s
        })().then(() => console.log('@@' + JSON.stringify(calls)));
        """) % (
        json.dumps(open_issues),
        json.dumps(suite_result),
        json.dumps(failed),
        textwrap.indent(_script(), "        "),
    )
    r = subprocess.run(["node", "-e", harness], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    line = [x for x in r.stdout.splitlines() if x.startswith("@@")]
    assert line, r.stdout + r.stderr
    return {"calls": json.loads(line[0][2:])}


RED = "main is RED — the full suite fails on main"
INFRA = "main-health check could not run"


def _issue(number: int, title: str, is_pr: bool = False) -> dict:
    d = {"number": number, "title": title}
    if is_pr:
        d["pull_request"] = {"url": "x"}
    return d


# --- the three states -----------------------------------------------------


def test_a_red_suite_opens_the_red_issue():
    calls = _run_script("success", "true", [])["calls"]
    creates = [c for c in calls if c["op"] == "create"]
    assert len(creates) == 1
    assert creates[0]["title"] == RED
    assert "Every push is blocked" in creates[0]["body"]


def test_a_green_suite_closes_the_red_issue():
    calls = _run_script("success", "false", [_issue(7, RED)])["calls"]
    assert [c["op"] for c in calls] == ["comment", "update"]
    assert calls[1]["state"] == "closed"
    assert calls[1]["issue_number"] == 7


def test_a_green_suite_with_nothing_open_does_nothing():
    assert _run_script("success", "false", [])["calls"] == []


def test_an_infrastructure_failure_is_not_reported_as_a_red_main():
    # The whole point of the third state: telling people main is RED when the
    # job merely failed to install sends them hunting for a regression that
    # does not exist.
    calls = _run_script("failure", "", [])["calls"]
    creates = [c for c in calls if c["op"] == "create"]
    assert len(creates) == 1
    assert creates[0]["title"] == INFRA
    assert "NOT evidence that main is red" in creates[0]["body"]


def test_a_cancelled_run_never_reaches_the_script():
    # `if: !cancelled()` rather than `always()`. A cancelled workflow_dispatch
    # tells us nothing, and filing "could not run" for it is noise that then
    # has to be closed by hand.
    wf = yaml.safe_load(WORKFLOW.read_text())
    cond = str(wf["jobs"]["report"]["if"])
    assert "cancelled()" in cond and "!" in cond, (
        f"report must run on failure but not on cancellation; got {cond!r}"
    )
    assert cond.strip() != "always()"


# --- the two issues must not deadlock each other --------------------------


def test_a_green_run_closes_a_stale_could_not_run_issue():
    # The bug this pins: TITLE is computed from the CURRENT run's state, so a
    # green run looked up only the RED title and left an "could not run" issue
    # from an earlier infra failure open forever — "a stale open issue is
    # indistinguishable from a current one", which is the thing the closing
    # logic exists to prevent.
    calls = _run_script("success", "false", [_issue(9, INFRA)])["calls"]
    closed = [c for c in calls if c["op"] == "update" and c["state"] == "closed"]
    assert [c["issue_number"] for c in closed] == [9]


def test_a_red_run_closes_a_stale_could_not_run_issue():
    # We now know the check CAN run, so "could not tell" is obsolete.
    calls = _run_script("success", "true", [_issue(9, INFRA)])["calls"]
    closed = [c for c in calls if c["op"] == "update" and c["state"] == "closed"]
    assert [c["issue_number"] for c in closed] == [9]
    assert any(c["op"] == "create" and c["title"] == RED for c in calls)


def test_an_infra_failure_does_NOT_close_an_open_red_issue():
    # The asymmetry that matters: "we cannot tell" is no reason to declare a
    # red main resolved. It may still be red.
    calls = _run_script("failure", "", [_issue(5, RED)])["calls"]
    assert not [c for c in calls if c["op"] == "update"], (
        "an infrastructure failure must not close the red-main issue — we did "
        "not learn that main is green, only that we cannot tell"
    )


def test_an_existing_issue_is_commented_not_duplicated():
    calls = _run_script("success", "true", [_issue(3, RED)])["calls"]
    assert not [c for c in calls if c["op"] == "create"]
    assert [c["op"] for c in calls] == ["comment"]
    assert calls[0]["issue_number"] == 3


# --- how it finds the issue ----------------------------------------------


def test_it_matches_on_title_not_label():
    # Labels can be dropped silently — GitHub drops unknown labels for actors
    # without push access, and a label can simply be deleted. A re-find that
    # depends on one turns this into a daily duplicate-issue generator with
    # nothing ever closed. The stub returns an issue with the right title and
    # NO labels; a label-based lookup would miss it and create a duplicate.
    calls = _run_script("success", "true", [_issue(3, RED)])["calls"]
    assert not [c for c in calls if c["op"] == "create"], (
        "an issue with the right title but no label must still be found"
    )


def test_a_pull_request_with_the_same_title_is_not_mistaken_for_the_issue():
    # The issues API returns PRs too. Commenting on a PR instead of the
    # tracking issue would be silent and confusing.
    calls = _run_script("success", "true", [_issue(11, RED, is_pr=True)])["calls"]
    assert [c["op"] for c in calls] == ["create"], (
        "a PR sharing the title must not be treated as the tracking issue"
    )


def test_the_issue_list_is_paginated():
    # per_page defaults to 30 and the list includes PRs, so an ageing tracking
    # issue eventually falls off page 1 and gets recreated daily.
    src = _script()
    assert "github.paginate" in src, (
        "listForRepo must be paginated or the tracking issue is eventually "
        "recreated every day and never closed"
    )


def test_the_script_is_syntactically_valid():
    # Nothing else parses it, so a syntax error would first surface on a
    # scheduled run — at which point nobody is told, which is the failure mode
    # this workflow exists to remove.
    r = subprocess.run(
        ["node", "--check", "-"],
        input=f"(async () => {{\n{_script()}\n}})()",
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr


# --- the suite job's own guards ------------------------------------------


def test_a_failed_rust_build_fails_the_job_rather_than_reporting_a_red_main():
    # `maturin develop --release || true` swallowed the failure, so a broken
    # build produced hundreds of import errors that were then reported as
    # "main is RED" — exactly what the three-state logic exists to prevent.
    src = WORKFLOW.read_text()
    assert "maturin develop --release || true" not in src
    assert "maturin develop --release" in src


def test_an_unparseable_run_is_an_infrastructure_failure_not_a_red_main():
    src = WORKFLOW.read_text()
    assert 'if [ -z "$SUMMARY" ]; then' in src, (
        "pytest producing no summary line means it never got far enough to "
        "have an opinion; that is 'could not run', not 'main is RED'"
    )


def test_the_workflow_parses_ids_the_same_way_the_script_does():
    # The two halves of one PR disagreeing about the same parse is the drift
    # class this repo keeps hitting (#1646). The workflow displays ids; the
    # script attributes them. Both must strip at the first " - " outside
    # brackets.
    assert "sed 's/ - .*//'" not in WORKFLOW.read_text(), (
        "the old greedy strip truncates any id whose parameter contains ' - '"
    )
    assert 'substr($0, i, 3) == " - "' in WORKFLOW.read_text()
