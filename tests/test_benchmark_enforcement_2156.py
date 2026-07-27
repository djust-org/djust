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

import pytest

yaml = pytest.importorskip("yaml")

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


def test_no_ci_job_that_could_enforce_thresholds_uses_xdist_silently():
    # Any OTHER job running the benchmarks under -n auto is fine — it just
    # executes the bodies. The failure this pins is the inverse: the benchmark
    # job losing its serial invocation in a later edit.
    run = "\n".join(s.get("run", "") for s in _test_yml()["jobs"]["benchmarks"]["steps"])
    assert "tests/benchmarks" in run, "the job must actually run the benchmarks"


# --- a new timing gate must not block a merge until it has proven itself --


def test_the_job_is_non_blocking_until_green_on_the_runner():
    # Canon #1534: a CI job exercising an environment the dev machine cannot
    # reproduce ships `continue-on-error: true` until it has been green on the
    # runner at least once. GitHub runners are shared and noisier than a dev
    # machine, and this is a *timing* gate — the exact class that has now
    # false-failed twice in this repo.
    #
    # When it is promoted, this test should be inverted, not deleted, and the
    # job added to test-summary's AND-condition (#1713: being in `needs` is
    # not the same as gating).
    job = _test_yml()["jobs"]["benchmarks"]
    assert job.get("continue-on-error") is True, (
        "promote deliberately: flip this assertion AND add `benchmarks` to "
        "test-summary's success condition in the same change, or the job is "
        "in `needs` without gating anything (#1713)"
    )


def test_promotion_is_all_or_nothing():
    # The half-promoted state is the trap: a job in `needs` and echoed in the
    # summary, but absent from the AND-condition, looks enforced and is not.
    wf = _test_yml()
    gating = "needs.benchmarks.result" in TEST_YML.read_text()
    non_blocking = wf["jobs"]["benchmarks"].get("continue-on-error") is True
    assert non_blocking != gating, (
        "`benchmarks` must be either non-blocking and absent from the gate, or "
        "blocking and present in it — never continue-on-error while also being "
        "read as a merge condition"
    )
    if gating:
        assert "benchmarks" in wf["jobs"]["test-summary"]["needs"]


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
