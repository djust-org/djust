"""The Playwright harness installs python-build's wheel; it never compiles djust.

`.github/actions/djust-playwright-server` used to compile the extension
TWICE per job, with no Rust cache: `uv sync --extra dev` built the project
(~2m), then `maturin develop --release` built it again (~1.5m) — ~3.8 min of
setup for ~0.4 min of tests, in each of four jobs. Each of these pins is one
way that build comes back without anything turning red:

- a `uv sync` without `--no-install-project` builds the project;
- a bare `uv run` re-syncs the env first, which builds the project too;
- a job that uses the harness without `needs: python-build` races the
  artifact and fails to download it — or, worse, someone "fixes" that by
  putting the build back.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / ".github/actions/djust-playwright-server/action.yml"
WORKFLOW = ROOT / ".github/workflows/test.yml"
HARNESS = "./.github/actions/djust-playwright-server"


def _action_runs() -> list[str]:
    steps = yaml.safe_load(ACTION.read_text())["runs"]["steps"]
    return [
        "\n".join(
            ln for ln in str(s.get("run", "")).splitlines() if not ln.lstrip().startswith("#")
        )
        for s in steps
    ]


def test_the_harness_compiles_nothing() -> None:
    runs = "\n".join(_action_runs())
    assert "maturin" not in runs, "the harness must install python-build's wheel, not build"
    assert "ci-install-prebuilt-djust.sh" in runs
    syncs = re.findall(r"^\s*uv sync\b.*$", runs, re.M)
    assert syncs, "no uv sync in the harness"
    for line in syncs:
        assert "--no-install-project" in line, line


def test_every_uv_run_in_the_harness_skips_the_resync() -> None:
    bare = [
        ln.strip()
        for ln in "\n".join(_action_runs()).splitlines()
        if re.search(r"\buv run\b", ln) and "--no-sync" not in ln
    ]
    assert not bare, f"`uv run` without --no-sync re-syncs and builds the project: {bare}"


def test_every_job_using_the_harness_needs_the_build() -> None:
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    users = {
        name: job
        for name, job in jobs.items()
        if any(s.get("uses") == HARNESS for s in job.get("steps", []))
    }
    assert set(users) >= {"playwright-tests", "browser-smoke", "nav-hooks-guard", "exposure-matrix"}
    for name, job in users.items():
        needs = job.get("needs")
        needs = [needs] if isinstance(needs, str) else list(needs or [])
        assert "python-build" in needs, f"{name} uses the harness without needs: python-build"
