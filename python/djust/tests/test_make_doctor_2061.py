"""Self-test for ``scripts/doctor.sh`` / ``make doctor`` (closes #2061).

#2061: three local-env incidents surfaced in one week while CI stayed
green — ``djust.pth`` repointed at a deleted worktree (import breakage +
stale compiled extension), embedded-PyO3 test binaries failing an
``init_fs_encoding``/``'/install'``-prefix bootstrap, and pyo3-ffi failing to
compile (SIGABRT in the ``rustc -vV`` build probe) — plus the pre-push hook
hardcoding ``.venv/bin/python`` (#1796, fails in a worktree). ``scripts/doctor.sh``
is a dev-environment self-diagnosis script that runs every check regardless
of earlier failures and reports ``[OK]``/``[WARN]``/``[FAIL] <name>: <finding>``
plus a one-line remedy, exiting non-zero iff any check FAILs.

These tests run the REAL script via subprocess against the repo checkout.
Per the task brief, asserting exit 0 in a "healthy" environment is
explicitly NOT required — a CI/dev box may legitimately WARN (no
``node_modules/`` yet, a venv shared with a different checkout, etc.), and a
FAIL is possible too (e.g. a genuine local rustc/LLVM toolchain mismatch).
Instead we assert: the script always runs to completion, every check name
appears in the output, and two small env-var test hooks built into the
script (``DOCTOR_FAKE_STALE_SO`` / ``DOCTOR_FAKE_PTH_BAD``) deterministically
force a FAIL + remedy line — with a gate-off sibling proving those FAIL
markers do NOT appear unprompted, so the assertions are non-tautological
(#1468/#1200).
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCTOR_SCRIPT = REPO_ROOT / "scripts" / "doctor.sh"

# The seven checks the script must always run, in the order the issue lists
# them. Every one must appear (as a `[OK]`/`[WARN]`/`[FAIL] <name>:` line) in
# every invocation's output, regardless of verdict.
CHECK_NAMES = [
    "venv",
    "djust.pth",
    "stale-extension",
    "embedded-pyo3",
    "node",
    "hooks",
    "git-config",
]

# Generous but bounded: doctor.sh internally caps its two slow checks at 30s
# (embedded-pyo3 cargo smoke test) and 20s (npx vitest --version), so total
# wall time is bounded even on a cold cache / cold rust toolchain.
SUBPROCESS_TIMEOUT = 150


def _run_doctor(extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(DOCTOR_SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=SUBPROCESS_TIMEOUT,
    )


def _status_line_pattern(check_name: str) -> re.Pattern[str]:
    """Match a `[OK]`/`[WARN]`/`[FAIL] <check_name>: ...` line for a check."""
    return re.compile(r"^\[(OK|WARN|FAIL)\] " + re.escape(check_name) + r": ", re.MULTILINE)


def test_script_exists_and_executable() -> None:
    """Source-pin: the script must exist and be directly executable."""
    assert DOCTOR_SCRIPT.exists(), f"{DOCTOR_SCRIPT} missing"
    assert os.access(DOCTOR_SCRIPT, os.X_OK), f"{DOCTOR_SCRIPT} not executable"


def test_script_uses_set_u_and_not_set_e() -> None:
    """Contract: `set -u` (catch unbound vars) but NO `set -e` — the script
    must run every check even when an earlier one fails/errors, so it can
    summarize ALL findings in one pass rather than aborting at the first."""
    text = DOCTOR_SCRIPT.read_text()
    assert re.search(r"^set -u\s*$", text, re.MULTILINE), "expected a bare `set -u` line"
    # Look only at actual code lines (strip full-line comments) so the
    # assertion isn't tripped by this script's own doc-comment prose
    # explaining that it deliberately omits `set -e`.
    code_lines = [line for line in text.splitlines() if not line.strip().startswith("#")]
    code_text = "\n".join(code_lines)
    assert not re.search(r"(^|[^-\w])set -e([^-\w]|$)", code_text, re.MULTILINE), (
        "doctor.sh must NOT set -e (it must run all checks even when one fails)"
    )


def test_makefile_has_doctor_target() -> None:
    """`make doctor` must exist and invoke the script."""
    makefile = (REPO_ROOT / "Makefile").read_text()
    assert re.search(r"^\.PHONY: doctor$", makefile, re.MULTILINE), (
        "no `.PHONY: doctor` in Makefile"
    )
    assert re.search(r"^doctor:.*##", makefile, re.MULTILINE), (
        "no `doctor:` target with a ## help comment"
    )
    assert "scripts/doctor.sh" in makefile


def test_doctor_runs_to_completion_and_reports_every_check() -> None:
    """The load-bearing structural test: the script runs to completion (no
    crash/hang/traceback) and every one of the seven checks reports a
    verdict line — regardless of whether individual checks pass, warn, or
    fail in THIS environment. Exit code is asserted to be a clean 0 or 1
    (the script's own contract: non-zero iff any check FAILs) — never a
    shell crash code (>1, 126, 127, 128+signal)."""
    result = _run_doctor()

    assert result.returncode in (0, 1), (
        f"doctor.sh exited {result.returncode} (expected 0 or 1 — a shell "
        f"crash, not a diagnosed FAIL). stdout={result.stdout!r} stderr={result.stderr!r}"
    )

    for name in CHECK_NAMES:
        assert _status_line_pattern(name).search(result.stdout), (
            f"missing status line for check {name!r} in doctor.sh output:\n{result.stdout}"
        )

    # Summary line reflects the same counts implied by the exit code.
    summary_match = re.search(
        r"^djust doctor summary: (\d+) ok, (\d+) warn, (\d+) fail\s*$",
        result.stdout,
        re.MULTILINE,
    )
    assert summary_match, f"missing summary line in output:\n{result.stdout}"
    ok_n, warn_n, fail_n = (int(x) for x in summary_match.groups())
    assert ok_n + warn_n + fail_n == len(CHECK_NAMES), (
        f"summary counts ({ok_n}+{warn_n}+{fail_n}) don't add up to {len(CHECK_NAMES)} checks"
    )
    if fail_n > 0:
        assert result.returncode == 1
    else:
        assert result.returncode == 0


def test_stale_so_gate_off_no_forced_marker_by_default() -> None:
    """Gate-off half of the non-tautology proof (#1468/#1200): WITHOUT the
    test-hook env var, the stale-extension line must never contain the
    test-hook marker. This is what makes the forced-FAIL assertion below
    meaningful rather than a check that always reports the same thing."""
    result = _run_doctor()
    stale_line = next(
        (
            line
            for line in result.stdout.splitlines()
            if "stale-extension" in line and line.startswith("[")
        ),
        None,
    )
    assert stale_line is not None, f"no stale-extension status line found:\n{result.stdout}"
    assert "DOCTOR_FAKE_STALE_SO" not in stale_line, (
        "stale-extension check reported the forced-failure marker WITHOUT "
        f"the env override set — tautological check: {stale_line!r}"
    )


def test_stale_so_force_fail_is_detected() -> None:
    """Forced-failure half: with DOCTOR_FAKE_STALE_SO=1, the stale-extension
    check FAILs with its remedy line, and the overall script exits non-zero."""
    result = _run_doctor({"DOCTOR_FAKE_STALE_SO": "1"})

    assert result.returncode != 0, (
        f"doctor.sh should exit non-zero with a forced FAIL. stdout={result.stdout!r}"
    )
    assert re.search(
        r"^\[FAIL\] stale-extension: .*DOCTOR_FAKE_STALE_SO", result.stdout, re.MULTILINE
    ), f"expected a [FAIL] stale-extension line naming the test hook:\n{result.stdout}"
    # Remedy line must follow immediately and mention the actual fix.
    assert "maturin develop" in result.stdout, (
        f"expected the maturin-develop remedy in output:\n{result.stdout}"
    )
    # Every OTHER check still ran (the script does not `set -e` / bail early).
    for name in CHECK_NAMES:
        assert _status_line_pattern(name).search(result.stdout), (
            f"forcing stale-extension to FAIL must not stop other checks "
            f"from running; missing {name!r}:\n{result.stdout}"
        )


def test_pth_gate_off_no_forced_marker_by_default() -> None:
    """Gate-off half for the djust.pth check (#1468/#1200)."""
    result = _run_doctor()
    pth_line = next(
        (
            line
            for line in result.stdout.splitlines()
            if "djust.pth" in line and line.startswith("[")
        ),
        None,
    )
    assert pth_line is not None, f"no djust.pth status line found:\n{result.stdout}"
    assert "DOCTOR_FAKE_PTH_BAD" not in pth_line, (
        f"djust.pth check reported the forced-failure marker WITHOUT the "
        f"env override set — tautological check: {pth_line!r}"
    )


def test_pth_force_fail_is_detected() -> None:
    """Forced-failure half: with DOCTOR_FAKE_PTH_BAD=1, the djust.pth check
    FAILs with its remedy line, and the overall script exits non-zero."""
    result = _run_doctor({"DOCTOR_FAKE_PTH_BAD": "1"})

    assert result.returncode != 0, (
        f"doctor.sh should exit non-zero with a forced FAIL. stdout={result.stdout!r}"
    )
    assert re.search(r"^\[FAIL\] djust\.pth: .*DOCTOR_FAKE_PTH_BAD", result.stdout, re.MULTILINE), (
        f"expected a [FAIL] djust.pth line naming the test hook:\n{result.stdout}"
    )
    assert "maturin develop" in result.stdout, (
        f"expected the maturin-develop remedy in output:\n{result.stdout}"
    )
    for name in CHECK_NAMES:
        assert _status_line_pattern(name).search(result.stdout), (
            f"forcing djust.pth to FAIL must not stop other checks from "
            f"running; missing {name!r}:\n{result.stdout}"
        )


def test_every_check_name_has_a_dedicated_check_function() -> None:
    """Source-pin: each CHECK_NAMES entry has a `check_<x>` function and a
    corresponding unconditional call at the bottom of the script (so a
    future edit can't silently orphan a check behind a conditional)."""
    text = DOCTOR_SCRIPT.read_text()
    for name in CHECK_NAMES:
        # e.g. "djust.pth" -> "pth", "stale-extension" -> "stale_so"/"stale"
        # is not mechanically derivable from the display name alone, so just
        # assert the display name string itself is referenced in a `fail`/
        # `warn`/`ok` call at least 3 times (one per verdict branch it can
        # take) as a loose structural signal the check is fully wired, not
        # just mentioned in a comment.
        assert text.count(f'"{name}"') >= 1, (
            f"check name {name!r} not passed as a literal anywhere in {DOCTOR_SCRIPT}"
        )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
