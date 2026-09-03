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
import re
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


def test_every_group_gated_step_names_a_group_that_exists() -> None:
    """`if: matrix.group == K` with K outside the matrix never runs anywhere."""
    groups = set(_matrix_groups())
    gated = []
    for step in _python_tests()["steps"]:
        cond = step.get("if")
        if not cond:
            continue
        m = re.fullmatch(r"\s*matrix\.group\s*==\s*(\d+)\s*", str(cond))
        assert m, f"unrecognised shard gate on step {step.get('name')!r}: {cond!r}"
        gated.append((step.get("name") or step.get("uses"), int(m.group(1))))
    assert gated, "expected the per-checkout checks to be gated onto one shard"
    for name, k in gated:
        assert k in groups, f"step {name!r} is gated on group {k}, which no shard runs"


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
