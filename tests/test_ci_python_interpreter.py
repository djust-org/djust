"""Regression coverage for the matrix that previously ran every cell on 3.12."""

import shlex
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


def _jobs():
    return yaml.safe_load((ROOT / ".github/workflows/test.yml").read_text())["jobs"]


def _step(job, name):
    return next(step for step in job["steps"] if step.get("name") == name)


def _assert_interpreter_selected_and_checked(job, step_name, then):
    """setup-python's path -> uv sync -> check-ci-python.py -> `then`."""
    steps = job["steps"]
    assert not any(".venv" in str(step.get("with", {}).get("path", "")) for step in steps)
    step = _step(job, step_name)
    assert step["env"]["UV_PYTHON"] == "${{ steps.python.outputs.python-path }}"
    assert step["env"]["EXPECTED_PYTHON"] == "${{ matrix.python-version }}"
    setup = next(s for s in steps if "actions/setup-python@" in s.get("uses", ""))
    assert setup["id"] == "python"
    run = step["run"]
    assert '--python "$UV_PYTHON"' in run
    assert 'export UV_PYTHON="$PWD/.venv/bin/python"' in run
    assert run.index("check-ci-python.py") < run.index("export UV_PYTHON") < run.index(then)
    return run


def test_matrix_selects_interpreter_and_checks_before_build():
    """The wheel is built for the interpreter the cell names, verified first."""
    job = _jobs()["python-build"]
    _assert_interpreter_selected_and_checked(
        job, "Install Python dependencies and build extension", "maturin build"
    )
    assert job["continue-on-error"] == "${{ matrix.python-version == '3.15' }}"


def test_shards_check_the_interpreter_before_installing_the_shared_build():
    """A shard verifies its interpreter, then installs the wheel built for it.

    The install script's own import check catches an ABI mismatch, but only
    after the venv exists; the interpreter check names the cause.
    """
    job = _jobs()["python-tests"]
    run = _assert_interpreter_selected_and_checked(
        job, "Install Python dependencies and prebuilt extension", "ci-install-prebuilt-djust.sh"
    )
    assert 'echo "UV_PYTHON=$UV_PYTHON" >> "$GITHUB_ENV"' in run
    tools = _step(job, "Install test tools")
    assert "uv pip install --python .venv/bin/python" in tools["run"]
    assert job["continue-on-error"] == "${{ matrix.python-version == '3.15' }}"


def test_maturin_build_command_uses_supported_cli_arguments():
    """Exercise the installed CLI parser, not a string pin on an invalid flag."""
    build = _step(_jobs()["python-build"], "Install Python dependencies and build extension")
    command = next(
        line.strip()
        for line in build["run"].splitlines()
        if line.strip().startswith("uv run --no-sync maturin ")
    )
    arguments = shlex.split(command)[4:]
    result = subprocess.run(
        [sys.executable, "-m", "maturin", *arguments, "--help"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
