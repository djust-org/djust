"""Keep the release security checklist's commands and paths actionable."""

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
TEMPLATE = REPO / ".github" / "SECURITY_AUDIT_TEMPLATE.md"


def test_backticked_make_targets_exist():
    snippets = re.findall(r"`([^`]+)`", TEMPLATE.read_text())
    targets = re.findall(r"^make ([A-Za-z0-9_.-]+)$", "\n".join(snippets), re.MULTILINE)
    assert targets
    declared = set(
        re.findall(r"^([A-Za-z0-9_.-]+):", (REPO / "Makefile").read_text(), re.MULTILINE)
    )
    assert set(targets) <= declared


def test_backticked_paths_exist():
    snippets = re.findall(r"`([^`]+)`", TEMPLATE.read_text())
    paths = [snippet for snippet in snippets if "/" in snippet and " " not in snippet]
    assert paths
    assert all((REPO / path).exists() for path in paths)


def test_unfinished_audit_items_are_carried_forward():
    text = TEMPLATE.read_text()
    section = text.split("## 0. Carried forward from earlier audits", 1)[1].split(
        "## 1. Automated Scans", 1
    )[0]
    items = [line for line in section.splitlines() if line.startswith("- [ ]")]
    assert len(items) == 8
    assert all("#3129" in item for item in items)
    for item in (
        "§2 raw HTML",
        "§2 dynamic attributes",
        "§2 escaping coverage",
        "§2 per-handler permissioning",
        "§3 regression evidence",
        "§4 penetration scenarios",
        "§5 deployment configuration",
        "§7 sign-off",
    ):
        assert any(item in line for line in items)
    assert "only in the PR that completes it" in section
