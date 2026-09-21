"""Protect the ADR-034 proof runner against false-positive acceptance."""

import ast
from pathlib import Path
import runpy
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = runpy.run_path(str(ROOT / "scripts/check-component-binding-types.py"))


def test_runtime_type_proof():
    result = RUNNER["run"]([sys.executable, "runtime_check.py"])
    assert result.returncode == 0, result.stdout + result.stderr


def test_liveview_constructor_stub_matches_the_real_signature():
    signatures = []
    for suffix in ("py", "pyi"):
        module = ast.parse((ROOT / "python/djust" / f"live_view.{suffix}").read_text())
        view = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == "LiveView"
        )
        constructor = next(
            node
            for node in view.body
            if isinstance(node, ast.FunctionDef) and node.name == "__init__"
        )
        signatures.append((ast.dump(constructor.args), ast.dump(constructor.returns)))
    assert signatures[0] == signatures[1]


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
