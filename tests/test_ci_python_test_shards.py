"""The 4-way pytest-split shard of CI's python-tests job is wired coherently.

CI runs the Python suite as ``--splits N --group M`` shards over a ``group``
matrix dimension, balanced by the committed ``.test_durations``. Each piece
is a plain string in YAML that nothing type-checks, and the failure modes are
silent:

- ``--splits 4`` with a ``group: [1, 2, 3]`` matrix runs three quarters of
  the suite and reports green.
- ``--splits 4`` with ``group: [1, 2, 3, 4, 5]`` makes pytest-split refuse
  group 5 — loud, at least — but a ``group: [1, 1, 2, 3]`` typo runs one
  quarter twice and skips one, green.
- A per-checkout step gated on ``if: matrix.group == 5`` never runs on any
  shard; every one of those steps is a merge gate (ruff, mypy, the ADR /
  doc-snippet / lockfile checks), so the gate would be decorative (#1859).
- A ``--durations-path`` pointing at a file that is not there falls back to
  count-balancing on every run, silently forfeiting the balance the file
  exists to provide.

These pins make each of those a red test. The companion
``tests/test_ci_python_test_roots.py`` pins that the ONE pytest invocation
still names all three roots; this file assumes that and pins the shard shape
on top of it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/test.yml"
DURATIONS = ROOT / ".test_durations"


def _jobs() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]


def _python_tests() -> dict:
    jobs = _jobs()
    assert "python-tests" in jobs, "python-tests job renamed/removed — update this pin"
    return jobs["python-tests"]


def _pytest_command() -> str:
    cmds = []
    for step in _python_tests()["steps"]:
        for line in (step.get("run") or "").splitlines():
            s = line.strip()
            if not s.startswith("#") and "-m pytest" in s:
                cmds.append(s)
    assert len(cmds) == 1, cmds
    return cmds[0]


def _matrix_groups() -> list[int]:
    matrix = _python_tests()["strategy"]["matrix"]
    assert "group" in matrix, "python-tests lost its `group` shard dimension"
    return list(matrix["group"])


def test_matrix_groups_are_exactly_one_through_n() -> None:
    groups = _matrix_groups()
    assert groups == list(range(1, len(groups) + 1)), (
        f"shard groups must be exactly 1..N with no gaps or repeats, got {groups}"
    )


def test_splits_flag_equals_the_number_of_matrix_groups() -> None:
    cmd = _pytest_command()
    m = re.search(r"--splits\s+(\d+)", cmd)
    assert m, f"pytest invocation has no --splits: {cmd}"
    assert int(m.group(1)) == len(_matrix_groups()), (
        f"--splits {m.group(1)} but the matrix has {len(_matrix_groups())} groups: "
        "a mismatch either runs a fraction of the suite (green) or makes "
        "pytest-split reject the out-of-range group."
    )
    assert "--group ${{ matrix.group }}" in cmd, cmd


def _matrix_python_versions() -> list[str]:
    """The interpreter list the matrix can produce.

    The expression is a ternary over `github.event_name`, so both arms are
    read out of the literal rather than evaluated.
    """
    raw = str(_python_tests()["strategy"]["matrix"]["python-version"])
    return re.findall(r'"(\d+\.\d+[^"]*)"', raw)


def test_every_gated_step_names_a_matrix_value_that_exists() -> None:
    """A step gated on a value no cell produces never runs, and every one of
    these steps is a merge gate or a diagnostic — decorative either way
    (#1859). `matrix.group == K` with K outside the matrix, and
    `matrix.python-version == 'X'` with X outside the interpreter list, are
    both that failure.
    """
    groups = set(_matrix_groups())
    versions = set(_matrix_python_versions())
    assert versions, "could not read the python-version matrix"
    gated_groups, gated_versions = [], []
    for step in _python_tests()["steps"]:
        cond = step.get("if")
        if not cond:
            continue
        name = step.get("name") or step.get("uses")
        if m := re.fullmatch(r"\s*matrix\.group\s*==\s*(\d+)\s*", str(cond)):
            gated_groups.append((name, int(m.group(1))))
        elif m := re.fullmatch(r"\s*matrix\.python-version\s*==\s*'([^']+)'\s*", str(cond)):
            gated_versions.append((name, m.group(1)))
        else:
            raise AssertionError(f"unrecognised gate on step {name!r}: {cond!r}")
    assert gated_groups, "expected the per-checkout checks to be gated onto one shard"
    for name, k in gated_groups:
        assert k in groups, f"step {name!r} is gated on group {k}, which no shard runs"
    for name, v in gated_versions:
        assert v in versions, f"step {name!r} is gated on py{v}, which no cell runs"


def test_per_checkout_merge_gates_run_on_exactly_one_shard() -> None:
    """Each non-pytest check step runs once — not 4x, and not 0x."""
    names = [
        "Run Python linter",
        "Run mypy type-check (ADR-023 strict islands)",
        "ADR status/version-line consistency (#1501)",
        "Lockfile self-entry version sync (#1498)",
    ]
    steps = {s.get("name"): s for s in _python_tests()["steps"]}
    for name in names:
        assert name in steps, f"{name!r} step missing from python-tests"
        assert steps[name].get("if"), f"{name!r} would run on every shard"


def test_rust_cache_is_shared_across_shards_and_saved_by_one() -> None:
    steps = [s for s in _python_tests()["steps"] if "rust-cache" in str(s.get("uses"))]
    assert len(steps) == 1, steps
    with_ = steps[0].get("with") or {}
    assert "matrix.python-version" in str(with_.get("shared-key")), with_
    m = re.search(r"matrix\.group\s*==\s*(\d+)", str(with_.get("save-if")))
    assert m and int(m.group(1)) in set(_matrix_groups()), with_


def _splitting_algorithm() -> str:
    m = re.search(r"--splitting-algorithm\s+(\S+)", _pytest_command())
    assert m, (
        "the pytest invocation names no --splitting-algorithm. pytest-split's "
        "default, duration_based_chunks, cuts CONTIGUOUS runs and cannot "
        "separate two adjacent heavyweight files — it dealt one shard 204 "
        "tests and 278s (#2584)."
    )
    return m.group(1)


def test_split_uses_the_bin_packing_algorithm() -> None:
    """`least_duration` is the reason the shards balance at all.

    `duration_based_chunks` (the default) splits the collection into four
    contiguous runs, so a heavyweight file straddling a boundary pins two
    shards together: measured on the committed durations it deals
    280/278/328/193s (1.70x) where `least_duration` deals 270/270/270/270s
    (1.00x). The balance assertion below cannot distinguish the two on its
    own — it measures whatever algorithm this names — so the choice is
    pinned here.
    """
    assert _splitting_algorithm() == "least_duration", _pytest_command()


def test_shards_record_their_own_durations_for_upload() -> None:
    """CI measures the durations CI splits on (#2584).

    `--store-durations` alone MERGES into the file, leaving every id this
    shard did not run at its stale value; `--clean-durations` makes the
    written file hold exactly this shard's tests, which is what lets the four
    uploads union without ambiguity (scripts/merge-test-durations.py refuses
    a non-disjoint merge). Dropping either flag turns the artifacts back into
    four near-copies of the committed file, and the union silently becomes
    the local numbers again.
    """
    cmd = _pytest_command()
    assert "--store-durations" in cmd, cmd
    assert "--clean-durations" in cmd, cmd

    uploads = [
        s
        for s in _python_tests()["steps"]
        if "upload-artifact" in str(s.get("uses"))
        and "durations" in str((s.get("with") or {}).get("path", ""))
    ]
    assert len(uploads) == 1, f"expected exactly one durations upload step, got {uploads}"
    step = uploads[0]
    with_ = step.get("with") or {}
    # Ancillary to the gate: an upload hiccup must not fail a blocking job.
    assert step.get("continue-on-error") is True, step
    # The artifact name must vary by shard, or four uploads collide into one.
    assert "matrix.group" in str(with_.get("name", "")), step
    # `.test_durations` is a dotfile and upload-artifact drops hidden files
    # by default. Without this the step is GREEN, runs in 0s, and uploads
    # nothing — which is exactly how it first shipped, discovered only when
    # `make test-durations-from-ci` reported no matching artifact.
    assert with_.get("include-hidden-files") is True, (
        "the durations file is a dotfile; without include-hidden-files the "
        "upload silently produces no artifact and still reports success"
    )
    # The file is committed, so it is always present: `warn` could only ever
    # hide a broken path. Silence already caused this once.
    assert with_.get("if-no-files-found") == "error", with_


def test_durations_file_is_committed_and_the_invocation_points_at_it() -> None:
    cmd = _pytest_command()
    m = re.search(r"--durations-path\s+(\S+)", cmd)
    assert m, f"no --durations-path in {cmd}"
    assert (ROOT / m.group(1)) == DURATIONS, m.group(1)
    assert DURATIONS.is_file(), (
        ".test_durations is missing — CI would still pass (pytest-split "
        "balances by count without it) but every shard would be unbalanced. "
        "Run `make test-durations` and commit the file."
    )
    data = json.loads(DURATIONS.read_text())
    assert isinstance(data, dict) and data, "expected a non-empty {nodeid: seconds} map"
    bad = [(k, v) for k, v in data.items() if not isinstance(v, (int, float)) or v < 0]
    assert not bad, bad[:5]
    # Every recorded nodeid must live under one of the three CI roots; an
    # entry from some other path means the file was generated from a
    # different invocation than the one CI shards.
    roots = ("tests/", "python/tests/", "python/djust/tests/")
    foreign = [k for k in data if not k.startswith(roots)]
    assert not foreign, foreign[:5]


@pytest.mark.parametrize("job", ["test-summary"])
def test_aggregate_gate_still_ands_the_matrix_wide_python_result(job: str) -> None:
    """`needs.python-tests.result` is the matrix-wide result (success only if
    every cell succeeded), so one clause covers all shards — but it must be
    in the AND chain, not merely echoed (#1713)."""
    gate = _jobs()[job]
    assert "python-tests" in gate["needs"]
    run = "\n".join(s.get("run") or "" for s in gate["steps"])
    cond = re.search(r"if \[(.*?)\]; then", run, re.S)
    assert cond, "could not find the aggregate if-condition"
    assert 'needs.python-tests.result }}" == "success"' in cond.group(1)


# --------------------------------------------------------------------------- #
# staleness / balance (#2703)
# --------------------------------------------------------------------------- #
#
# The pin above proves `.test_durations` EXISTS, parses, and holds no foreign
# roots. None of that can fail when the file is merely OUT OF DATE — and that
# is the state that actually broke CI: 20% of collected tests had no recorded
# duration, so pytest-split count-balanced that fifth of the suite and dealt
# shard 3 **9909 tests against shard 2's 1898**. Shard 3/4 then died on all
# three interpreters, on five consecutive `main` runs, with
# `The runner has received a shutdown signal` and zero failing tests.
#
# A pin that cannot go red on the condition that broke production is
# decorative (#1859). These two can.

STALE_FRACTION_MAX = 0.10  # CONTRIBUTING: regenerate past ~10% shift
IMBALANCE_RATIO_MAX = 2.0  # largest shard vs smallest, by recorded time


def _validate_snapshot(snapshot: dict, data: dict[str, float], n: int) -> None:
    collected = snapshot["collected"]
    per_group = snapshot["groups"]
    assert collected, "collection produced no ids — the harness is broken, not the file"
    assert len(collected) == len(set(collected)), "full collection contains duplicate ids"
    assert len(per_group) == n, "snapshot does not contain every configured shard"
    flattened = [nodeid for group in per_group for nodeid in group]
    assert len(flattened) == len(set(flattened)), "shards assign a test more than once"
    assert set(flattened) == set(collected), "shards omit or invent collected tests"
    shared = set(snapshot.get("shared_corpus", []))
    assert shared <= set(collected), "shared corpus names uncollected tests"
    if shared:
        owners = [group for group in per_group if shared.intersection(group)]
        assert len(owners) == 1, "full corpus readers would repeat the sweep across shards"
    missing = [t for t in collected if t not in data]
    fraction = len(missing) / len(collected)
    assert fraction <= STALE_FRACTION_MAX, (
        f"{len(missing)} of {len(collected)} collected tests ({fraction:.0%}) have no "
        f"recorded duration, over the {STALE_FRACTION_MAX:.0%} threshold. "
        f"Run make test-durations-from-ci RUN=<run-id>. First missing: {missing[:3]}"
    )
    counts = [len(g) for g in per_group]
    assert all(counts), f"a shard would collect nothing: {counts}"
    default = (sum(data.values()) / len(data)) if data else 0.0
    times = [sum(data.get(t, default) for t in g) for g in per_group]
    lo, hi = min(times), max(times)
    assert lo > 0 and hi / lo <= IMBALANCE_RATIO_MAX, (
        f"pytest-split would deal these shards {counts} tests / "
        f"{[round(t) for t in times]}s — exceeds {IMBALANCE_RATIO_MAX}x balance limit. "
        "Run make test-durations-from-ci RUN=<run-id>."
    )


@pytest.mark.slow
def test_shards_cover_current_collection_with_balanced_durations(tmp_path: Path) -> None:
    """One full collection verifies staleness, coverage, and real plugin balance.

    The helper calls pytest-split's installed collection hook for every group;
    it does not copy the algorithm. Its small-corpus tests compare this probe
    against normal --splits/--group CLI invocations.
    """
    output = tmp_path / "shards.json"
    n = len(_matrix_groups())
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/collect-test-shards.py"),
            "--output",
            str(output),
            "--splits",
            str(n),
            "--splitting-algorithm",
            _splitting_algorithm(),
            "--durations-path",
            str(DURATIONS),
            "tests/",
            "python/tests/",
            "python/djust/tests/",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": "."},
        timeout=180,
    )
    assert proc.returncode == 0, (
        f"shard collection failed ({proc.returncode}):\n{proc.stdout[-8000:]}\n{proc.stderr[-8000:]}"
    )
    snapshot = json.loads(output.read_text())
    assert snapshot.get("shared_corpus"), "full sweep fixture was not recognized during collection"
    _validate_snapshot(snapshot, json.loads(DURATIONS.read_text()), n)
