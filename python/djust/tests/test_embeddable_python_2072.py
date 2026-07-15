"""Self-test for ``scripts/embeddable-python.sh`` (closes #2072).

#2072: the pre-push ``cargo-test`` hook used to export
``PYO3_PYTHON="$(bash scripts/run-with-venv-python.sh --print)"`` — the
project venv's interpreter. When the venv's BASE interpreter is uv's
python-build-standalone (``~/.local/share/uv/python/cpython-*``), any PyO3
test binary that embeds CPython fails bootstrap deterministically:
``Fatal Python error: init_fs_encoding`` with the baked ``sys.prefix =
'/install'``. ``scripts/embeddable-python.sh`` resolves an interpreter safe
for embedding: the venv python if its ``sys.base_prefix`` is NOT a
python-build-standalone install, else a matching-version framework/homebrew
python, else the venv python anyway with a loud warning.

These tests run the REAL script via subprocess. Per the reproduction-fidelity
canon, we don't mock the interpreter-resolution logic — we execute the actual
shell script and inspect its real stdout/stderr against the actual machine
state.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "embeddable-python.sh"

SUBPROCESS_TIMEOUT = 30


def _run(extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=SUBPROCESS_TIMEOUT,
    )


def test_script_exists_and_executable() -> None:
    assert SCRIPT.exists(), f"{SCRIPT} missing"
    assert os.access(SCRIPT, os.X_OK), f"{SCRIPT} not executable"


def test_prints_some_executable_python_and_exits_0() -> None:
    """The load-bearing structural contract: the script always resolves to
    SOME interpreter (stdout is exactly one line, an executable path) and
    exits 0 — regardless of whether the venv itself was embeddable, a
    homebrew/framework python was substituted, or (last resort) the venv was
    used anyway with a warning."""
    result = _run()

    assert result.returncode == 0, (
        f"expected exit 0, got {result.returncode}. "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, f"expected exactly one stdout line, got {lines!r}"
    resolved = lines[0]
    assert os.path.isabs(resolved), f"resolved interpreter path not absolute: {resolved!r}"
    assert os.access(resolved, os.X_OK), f"resolved path {resolved!r} is not executable"

    # The resolved interpreter must actually be a working Python.
    probe = subprocess.run(
        [resolved, "-c", "print('embeddable-python-2072-ok')"],
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT,
    )
    assert probe.returncode == 0, f"resolved interpreter {resolved!r} failed to run: {probe.stderr}"
    assert "embeddable-python-2072-ok" in probe.stdout


def _venv_python() -> str | None:
    run_with_venv = REPO_ROOT / "scripts" / "run-with-venv-python.sh"
    if not run_with_venv.exists():
        return None
    result = subprocess.run(
        ["bash", str(run_with_venv), "--print"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=SUBPROCESS_TIMEOUT,
    )
    path = result.stdout.strip()
    return path if path and os.path.isfile(path) else None


def _base_prefix(python_path: str) -> str:
    result = subprocess.run(
        [python_path, "-c", "import sys; print(sys.base_prefix)"],
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT,
    )
    return result.stdout.strip()


def _is_uv_standalone(base_prefix: str) -> bool:
    return "/uv/python/" in base_prefix


def test_resolved_interpreter_base_prefix_is_not_uv_standalone_when_venv_is() -> None:
    """The empirical gate-off/gate-on pair for the #2072 fix (#1468/#1200):

    - Gate-on precondition: this dev machine's project venv genuinely has a
      python-build-standalone (uv-managed) ``sys.base_prefix`` — verified
      directly, not assumed.
    - The fix under test: when that precondition holds, the interpreter
      ``embeddable-python.sh`` resolves must NOT itself have a uv-standalone
      base_prefix — proving the script actually rejected the unsafe venv
      python rather than passing it through unchanged.

    If the venv is NOT uv-standalone-based on some future/other machine, this
    test is skipped (nothing to prove — the venv is already safe and (a) is
    the correct, unexercised branch)."""
    venv_python = _venv_python()
    if venv_python is None:
        pytest.skip("no project venv resolvable via run-with-venv-python.sh --print")

    venv_base_prefix = _base_prefix(venv_python)
    if not _is_uv_standalone(venv_base_prefix):
        pytest.skip(
            f"venv python {venv_python} is not uv-standalone-based "
            f"(base_prefix={venv_base_prefix!r}) — nothing to prove on this machine"
        )

    # Gate-on precondition confirmed: exercise the real script.
    result = _run()
    assert result.returncode == 0, f"stderr={result.stderr!r}"
    resolved = result.stdout.strip()

    assert resolved != venv_python, (
        f"embeddable-python.sh resolved the SAME uv-standalone venv python "
        f"({venv_python}) it should have rejected — the detection did nothing"
    )
    resolved_base_prefix = _base_prefix(resolved)
    assert not _is_uv_standalone(resolved_base_prefix), (
        f"resolved interpreter {resolved} still has a uv-standalone "
        f"base_prefix ({resolved_base_prefix!r}) — #2072 not actually fixed"
    )
    # Diagnostics must explain why, on stderr, mentioning the issue.
    assert "#2072" in result.stderr
    assert "uv" in result.stderr.lower()


def test_diagnostics_go_to_stderr_not_stdout() -> None:
    """Contract: stdout is reserved for exactly the resolved path (callers do
    ``PYO3_PYTHON="$(bash scripts/embeddable-python.sh)"``); all explanatory
    text goes to stderr."""
    result = _run()
    stdout_lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(stdout_lines) == 1, (
        f"stdout must contain exactly the resolved interpreter path, got: {result.stdout!r}"
    )
    assert "embeddable-python.sh:" not in result.stdout


def test_major_minor_matches_venv_when_venv_is_rejected() -> None:
    """Per #2072's resolution-order note: pyo3 needs interpreter version
    consistency with what built the cached artifacts, so when the venv is
    rejected, the substituted interpreter should prefer the SAME major.minor
    as the venv (a mismatched minor version forces cache rebuilds)."""
    venv_python = _venv_python()
    if venv_python is None:
        pytest.skip("no project venv resolvable via run-with-venv-python.sh --print")
    venv_base_prefix = _base_prefix(venv_python)
    if not _is_uv_standalone(venv_base_prefix):
        pytest.skip("venv is already embeddable — no substitution to check")

    def major_minor(python_path: str) -> str:
        result = subprocess.run(
            [python_path, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT,
        )
        return result.stdout.strip()

    venv_mm = major_minor(venv_python)
    result = _run()
    resolved = result.stdout.strip()
    if resolved == venv_python:
        pytest.skip("script fell back to the venv anyway (no alternative found on this machine)")
    resolved_mm = major_minor(resolved)
    assert resolved_mm == venv_mm, (
        f"resolved interpreter {resolved} is Python {resolved_mm}, venv is "
        f"Python {venv_mm} — expected a matching-version substitute to be "
        f"preferred (a homebrew python@{venv_mm} exists on this machine)"
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
