"""The benchmark thresholds must be enforced *somewhere* (#2156).

`tests/benchmarks/conftest.py` skips `_assert_benchmark_under` whenever
`benchmark.stats` is unavailable — which is what pytest-benchmark does under
pytest-xdist. Every CI job and `make test` run `-n auto`, so the only context
enforcing these thresholds was the serial pre-push hook: a process that had
just executed 10,000 tests, where a warm and fragmented heap makes the median
systematically slower.

``test_vdom_diff_list_reorder`` measures **3.78ms** on a quiet machine against
its 5ms target and **7.57ms** there. So a threshold no CI job checked was
blocking every push on `main`, while measuring the environment rather than the
code — found by `scripts/pre-push-pytest.sh` (#2139) on its first real use.

The conftest docstring asserted "the benchmark-gated CI job enforces it". A
grep for ``--benchmark-only`` across all twelve workflows returned **nothing**;
the job had never existed. That is the #1867 class — a prose invariant nobody
had run against the code — and it is what these tests exist to prevent
recurring.
"""

from __future__ import annotations

from pathlib import Path

import yaml  # hard import, deliberately

# NOT `pytest.importorskip`. PyYAML is not a declared dependency — it arrives
# transitively via `uvicorn[standard]` — so an importorskip made all seven pins
# report `1 skipped` and the suite stay green with zero signal. "Nothing was
# checked" reading as "all clean" is the failure this repo's own tooling exits
# 2 to avoid, and the sibling pin in test_main_health_workflow_2139.py already
# uses a hard import.

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"
TEST_YML = WORKFLOWS / "test.yml"
BENCH_CONFTEST = ROOT / "tests/benchmarks/conftest.py"


def _test_yml() -> dict:
    return yaml.safe_load(TEST_YML.read_text())


def code_only(text: str) -> str:
    """Strip comment lines before asserting a token is absent.

    A source-grep assertion that scans raw text cannot tell *mentioning* a
    thing from *doing* it, so the comment explaining why a flag is absent
    satisfies an assertion that the flag is absent. This exact hole appeared
    **five times** across #2139/#2155/#2156 — every time it was caught by
    gate-off, and every time it was fixed locally instead of generalised.

    Any absence assertion over source should go through this.
    """
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


# --- the job the docstring claims exists must actually exist --------------


def test_a_serial_benchmark_job_exists():
    job = _test_yml()["jobs"].get("benchmarks")
    assert job is not None, (
        "conftest.py tells readers a benchmark-gated CI job enforces these "
        "thresholds. For the whole life of that sentence, no such job existed."
    )


def test_the_benchmark_job_runs_serially():
    # The entire point. `-n auto` disables pytest-benchmark's stats
    # collection, which is precisely how the thresholds came to be enforced
    # nowhere; a benchmark job that inherited it would be decorative.
    steps = _test_yml()["jobs"]["benchmarks"]["steps"]
    run = code_only("\n".join(s.get("run", "") for s in steps))
    assert "--benchmark-only" in run
    assert "-n auto" not in run, (
        "xdist disables benchmark stats, so the thresholds would silently not "
        "be enforced in the one job that exists to enforce them"
    )


def test_the_benchmark_job_actually_runs_the_benchmarks():
    # Renamed: the old name claimed a global property ("no CI job uses xdist
    # silently") that the body never checked and that is false anyway —
    # test.yml's python-tests runs `pytest tests/ ... -n auto`, and `tests/`
    # contains `tests/benchmarks/`. That is harmless (it just executes the
    # bodies); the failure worth pinning is this job losing its target path.
    run = "\n".join(s.get("run", "") for s in _test_yml()["jobs"]["benchmarks"]["steps"])
    assert "tests/benchmarks" in run, "the job must actually run the benchmarks"


# --- a new timing gate must not block a merge until it has proven itself --


def test_the_job_now_fails_the_aggregate_check():
    """Inverted at promotion (#2160), as its previous form instructed.

    Deliberately NOT named "blocks the merge". `main` has **no required status
    checks** — `repos/.../branches/main/protection` has no
    `required_status_checks` key and the rulesets carry only deletion,
    non-fast-forward and a review requirement. So a red `test-summary` does not
    stop a merge here; this job failing fails the aggregate check, and nothing
    more. Tracked in #2163.

    That is #1713 one level up, and worth stating precisely: the first version
    of this PR claimed "blocking merge gate" in its title, CHANGELOG, this test
    name and this docstring, all on the strength of the AND-chain edit — which
    is necessary and not sufficient.

    What this does buy: `continue-on-error: true` previously made
    `needs.benchmarks.result == 'success'` even on failure, so the job proved
    the thresholds could be enforced without enforcing them. Now a threshold
    breach turns the aggregate red, which is visible and actionable.
    """
    job = _test_yml()["jobs"]["benchmarks"]
    assert job.get("continue-on-error") is not True, (
        "the benchmarks job must fail the aggregate check; job-level "
        "continue-on-error would make needs.benchmarks.result 'success' even "
        "when the thresholds fail"
    )
    # F2: a STEP-level flag defeats the gate identically, and this repo uses
    # that idiom four times in this very file (test.yml:456,475,488,492) with
    # comments recommending it as the way to ship a new check. A pin that sees
    # only the job-level key is load-bearing on one axis and decorative on the
    # adjacent one (#1859/#1543).
    for step in job.get("steps", []):
        assert step.get("continue-on-error") is not True, (
            f"step {step.get('name') or step.get('uses')!r} fails soft, so the "
            f"job still reports success and the AND-chain passes with the "
            f"thresholds breached"
        )


def _gates_the_merge() -> bool:
    """Is `benchmarks` in test-summary's AND-chain — the thing that fails the run?

    A whole-file scan for `needs.benchmarks.result` is wrong in both
    directions, and the first version of this test used one. The summary's
    *echo* block mentions every job, so the scan reported "gating" for a job
    that only appeared in a status line; and a comment naming the string made
    it report gating with nothing changed at all. Only the `if [ ... ] && [
    ... ]` chain decides whether the run fails (#1713).
    """
    steps = _test_yml()["jobs"]["test-summary"]["steps"]
    run = code_only("\n".join(s.get("run", "") for s in steps))
    if "if [" not in run:
        return False
    and_chain = run.split("if [", 1)[1].split("; then", 1)[0]
    return "needs.benchmarks.result" in and_chain


def test_promotion_is_all_or_nothing():
    # The half-promoted state is the trap: a job in `needs` and echoed in the
    # summary, but absent from the AND-condition, looks enforced and gates
    # nothing.
    #
    # The first version of this test could not see that state. A reviewer
    # shipped it in full — continue-on-error removed, job added to `needs`,
    # echoed in the summary, absent from the AND-chain — and the suite stayed
    # green (0 failed / 7 passed). That is #1859: a decorative pin, and it was
    # this PR's headline safety claim.
    wf = _test_yml()
    gating = _gates_the_merge()
    non_blocking = wf["jobs"]["benchmarks"].get("continue-on-error") is True
    assert non_blocking != gating, (
        "`benchmarks` must be either non-blocking and absent from the gate, or "
        "blocking and present in it — never continue-on-error while also being "
        "read as a merge condition, and never in `needs` without being in the "
        "AND-chain"
    )
    if gating:
        assert "benchmarks" in wf["jobs"]["test-summary"]["needs"], (
            "a job read in the AND-chain must also be in `needs`, or the expression is always empty"
        )


# --- the targets were not loosened ---------------------------------------


def test_the_targets_were_not_quietly_raised():
    # The reflex this issue exists to stop. Raising a bound is what turned a
    # 10ms threshold into 100ms in test_redis_serialization_performance, which
    # then flaked again at 100ms. Measured on a quiet machine, all 58
    # benchmarks pass at these values, so the numbers were never the problem.
    src = BENCH_CONFTEST.read_text()
    assert "TARGET_PER_EVENT_S = 0.002" in src
    assert "TARGET_LIST_UPDATE_S = 0.005" in src
    assert "TARGET_WS_MOUNT_S = 0.100" in src


def test_the_conftest_no_longer_claims_a_job_that_does_not_exist():
    # #1867: a prose invariant nobody ran against the code. The citation was
    # checkable and false for the whole life of the sentence.
    src = BENCH_CONFTEST.read_text()
    assert ".github/workflows/test.yml" in src, (
        "name the real job, so the claim is verifiable rather than aspirational"
    )
    assert "benchmark-gated CI job (`--benchmark-only` serial) enforces it." not in src
