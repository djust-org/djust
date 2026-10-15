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
