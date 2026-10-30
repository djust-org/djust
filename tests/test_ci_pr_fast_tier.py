"""PR CI runs the fast tier; main (and a `full-tests` PR) runs the slow tier too.

The python-tests shard step reads its marker expression from PYTEST_MARK. A
pull request without the `full-tests` label deselects `slow` (tests/slow_tier.py);
every other event keeps it, so post-merge main runs still cover the slow tier.
"""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/test.yml"


def _shard_step() -> dict:
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    (step,) = [s for s in jobs["python-tests"]["steps"] if "-m pytest" in (s.get("run") or "")]
    return step


def test_the_pytest_invocation_takes_its_marker_from_pytest_mark():
    run = _shard_step()["run"]
    assert '-m "$PYTEST_MARK"' in run, run
    assert "--dist loadfile" in run, run


def test_only_unlabelled_pull_requests_skip_the_slow_tier():
    expr = _shard_step()["env"]["PYTEST_MARK"]
    assert "github.event_name == 'pull_request'" in expr
    assert "'full-tests'" in expr, "a PR must be able to opt into the slow tier"
    pr_value, other_value = expr.split("&& '", 1)[1].split("' || '", 1)
    assert pr_value == "not nightly and not slow", pr_value
    assert other_value.rstrip("' }") == "not nightly", "main/nightly must keep the slow tier"
