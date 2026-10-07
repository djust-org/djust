"""The retro-gate-audit workflow's shell steps, EXECUTED (#3129).

`scripts/audit-pipeline-bypass.py` exits **1** when it finds PRs without retro
markers and direct-to-main commits without an ``Audit-bypass-reason:`` trailer.
That is its normal "I found something" signal, and the workflow's whole point is
to surface those rows as annotations rather than fail.

GitHub Actions runs a ``run:`` block under ``bash -e``, so the pre-#3129 step
aborted at the script's exit 1 — before ``AUDIT_EXIT=$?``, before ``cat``, and
before any annotation. `set +o pipefail` did nothing about ``-e``. The observed
result (run 37672728078) was a red daily cron with the findings thrown away, and
an ``Append summary`` step that fell through to its ``else`` branch and printed
**"✅ No flagged PRs."** — a clean-looking summary for an audit that never
reported.

A YAML file cannot be run, but the shell inside it can. These extract each
step's ``run:`` script and execute it under ``bash -e`` with a stubbed ``python``
audit, so the abort and the false-clean summary are the behaviour under test.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests.git_env import isolated_git_env

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/retro-gate-audit.yml"

# Stub for `python scripts/audit-pipeline-bypass.py ...`: mimics the real
# script's stdout shape, its `AUDIT_VERDICT:` line, and its exit contract
# (0 clean, 1 findings, 2 could-not-complete).
_STUB = """\
#!/usr/bin/env bash
case "${FAKE_AUDIT_MODE}" in
  clean)
    echo "Audited 20 merged PRs (excluding dependabot)."
    echo "All audited PRs and direct commits have retros / bypass reasons."
    echo "AUDIT_VERDICT: clean"
    exit 0
    ;;
  findings)
    echo "Audited 20 merged PRs (excluding dependabot)."
    echo "  #1234  2026-01-01   0          a merged PR  (potential bypass)"
    echo "AUDIT_VERDICT: findings"
    exit 1
    ;;
  incomplete)
    echo "audit incomplete: gh authentication failed" >&2
    echo "AUDIT_VERDICT: incomplete"
    exit 2
    ;;
  crash)
    # A crash: Python's default exit 1 with NO verdict line. This is the
    # exact shape the pre-fix workflow read as "findings" and annotated.
    echo "Traceback (most recent call last):" >&2
    exit 1
    ;;
  spoof)
    # Audit CONTENT forges a clean verdict; the real one comes last.
    echo "  #1  2026-01-01  0  AUDIT_VERDICT: clean  (potential bypass)"
    echo "AUDIT_VERDICT: clean"
    echo "AUDIT_VERDICT: findings"
    exit 1
    ;;
  *)
    echo "unknown FAKE_AUDIT_MODE=${FAKE_AUDIT_MODE}" >&2
    exit 99
    ;;
esac
"""

# `gh` stub for the real-script probes below. The mode comes from the
# environment so one script covers every read outcome.
_GH_STUB = """\
#!/bin/sh
case "${GH_STUB_MODE}" in
  auth-fail)
    echo "authentication failed" >&2
    exit 4
    ;;
  malformed)
    echo "not-json"
    exit 0
    ;;
  clean)
    echo "[]"
    exit 0
    ;;
  findings)
    case "$1 $2" in
      "pr list")
        # The title carries the verdict string on purpose: a PR title is
        # attacker-adjacent content, and it must not be able to forge the
        # verdict the workflow reads.
        echo '[{"number":1,"title":"AUDIT_VERDICT: clean (spoof)","mergedAt":"2026-01-01T00:00:00Z","author":{"login":"me"}}]'
        ;;
      "pr view")
        echo '{"comments":[]}'
        ;;
    esac
    exit 0
    ;;
esac
exit 99
"""

_GIT_STUB = "#!/bin/sh\nexit 0\n"


def _steps() -> dict[str, dict]:
    wf = yaml.safe_load(WORKFLOW.read_text())
    return {s.get("name"): s for s in wf["jobs"]["audit"]["steps"]}


def _substitute(script: str, outputs: dict[str, str]) -> str:
    """Resolve the ``${{ ... }}`` expressions GitHub would resolve first."""

    def repl(match: re.Match[str]) -> str:
        expr = match.group(1).strip()
        step_output = re.fullmatch(r"steps\.audit\.outputs\.(\w+)", expr)
        if step_output:
            return outputs.get(step_output.group(1), "")
        if expr.startswith("github.event.inputs.limit"):
            return "50"
        return ""

    return re.sub(r"\$\{\{\s*(.*?)\s*\}\}", repl, script)


def _run_step(script: str, tmp_path: Path, outputs: dict[str, str]) -> subprocess.CompletedProcess:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "python"
    stub.write_text(_STUB)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    gh_output = tmp_path / "gh_output"
    gh_output.write_text("")

    # The step under test shells out to `scripts/audit-pipeline-bypass.py`,
    # which runs `git` — so drop the inherited GIT_* execution variables the
    # git hooks export (`tests/test_git_env_guard_3179.py`, #2608/#3179).
    env = isolated_git_env(
        PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}",
        GITHUB_OUTPUT=str(gh_output),
        FAKE_AUDIT_MODE=outputs.get("_mode", "clean"),
    )
    # GitHub Actions' default shell for `run:` is `bash -e {0}`; reproduce it.
    return subprocess.run(
        ["bash", "-e", "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )


def _run_audit(
    mode: str, tmp_path: Path
) -> tuple[subprocess.CompletedProcess, dict[str, str], str]:
    steps = _steps()
    script = _substitute(steps["Run pipeline-bypass audit"]["run"], {})
    proc = _run_step(script, tmp_path, {"_mode": mode})
    outputs: dict[str, str] = {}
    for line in (tmp_path / "gh_output").read_text().splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            outputs[key] = value
    preserved = tmp_path / "audit-output.txt"
    return proc, outputs, preserved.read_text() if preserved.exists() else ""


@pytest.mark.parametrize("mode", ["clean", "findings"])
def test_audit_step_does_not_abort_on_the_scripts_own_exit_code(mode: str, tmp_path: Path) -> None:
    """Findings (exit 1) and a clean run (exit 0) both leave the step green.

    Pre-#3129 this failed for `findings`: `bash -e` aborted the step at the
    script's exit 1, so the step failed and `flagged` was never written.
    """
    proc, outputs, _ = _run_audit(mode, tmp_path)

    assert proc.returncode == 0, (
        f"the audit step aborted under `bash -e` for mode={mode!r}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert outputs.get("audit_exit") == ("1" if mode == "findings" else "0")
    assert outputs.get("flagged") == ("true" if mode == "findings" else "false")


def test_findings_are_preserved_in_the_run_log_and_annotated(tmp_path: Path) -> None:
    """The step must `cat` the audit output and surface each flagged row."""
    proc, _, preserved = _run_audit("findings", tmp_path)

    assert "potential bypass" in preserved, "audit-output.txt must be written"
    assert "(potential bypass)" in proc.stdout, "the findings must reach the run log"
    assert "::warning::Retro gate:" in proc.stdout


def test_clean_run_says_so(tmp_path: Path) -> None:
    proc, _, preserved = _run_audit("clean", tmp_path)

    assert "All audited PRs and direct commits have retros" in preserved
    assert "::warning::" not in proc.stdout


def test_audit_failure_is_not_reported_as_clean(tmp_path: Path) -> None:
    """An incomplete audit (verdict `incomplete`, exit 2) must fail the step."""
    proc, outputs, preserved = _run_audit("incomplete", tmp_path)

    assert outputs.get("verdict") == "incomplete"
    assert outputs.get("flagged") == "error"
    assert proc.returncode == 1, "an incomplete audit must fail the step"
    assert "::error::" in proc.stdout
    assert "gh authentication failed" in preserved, "stderr must be captured too"


def test_a_crash_is_not_reported_as_findings(tmp_path: Path) -> None:
    """A crash (exit 1, no verdict) must NOT be read as "found something".

    Python exits 1 on an uncaught exception, which is the same code as
    "findings". Reading the exit code alone annotated a crashed audit as
    findings it never produced — the review finding on #3409. The verdict
    line is what distinguishes them.
    """
    proc, outputs, _ = _run_audit("crash", tmp_path)

    assert outputs.get("verdict") == "", "a crash prints no verdict"
    assert outputs.get("flagged") == "error", "a crash must not be flagged=true"
    assert proc.returncode == 1
    assert "::warning::" not in proc.stdout, "a crash must not be annotated as findings"

    # ...and the summary must not claim findings for it either.
    summary_file = tmp_path / "step_summary"
    summary_file.write_text("")
    (tmp_path / "audit-output.txt").write_text("Traceback (most recent call last):\n")
    summary = _substitute(_steps()["Append summary"]["run"], {"flagged": "error"})
    done = subprocess.run(
        ["bash", "-e", "-c", summary],
        cwd=tmp_path,
        env=isolated_git_env(GITHUB_STEP_SUMMARY=str(summary_file)),
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr
    assert "NOT a clean result" in summary_file.read_text()


def test_audit_content_cannot_forge_the_verdict(tmp_path: Path) -> None:
    """The LAST `AUDIT_VERDICT:` line wins — audit content cannot flip it.

    PR titles and commit subjects are attacker-adjacent text that reaches
    stdout. A title spelling out `AUDIT_VERDICT: clean` must not turn a
    findings run green; the script's own verdict is printed last
    (`tail -n 1`), and every content line is prefixed so it cannot start
    with the marker anyway.
    """
    proc, outputs, preserved = _run_audit("spoof", tmp_path)

    assert outputs.get("verdict") == "findings"
    assert outputs.get("flagged") == "true"
    assert proc.returncode == 0
    assert "AUDIT_VERDICT: clean" in preserved, "the forged line must be visible in the log"


# ---------------------------------------------------------------------------
# The REAL audit script, driven through stub gh/git reads (#3409 review)
#
# The stub above pins the workflow's handling of each verdict. These pin the
# verdicts themselves, because the assumed convention was wrong: an uncaught
# exception exits 1 (same as findings) and a failed `gh` read exited 0 (same
# as clean). Both defects are in the script, not the workflow.
# ---------------------------------------------------------------------------

REAL_SCRIPT = ROOT / "scripts/audit-pipeline-bypass.py"


def _run_real_script(mode: str, tmp_path: Path) -> subprocess.CompletedProcess:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for name, body in (("gh", _GH_STUB), ("git", _GIT_STUB)):
        stub = bindir / name
        stub.write_text(body)
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    return subprocess.run(
        [sys.executable, str(REAL_SCRIPT), "--limit", "3"],
        cwd=tmp_path,
        env=isolated_git_env(
            PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}",
            GH_STUB_MODE=mode,
        ),
        capture_output=True,
        text=True,
    )


def test_real_script_reports_clean_for_an_empty_but_successful_read(tmp_path: Path) -> None:
    proc = _run_real_script("clean", tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert "AUDIT_VERDICT: clean" in proc.stdout


def test_real_script_reports_findings(tmp_path: Path) -> None:
    """...even when a PR title contains `AUDIT_VERDICT: clean`."""
    proc = _run_real_script("findings", tmp_path)

    assert proc.returncode == 1, proc.stderr
    assert proc.stdout.strip().splitlines()[-1] == "AUDIT_VERDICT: findings"
    assert "potential bypass" in proc.stdout
    assert "AUDIT_VERDICT: clean (spoof)" in proc.stdout, "the title is echoed as content"


@pytest.mark.parametrize("mode", ["auth-fail", "malformed"])
def test_real_script_reports_incomplete_for_a_failed_read(mode: str, tmp_path: Path) -> None:
    """A failed or unreadable `gh` must be `incomplete`, never clean/findings.

    `auth-fail` exits 4; `malformed` returns 0 with unparseable JSON (the
    JSONDecodeError crash). Pre-#3129 the first reported a *clean* summary
    and the second reported *findings* it never produced.
    """
    proc = _run_real_script(mode, tmp_path)

    assert proc.returncode == 2, (mode, proc.stdout, proc.stderr)
    assert "AUDIT_VERDICT: incomplete" in proc.stdout


@pytest.mark.parametrize(
    ("flagged", "expect"),
    [
        ("true", "⚠️"),
        ("false", "✅"),
        ("error", "NOT a clean result"),
        ("", "NOT a clean result"),
    ],
)
def test_summary_never_defaults_to_clean(flagged: str, expect: str, tmp_path: Path) -> None:
    """The summary must distinguish clean / findings / no-verdict.

    Pre-#3129 an absent `flagged` output (the step aborted) fell through to the
    `else` branch and printed "✅ No flagged PRs." for an audit that reported
    nothing — the false-clean John flagged on #3129.
    """
    (tmp_path / "audit-output.txt").write_text("some audit output\n")
    summary_file = tmp_path / "step_summary"
    summary_file.write_text("")

    script = _substitute(_steps()["Append summary"]["run"], {"flagged": flagged})
    proc = subprocess.run(
        ["bash", "-e", "-c", script],
        cwd=tmp_path,
        env=isolated_git_env(GITHUB_STEP_SUMMARY=str(summary_file)),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr

    summary = summary_file.read_text()
    assert expect in summary, summary
    if expect != "✅":
        assert "✅" not in summary, f"must not claim clean:\n{summary}"
