"""JS coverage is its own job: parallel on main, skipped on unlabelled PRs.

The coverage pass re-runs the whole vitest suite against instrumented bundles
and enforces the coverage floors. `npm test` (shipped bundle) still gates every
PR; `javascript-coverage` runs on main pushes and on PRs labelled `full-tests`.
The test-summary gate accepts `skipped` for it on pull requests only — the
condition is evaluated with bash below, not string-matched.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/test.yml"


def _jobs() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]


def _runs(job: dict) -> str:
    return "\n".join(s.get("run") or "" for s in job["steps"])


def test_coverage_left_the_javascript_tests_job():
    jobs = _jobs()
    assert "npm test" in _runs(jobs["javascript-tests"])
    assert "test:coverage" not in _runs(jobs["javascript-tests"])
    assert "npm run test:coverage" in _runs(jobs["javascript-coverage"])


def test_coverage_job_runs_on_main_and_on_full_tests_prs():
    cond = _jobs()["javascript-coverage"]["if"]
    assert "github.event_name != 'pull_request'" in cond
    assert "'full-tests'" in cond


def _gate_verdict(coverage: str, event: str) -> bool:
    step = next(
        s for s in _jobs()["test-summary"]["steps"] if s.get("name") == "Check test results"
    )
    cond = re.search(r"if (\[.*?); then", step["run"], re.S).group(1)
    cond = cond.replace("${{ needs.javascript-coverage.result }}", coverage)
    cond = cond.replace("${{ github.event_name }}", event)
    cond = re.sub(r"\$\{\{ needs\.[\w-]+\.result \}\}", "success", cond)
    out = subprocess.run(
        ["bash", "-c", f"if {cond}; then echo PASS; else echo FAIL; fi"],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.strip() == "PASS"


def test_gate_requires_the_coverage_job():
    assert "javascript-coverage" in _jobs()["test-summary"]["needs"]


@pytest.mark.parametrize(
    ("coverage", "event", "passes"),
    [
        ("success", "push", True),
        ("success", "pull_request", True),
        ("skipped", "pull_request", True),
        ("skipped", "push", False),  # main must run it
        ("failure", "pull_request", False),
        ("failure", "push", False),
        ("cancelled", "push", False),
    ],
)
def test_gate_accepts_skipped_coverage_only_on_pull_requests(coverage, event, passes):
    assert _gate_verdict(coverage, event) is passes
