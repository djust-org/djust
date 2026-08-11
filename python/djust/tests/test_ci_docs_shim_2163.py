"""The docs-only shim must stay aligned with test.yml's paths-ignore (#2163).

`test.yml` skips the whole workflow when every changed file matches its
``paths-ignore``. A skipped WORKFLOW never creates its check runs at all
(unlike a skipped JOB, which reports and satisfies branch protection), so a
required ``test-summary`` would hang at "Expected — Waiting for status to be
reported" on a docs-only PR. ``test-summary-docs-shim.yml`` reports the
context in exactly that case.

The two path lists are therefore one contract split across two files — the
#1646 drift shape. If someone adds ``*.rst`` to test.yml's ``paths-ignore``
and not to the shim, an ``.rst``-only PR skips test.yml, the shim classifies
it as non-docs, its ``test-summary`` job is skipped, and the PR is
unmergeable with no visible cause. These tests make that drift fail loudly
here instead.
"""

from __future__ import annotations

import pathlib
import re

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOWS = pathlib.Path(__file__).resolve().parents[3] / ".github" / "workflows"
TEST_YML = WORKFLOWS / "test.yml"
SHIM_YML = WORKFLOWS / "test-summary-docs-shim.yml"


def _load(path: pathlib.Path) -> dict:
    assert path.is_file(), f"missing workflow: {path}"
    # `on:` is the YAML 1.1 boolean True, so it round-trips as the key True.
    return yaml.safe_load(path.read_text())


def _trigger(doc: dict, event: str) -> dict:
    on = doc.get("on", doc.get(True))
    assert on is not None, "workflow has no trigger block"
    return on[event]


def test_shim_workflow_exists() -> None:
    assert SHIM_YML.is_file(), (
        "the docs-only shim is what lets test-summary be a REQUIRED check; "
        "deleting it silently re-opens #2163"
    )


def test_shim_paths_match_test_yml_paths_ignore() -> None:
    """The one contract this file exists to pin."""
    ignored = _trigger(_load(TEST_YML), "pull_request")["paths-ignore"]
    shim_paths = _trigger(_load(SHIM_YML), "pull_request")["paths"]

    assert sorted(shim_paths) == sorted(ignored), (
        "test-summary-docs-shim.yml's `paths` must mirror test.yml's "
        f"`paths-ignore` exactly.\n  test.yml paths-ignore: {sorted(ignored)}\n"
        f"  shim paths           : {sorted(shim_paths)}\n"
        "Drift means some file type skips test.yml without the shim "
        "vouching for it, and a required test-summary hangs forever."
    )


def test_shim_case_arms_match_the_same_list() -> None:
    """The bash classifier must cover the same globs as the trigger.

    The trigger decides whether the shim RUNS; the `case` arms decide whether
    it VOUCHES. Both have to agree, or the shim runs and then declines.
    """
    ignored = _trigger(_load(TEST_YML), "pull_request")["paths-ignore"]
    body = SHIM_YML.read_text()

    m = re.search(r"^\s*case \"\$f\" in\n\s*([^\n]+)\)", body, re.M)
    assert m, 'could not locate the `case "$f" in` arm in the shim'
    arms = {a.strip() for a in m.group(1).split("|")}

    # GitHub-glob -> shell-`case`-glob. The distinction that matters: GitHub
    # needs `**` to cross a `/`, while shell `case` matches against the whole
    # string with `*` already spanning `/`. So `**.md` is `*.md` here and
    # `docs/**` is `docs/*` — collapsing `**` to `*` is the whole rule.
    expected = {p.replace("**", "*") for p in ignored}
    assert arms == expected, (
        f"shim `case` arms {sorted(arms)} do not cover test.yml's paths-ignore {sorted(expected)}"
    )


def test_shim_reporting_job_is_named_test_summary() -> None:
    """The job name IS the required-check contract; renaming it breaks the gate."""
    jobs = _load(SHIM_YML)["jobs"]
    assert "test-summary" in jobs, (
        f"shim must define a job literally named `test-summary` "
        f"(found: {sorted(jobs)}) — the name is what branch protection matches"
    )


def test_shim_reporting_job_is_gated_not_unconditional() -> None:
    """The shim must never green-wash a mixed PR.

    `paths` here and `paths-ignore` in test.yml are not mutually exclusive: a
    PR touching a .md AND a .py triggers both workflows. If the shim reported
    success unconditionally it would sit a second, always-green `test-summary`
    next to the real one and mask a genuine failure.
    """
    job = _load(SHIM_YML)["jobs"]["test-summary"]
    cond = job.get("if", "")
    assert "docs_only" in cond and "true" in cond, (
        f"the test-summary job must be gated on the detect job's docs_only output, got if: {cond!r}"
    )
    assert job.get("needs") == "detect" or "detect" in (job.get("needs") or []), (
        "the test-summary job must depend on `detect` for its gating input"
    )


def test_test_yml_push_and_pull_request_filters_agree() -> None:
    """test.yml carries the SAME paths-ignore list twice — pin them together.

    `push:` and `pull_request:` each declare their own `paths-ignore`. The shim
    mirrors the pull_request one, so if the two triggers drift the shim can be
    correct for PRs while `main`'s push runs disagree about what a docs-only
    change is. Found while gate-off-testing this file: a mutation aimed at the
    first block silently landed on `push` and changed nothing the other tests
    could see.
    """
    pr = _trigger(_load(TEST_YML), "pull_request")["paths-ignore"]
    push = _trigger(_load(TEST_YML), "push")["paths-ignore"]
    assert sorted(pr) == sorted(push), (
        "test.yml's push and pull_request paths-ignore lists have drifted:\n"
        f"  push        : {sorted(push)}\n  pull_request: {sorted(pr)}\n"
        "The shim mirrors pull_request; divergence means push runs classify "
        "docs-only differently."
    )


def test_test_yml_still_filters_by_path() -> None:
    """If test.yml ever drops paths-ignore, the shim becomes dead weight.

    Not a failure — but it should be deleted rather than left to double-report,
    so this asserts the premise the shim is built on still holds.
    """
    assert "paths-ignore" in _trigger(_load(TEST_YML), "pull_request"), (
        "test.yml no longer filters pull_request by path — the docs-only shim "
        "is now redundant and should be REMOVED along with these tests, since "
        "two workflows reporting `test-summary` is worse than one"
    )
