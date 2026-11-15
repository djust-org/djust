"""The test files that state ``exposure_policy = "legacy"`` match the ledger.

``tests/legacy_exposure_ledger.py`` records every test file that still states
the legacy exposure policy, and why (#4082). Every count here is derived from
the tree at run time; the ledger holds names, never totals.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.legacy_exposure_ledger import (
    MIGRATED_4082,
    OTHER_LEGACY_FILES,
    REASONED_INLINE,
    REMAINING_4082,
)

ROOT = Path(__file__).resolve().parents[2]
TEST_ROOTS = ("python/djust/tests", "python/tests", "tests")
#: A class body (``exposure_policy = "legacy"``) or a ``type(...)`` dict
#: (``"exposure_policy": "legacy"``) statement.
LEGACY = re.compile(
    r"""exposure_policy\s*=\s*["']legacy["']|["']exposure_policy["']\s*:\s*["']legacy["']"""
)
REASON = re.compile(r"#\s*(legacy subject:|gap #\d+:)")
#: The ledger and this test quote the statement; neither defines a view.
SELF = {"tests/legacy_exposure_ledger.py", "tests/unit/test_legacy_exposure_ledger.py"}


def _legacy_lines(path: str) -> list[str]:
    text = (ROOT / path).read_text(encoding="utf-8")
    return [line for line in text.splitlines() if LEGACY.search(line)]


def _tree_files_stating_legacy() -> set[str]:
    found = set()
    for top in TEST_ROOTS:
        for path in (ROOT / top).rglob("*.py"):
            rel = path.relative_to(ROOT).as_posix()
            if rel not in SELF and _legacy_lines(rel):
                found.add(rel)
    return found


def test_the_scan_finds_what_it_claims_to_find():
    """The pattern matches both statement forms and nothing else."""
    assert LEGACY.search('    exposure_policy = "legacy"')
    assert LEGACY.search("    exposure_policy='legacy'  # legacy subject: x")
    assert LEGACY.search('{"exposure_policy": "legacy", "template": "x"}')
    assert not LEGACY.search('    exposure_policy = "explicit"')
    assert REASON.search('exposure_policy = "legacy"  # legacy subject: why')
    assert REASON.search('exposure_policy = "legacy"  # gap #4149: why')
    assert not REASON.search('exposure_policy = "legacy"  # why')
    # The scan reaches every test root (a root it missed would read as clean).
    found = _tree_files_stating_legacy()
    for top in TEST_ROOTS:
        assert any(path.startswith(top + "/") for path in found), top


def test_every_file_stating_legacy_is_in_the_ledger_and_no_more():
    tree = _tree_files_stating_legacy()
    ledger = set(REMAINING_4082) | set(OTHER_LEGACY_FILES)
    started = sorted(tree - ledger)
    stopped = sorted(ledger - tree)
    assert not started, (
        "These test files state exposure_policy = 'legacy' but are not in "
        f"tests/legacy_exposure_ledger.py: {started}. A new test view states "
        "'explicit'; a test OF the legacy policy is added there with its reason."
    )
    assert not stopped, (
        "These ledger entries no longer state the legacy policy: "
        f"{stopped}. Remove them from tests/legacy_exposure_ledger.py "
        "(a #4082 file moves to MIGRATED_4082)."
    )


def test_migrated_4082_files_state_no_legacy_policy():
    for path in MIGRATED_4082:
        assert (ROOT / path).is_file(), path
        assert not _legacy_lines(path), f"{path} was migrated to explicit (#4082)"
    assert not set(MIGRATED_4082) & set(REMAINING_4082)
    assert not set(MIGRATED_4082) & set(OTHER_LEGACY_FILES)
    assert not set(REMAINING_4082) & set(OTHER_LEGACY_FILES)


def test_every_remaining_4082_file_has_a_reason():
    for path, reason in REMAINING_4082.items():
        assert reason.strip(), path


def test_kept_statements_carry_their_reason_inline():
    """In a file kept (wholly or partly) on purpose, each legacy statement says why."""
    for path in REASONED_INLINE:
        assert path in REMAINING_4082, path
        lines = _legacy_lines(path)
        assert lines, path
        bare = [line.strip() for line in lines if not REASON.search(line)]
        assert not bare, f"{path}: legacy statements without a reason: {bare}"
