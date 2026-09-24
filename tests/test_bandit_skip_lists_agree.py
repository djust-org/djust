"""Every Bandit invocation skips the same reviewed rules, and only those.

The skip list is policy: a rule on it is invisible in shipped code, so a new
use of pickle, exec or a weak hash would pass unseen. The only reviewed skips
are the two mark_safe rules (B703, B308), which the component and theming code
uses by design. Intentional exceptions elsewhere carry an inline ``# nosec``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    tomllib = pytest.importorskip("tomli")

ROOT = Path(__file__).resolve().parents[1]
REVIEWED = {"B703", "B308"}


def _skip_ids(text: str) -> list[set[str]]:
    return [set(m.split(",")) for m in re.findall(r"-s[\"', ]+\s*\"?(B\d{3}(?:,B\d{3})*)", text)]


def test_pre_commit_hook_skips_only_reviewed_rules():
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())
    args = [
        hook["args"] for repo in config["repos"] for hook in repo["hooks"] if hook["id"] == "bandit"
    ]
    assert args, "bandit hook not found"
    for hook_args in args:
        skips = set(hook_args[hook_args.index("-s") + 1].split(","))
        assert skips == REVIEWED


def test_workflows_skip_only_reviewed_rules():
    found = []
    for name in ("pre-release-security-audit.yml", "test.yml"):
        text = (ROOT / ".github" / "workflows" / name).read_text()
        for skips in _skip_ids(text):
            found.append(name)
            assert skips == REVIEWED, f"{name}: {sorted(skips)}"
    assert "pre-release-security-audit.yml" in found and "test.yml" in found


def test_pyproject_bandit_skips_match():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert set(data["tool"]["bandit"]["skips"]) == REVIEWED
