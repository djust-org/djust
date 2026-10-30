"""Every push to main gets a complete Tests run.

With `group: workflow-ref` + `cancel-in-progress: true`, back-to-back merges to
main cancelled each other's post-merge runs (11 of 20 on 2026-10-21/22), so a
red main could not be bisected. The group must be per-commit on main and stay
per-ref elsewhere (superseded PR/branch revisions should still cancel).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/test.yml"


def _concurrency() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())["concurrency"]


def test_main_pushes_each_get_their_own_group() -> None:
    group = re.sub(r"\s+", " ", _concurrency()["group"])
    assert "github.ref == 'refs/heads/main' && github.sha" in group, (
        f"main pushes must be grouped per commit (github.sha), got {group!r} — "
        "otherwise a newer merge cancels the previous merge's post-merge run"
    )


def test_branches_and_prs_still_cancel_superseded_runs() -> None:
    conc = _concurrency()
    group = re.sub(r"\s+", " ", conc["group"])
    assert conc["cancel-in-progress"] is True
    assert group.rstrip("} ").endswith("|| github.ref"), (
        f"non-main refs must stay grouped by ref so a PR's stale run is cancelled, got {group!r}"
    )
