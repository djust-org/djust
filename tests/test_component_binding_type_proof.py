"""Protect the ADR-034 proof runner against false-positive acceptance."""

from pathlib import Path
import runpy
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = runpy.run_path(str(ROOT / "scripts/check-component-binding-types.py"))


def test_runtime_type_proof():
    result = subprocess.run(
        [sys.executable, str(ROOT / "tests/typing_component_bindings/positive.py")],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_exact_expected_locations_pass():
    locations = {("negative.py", 10), ("negative.py", 20)}
    RUNNER["check_diagnostics"]("test", locations, locations)


@pytest.mark.parametrize(
    "actual",
    [
        set(),
        {("negative.py", 11)},
        {("positive.py", 10)},
        {("negative.py", 10), ("prototype.py", 2)},
    ],
)
def test_missing_or_unexpected_diagnostics_fail(actual):
    with pytest.raises(RuntimeError):
        RUNNER["check_diagnostics"]("test", actual, {("negative.py", 10)})
