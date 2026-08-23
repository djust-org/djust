"""Every Python test root stays in the gating CI invocation (#2032/#2034).

`python/djust/tests/` was once absent from CI's Python step. The cause is a
sharp edge worth restating: passing explicit paths to pytest **overrides**
`pyproject.toml`'s `testpaths`, so listing two of the three roots silently
drops the third — no error, no warning, and the suite still reports green.

A large suite ran in neither CI nor the pre-push hook that way, and a RED
`TestSetattrChokepoint` CWE-915 guard sat undetected on `main` (#2032). It was
restored as a separate soak step, then promoted to blocking (#2034).

The two steps were later merged into one invocation for wall-clock (split
93s + 43s = 136s at `-n 4`; merged 109s). That merge is exactly the kind of
edit that could re-drop a root — the paths become one list that someone might
"tidy" — so the roots are pinned here rather than trusted to a comment (#1859).

This asserts the CI invocation, not pytest's own discovery: a test that only
checked `testpaths` would pass while CI ran a subset, which is the original
bug.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/test.yml"

REQUIRED_ROOTS = ("tests/", "python/tests/", "python/djust/tests/")


def _gating_pytest_commands() -> list[str]:
    """Every `pytest` line in the python-tests job's run steps."""
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    assert "python-tests" in jobs, (
        "the python-tests job was renamed or removed — this guard now pins "
        "nothing and must be updated rather than left silently passing."
    )
    cmds: list[str] = []
    for step in jobs["python-tests"].get("steps", []):
        run = step.get("run") or ""
        for line in run.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue  # a comment mentioning pytest is not an invocation
            if re.search(r"\bpytest\b", stripped) and "-m pytest" in stripped:
                cmds.append(stripped)
    assert cmds, "no pytest invocation found in the python-tests job"
    return cmds


@pytest.mark.parametrize("root", REQUIRED_ROOTS)
def test_every_test_root_is_in_the_gating_ci_invocation(root: str) -> None:
    combined = " ".join(_gating_pytest_commands())
    assert root in combined, (
        f"{root} is not passed to pytest in the python-tests job.\n"
        f"Explicit paths OVERRIDE pyproject's `testpaths`, so this root is not "
        f"merely deprioritised — it does not run at all, and CI still reports "
        f"green. That is exactly #2032: a RED security-structural guard sat "
        f"undetected on main because this root was missing.\n"
        f"Invocation(s) found: {_gating_pytest_commands()}"
    )


def test_the_roots_run_under_one_invocation() -> None:
    """Merged, not split — the shape the wall-clock measurement chose.

    Not a style preference: two invocations each drain their own xdist worker
    pool, so end-of-run stragglers hold one worker while the rest idle. 136s
    split against 109s merged at `-n 4`.
    """
    cmds = _gating_pytest_commands()
    assert len(cmds) == 1, (
        f"expected ONE pytest invocation covering all roots, found {len(cmds)}. "
        f"Splitting them back out costs ~27s of wall-clock at 4 workers. If the "
        f"split is deliberate (a root that genuinely cannot share a session), "
        f"say so here and update this test.\n{cmds}"
    )


def test_the_gating_invocation_runs_in_parallel() -> None:
    """A serial run of 10,489 tests would dwarf everything else in the job."""
    cmd = _gating_pytest_commands()[0]
    assert re.search(r"-n\s+(auto|\d+)", cmd), (
        f"the gating pytest invocation lost its `-n auto`: {cmd}"
    )
