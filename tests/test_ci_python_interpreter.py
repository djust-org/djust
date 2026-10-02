"""Regression coverage for the matrix that previously ran every cell on 3.12."""

import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_runtime_guard_accepts_actual_version_and_rejects_wrong_version():
    script = ROOT / "scripts/check-ci-python.py"
    version = ".".join(map(str, sys.version_info[:2]))
    good = subprocess.run([sys.executable, str(script), version], capture_output=True, text=True)
    assert good.returncode == 0
    assert "SOABI" in good.stdout
    bad = subprocess.run([sys.executable, str(script), "0.0"], capture_output=True, text=True)
    assert bad.returncode != 0
    assert "Interpreter mismatch" in bad.stderr


def test_matrix_selects_interpreter_and_checks_before_build():
    job = yaml.safe_load((ROOT / ".github/workflows/test.yml").read_text())["jobs"]["python-tests"]
    steps = job["steps"]
    assert not any(".venv" in str(step.get("with", {}).get("path", "")) for step in steps)
    build = next(
        step
        for step in steps
        if step.get("name") == "Install Python dependencies and build extension"
    )
    assert build["env"]["UV_PYTHON"] == "${{ steps.python.outputs.python-path }}"
    assert build["env"]["EXPECTED_PYTHON"] == "${{ matrix.python-version }}"
    setup = next(step for step in steps if "actions/setup-python@" in step.get("uses", ""))
    assert setup["id"] == "python"
    run = build["run"]
    assert '--python "$UV_PYTHON"' in run
    assert 'echo "UV_PYTHON=$UV_PYTHON" >> "$GITHUB_ENV"' in run
    assert run.index("check-ci-python.py") < run.index("maturin develop")
    assert "--interpreter .venv/bin/python" in run
    tools = next(step for step in steps if step.get("name") == "Install test tools")
    assert "uv pip install --python .venv/bin/python" in tools["run"]
    assert job["continue-on-error"] == "${{ matrix.python-version == '3.15' }}"
