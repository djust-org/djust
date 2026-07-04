"""#2028 — pin already-shipped CHANGELOG sections against the newest release tag.

A stray branch merge can silently rewrite an already-shipped ``## [X.Y.Z]``
section (the v1.1.0rc5 consolidation incident). ``check-changelog-tagged-sections.py``
catches it by comparing every superseded section against the newest release
tag's frozen ``CHANGELOG.md`` snapshot.

These tests run against the REAL repo + tags (the check reads git tags from the
repo root), so the empirical canary (#1459) is a permanent regression: injecting
spurious content into a shipped section MUST make the check fail, and the
untouched tree MUST pass.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check-changelog-tagged-sections.py"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"


def _load_check():
    spec = importlib.util.spec_from_file_location("check_changelog_tagged_sections", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


check = _load_check()


def _in_git_repo_with_a_shipped_section() -> bool:
    """The check is a no-op outside a git work tree or before the first release
    tag exists — skip those environments rather than assert on a no-op."""
    if (
        subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(REPO_ROOT),
            capture_output=True,
        ).returncode
        != 0
    ):
        return False
    working = check._split_sections(CHANGELOG.read_text(encoding="utf-8"))
    return any(check._tag_exists(f"v{ver}") for ver, _ in working)


requires_shipped = pytest.mark.skipif(
    not _in_git_repo_with_a_shipped_section(),
    reason="no git work tree / no shipped CHANGELOG section tagged yet",
)


def _first_superseded_section() -> str:
    """The version of the first section BELOW the anchor that the anchor's
    snapshot contains — a genuinely-frozen section to mutate for the canary."""
    working = check._split_sections(CHANGELOG.read_text(encoding="utf-8"))
    anchor_i = next(i for i, (ver, _) in enumerate(working) if check._tag_exists(f"v{ver}"))
    anchor_ver = working[anchor_i][0]
    snap = dict(
        check._split_sections(
            subprocess.run(
                ["git", "show", f"v{anchor_ver}:CHANGELOG.md"],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
            ).stdout
        )
    )
    for ver, _ in working[anchor_i + 1 :]:
        if ver in snap:
            return ver
    raise AssertionError("no superseded section found to exercise the canary")


@requires_shipped
class TestChangelogTaggedSectionPin:
    def test_current_tree_passes(self):
        # The untouched working CHANGELOG must pin clean.
        assert check.check_changelog(CHANGELOG) == 0

    def test_rewriting_a_shipped_section_is_caught(self, tmp_path):
        # Empirical canary (#1459): inject a spurious bullet into a frozen
        # section in a COPY (real tags still back the comparison) → must fail.
        ver = _first_superseded_section()
        text = CHANGELOG.read_text(encoding="utf-8")
        heading = f"## [{ver}]"
        i = text.index(heading)
        nl = text.index("\n", i) + 1
        tampered = (
            text[:nl]
            + "\n- **SPURIOUS: unreleased content merged into a shipped section.**\n"
            + text[nl:]
        )
        tampered_path = tmp_path / "CHANGELOG.md"
        tampered_path.write_text(tampered, encoding="utf-8")

        assert check.check_changelog(tampered_path) == 1

    def test_gate_off_untampered_copy_passes(self, tmp_path):
        # Non-tautology guard: the SAME copy without the injection passes, so it
        # is the injection — not the copy path — that trips the check.
        copy = tmp_path / "CHANGELOG.md"
        copy.write_text(CHANGELOG.read_text(encoding="utf-8"), encoding="utf-8")
        assert check.check_changelog(copy) == 0
