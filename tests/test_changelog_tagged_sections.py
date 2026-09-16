"""#2028 — pin already-shipped CHANGELOG sections against the newest release tag.

A stray branch merge can silently rewrite an already-shipped ``## [X.Y.Z]``
section (the v1.1.0rc5 consolidation incident). ``check-changelog-tagged-sections.py``
catches it by comparing every shipped section against the newest release
tag's frozen ``CHANGELOG.md`` snapshot.

#2854 extends the check with *absence* detection: a release tag whose version
has NO section used to be invisible (the gate silently fell back to the
previous tag — v1.1.3 shipped to PyPI that way).

#2862 extends it with *deletion* detection: the #2028 pin iterated only the
sections the working tree still has, so a shipped section deleted from the
tree was never compared and its removal exited 0. The fix iterates the union
of the tree's sections and the anchor snapshot's sections.

Some tests run against the REAL repo + tags (the check reads git tags from the
repo root), so the empirical canary (#1459) is a permanent regression: injecting
spurious content into a shipped section MUST make the check fail, and the
untouched tree MUST pass. Tag-set scenarios that the real repo cannot exhibit
run in throwaway git repos with the script installed under ``scripts/``.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
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


@requires_shipped
@pytest.mark.parametrize("target", ["heading", "body"])
def test_newest_shipped_section_is_frozen(tmp_path, target):
    sections = check._split_sections(CHANGELOG.read_text(encoding="utf-8"))
    newest = next(ver for ver, _ in sections if check._tag_exists(f"v{ver}"))
    text = CHANGELOG.read_text(encoding="utf-8")
    heading = f"## [{newest}]"
    if target == "heading":
        text = text.replace(heading, heading + " CORRUPTED", 1)
    else:
        pos = text.index("\n", text.index(heading)) + 1
        text = text[:pos] + "\n- CORRUPTED shipped content.\n" + text[pos:]
    copy = tmp_path / "CHANGELOG.md"
    copy.write_text(text, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(copy)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert f"Section '## [{newest}]' was rewritten" in result.stderr


@requires_shipped
def test_missing_newest_section_is_caught(tmp_path):
    """#2854 replay on the real repo: delete the anchor's own section from a
    copy. Before the fix the gate fell back to the previous tag and reported
    OK (the exact way v1.1.3 shipped); now it must fail naming the tag."""
    sections = check._split_sections(CHANGELOG.read_text(encoding="utf-8"))
    anchor = next(ver for ver, _ in sections if check._tag_exists(f"v{ver}"))
    text = CHANGELOG.read_text(encoding="utf-8")
    start = text.index(f"## [{anchor}]")
    end = text.find("## [", start + 1)
    text = text[:start] + (text[end:] if end != -1 else "")
    copy = tmp_path / "CHANGELOG.md"
    copy.write_text(text, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(copy)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert f"no '## [{anchor}]' section" in result.stderr


@requires_shipped
def test_deleting_a_superseded_section_is_caught(tmp_path):
    """#2862 replay on the real repo: delete a genuinely-frozen superseded
    section from a copy (real tags still back the comparison). Before the fix
    the pin iterated only the tree's own sections, so this exited 0 with
    'OK: N-1 shipped section(s)' — the exact v1.2.0rc6 deletion reproduction.
    Now it must fail naming the section as missing."""
    ver = _first_superseded_section()
    text = CHANGELOG.read_text(encoding="utf-8")
    start = text.index(f"## [{ver}]")
    end = text.find("## [", start + 1)
    deleted = text[:start] + (text[end:] if end != -1 else "")
    copy = tmp_path / "CHANGELOG.md"
    copy.write_text(deleted, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(copy)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert f"## [{ver}]' shipped" in result.stderr
    assert "missing from the" in result.stderr


class TestParseVersion:
    """The absence check sorts tags, so the pre-release order must be right:
    ``1.2.0rc7 < 1.2.0`` and ``1.1.2 < 1.1.3``."""

    def test_final_release_outranks_its_own_rcs(self):
        assert check._parse_version("1.2.0rc7") < check._parse_version("1.2.0")

    def test_rc_numbers_sort_numerically(self):
        assert check._parse_version("1.2.0rc2") < check._parse_version("1.2.0rc7")

    def test_patch_bump_sorts_above(self):
        assert check._parse_version("1.1.2") < check._parse_version("1.1.3")

    def test_alpha_beta_rc_final_order(self):
        keys = [check._parse_version(v) for v in ("0.2.0a1", "0.2.0b1", "0.2.0rc1", "0.2.0")]
        assert keys == sorted(keys)

    def test_non_release_tag_is_not_a_version(self):
        assert check._parse_version("not-a-version") is None
        assert check._parse_version("v") is None


class _TempRepo:
    """A throwaway git repo with the check script installed, so tag-set
    scenarios the real repo cannot exhibit (a sectionless release tag) can be
    built without touching the real repo's tags."""

    def __init__(self, root: Path):
        self.root = root
        (root / "scripts").mkdir(parents=True)
        shutil.copy2(SCRIPT, root / "scripts" / SCRIPT.name)
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "test")
        self.set_sections([])

    def _git(self, *args: str) -> None:
        subprocess.run(["git", *args], cwd=str(self.root), check=True, capture_output=True)

    def set_sections(self, versions: "list[str]") -> None:
        """Rewrite CHANGELOG.md with one section per version and commit."""
        text = "# Changelog\n\n## [Unreleased]\n\n" + "".join(
            f"## [{v}] - 2026-09-15\n\n- Section {v}.\n\n" for v in versions
        )
        (self.root / "CHANGELOG.md").write_text(text, encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-q", "--allow-empty", "-m", "changelog")

    def tag(self, name: str) -> None:
        self._git("tag", "-a", name, "-m", name)

    def tag_on_side_branch(self, name: str) -> None:
        """Tag a commit that is NOT an ancestor of main (the multi-branch
        case: another release line's tags must not demand sections here)."""
        self._git("checkout", "-q", "-b", "side")
        self._git("commit", "-q", "--allow-empty", "-m", "side work")
        self._git("tag", "-a", name, "-m", name)
        self._git("checkout", "-q", "main")

    def run_check(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.root / "scripts" / SCRIPT.name)],
            capture_output=True,
            text=True,
            cwd=str(self.root),
        )


class TestMissingSectionForHigherTag:
    def test_higher_tag_without_section_fails(self, tmp_path):
        # The v1.1.3 shape: tag cut, section never written. The gate used to
        # silently fall back to the previous tag and report OK (#2854).
        repo = _TempRepo(tmp_path / "repo")
        repo.set_sections(["1.1.1", "1.1.2"])
        repo.tag("v1.1.1")
        repo.tag("v1.1.2")
        repo.tag("v1.1.3")
        result = repo.run_check()
        assert result.returncode == 1
        assert "no '## [1.1.3]' section" in result.stderr

    def test_higher_tag_with_section_passes(self, tmp_path):
        repo = _TempRepo(tmp_path / "repo")
        repo.set_sections(["1.1.1", "1.1.2", "1.1.3"])
        repo.tag("v1.1.1")
        repo.tag("v1.1.2")
        repo.tag("v1.1.3")
        assert repo.run_check().returncode == 0

    def test_final_release_tag_above_rc_anchor_needs_section(self, tmp_path):
        # rc ordering: a v1.2.0 final tag ranks ABOVE a 1.2.0rc7 anchor, so it
        # demands a [1.2.0] section even though 1.2.0rc7 is sectioned.
        repo = _TempRepo(tmp_path / "repo")
        repo.set_sections(["1.2.0rc7"])
        repo.tag("v1.2.0rc7")
        repo.tag("v1.2.0")
        result = repo.run_check()
        assert result.returncode == 1
        assert "no '## [1.2.0]' section" in result.stderr

        # Writing the section turns the same tag set green again.
        repo.set_sections(["1.2.0", "1.2.0rc7"])
        assert repo.run_check().returncode == 0

    def test_unreachable_higher_tag_is_not_demanded(self, tmp_path):
        # Multi-branch guard: main never carried the 1.1.x maintenance
        # sections and the 1.1 branch never carried main's rc sections — a
        # tag from another release line must not fail this branch's check.
        repo = _TempRepo(tmp_path / "repo")
        repo.set_sections(["1.1.1", "1.1.2"])
        repo.tag("v1.1.1")
        repo.tag("v1.1.2")
        repo.tag_on_side_branch("v1.1.3")
        assert repo.run_check().returncode == 0

    def test_non_release_tag_is_ignored(self, tmp_path):
        repo = _TempRepo(tmp_path / "repo")
        repo.set_sections(["1.1.1"])
        repo.tag("v1.1.1")
        repo.tag("vnot-a-version")
        assert repo.run_check().returncode == 0


class TestDeletedShippedSection:
    """#2862 — a shipped section deleted from the tree must fail the pin.

    The #2028 loop iterated only the working tree's own sections, so a
    deletion was never compared (exit 0, count N-1). The fix iterates the
    anchor snapshot's sections too, so *deletion* and *rewrite* are two
    symptoms of one union comparison. The anchor-itself case is different:
    there the pin falls back to the next tag and the #2854 absence check is
    what fires — pinned here so the mechanism split stays explicit.
    """

    def test_deleting_a_superseded_section_fails(self, tmp_path):
        # [1.1.1] shipped before the v1.1.2 anchor; deleting it from the tree
        # used to leave nothing for the pin to compare against.
        repo = _TempRepo(tmp_path / "repo")
        repo.set_sections(["1.1.1", "1.1.2"])
        repo.tag("v1.1.1")
        repo.tag("v1.1.2")
        repo.set_sections(["1.1.2"])  # delete [1.1.1] below the anchor
        result = repo.run_check()
        assert result.returncode == 1
        assert "## [1.1.1]' shipped" in result.stderr
        assert "missing from the" in result.stderr

    def test_deleting_the_anchor_section_fails(self, tmp_path):
        # The anchor deleted: the pin falls back to v1.1.1 and the #2854
        # absence check demands the deleted anchor's section by name.
        repo = _TempRepo(tmp_path / "repo")
        repo.set_sections(["1.1.1", "1.1.2"])
        repo.tag("v1.1.1")
        repo.tag("v1.1.2")
        repo.set_sections(["1.1.1"])  # delete the anchor [1.1.2] itself
        result = repo.run_check()
        assert result.returncode == 1
        assert "no '## [1.1.2]' section" in result.stderr

    def test_edit_above_the_anchor_still_passes(self, tmp_path):
        # Legitimate work above the anchor must stay green: content in
        # [Unreleased] and a brand-new not-yet-tagged section.
        repo = _TempRepo(tmp_path / "repo")
        repo.set_sections(["1.1.1", "1.1.2"])
        repo.tag("v1.1.1")
        repo.tag("v1.1.2")
        text = (repo.root / "CHANGELOG.md").read_text(encoding="utf-8")
        text = text.replace(
            "## [Unreleased]\n",
            "## [Unreleased]\n\n- New unreleased work.\n",
            1,
        )
        text = text.replace(
            "## [1.1.2]",
            "## [1.1.3] - 2026-09-15\n\n- Untagged new section.\n\n## [1.1.2]",
            1,
        )
        (repo.root / "CHANGELOG.md").write_text(text, encoding="utf-8")
        repo._git("add", "-A")
        repo._git("commit", "-q", "--allow-empty", "-m", "changelog")
        assert repo.run_check().returncode == 0
