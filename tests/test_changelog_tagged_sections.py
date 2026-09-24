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

#2865 extends it with *wipe* detection: with every tagged section gone the
tree yields no anchor and the check used to exit 0 — indistinguishable from
a fresh, pre-first-release repo, which must keep passing. Release tags
reachable from HEAD disambiguate: some reachable + no anchored section is a
wipe (fail, naming the newest tag as the restore source); none reachable
stays the fresh-repo pass.

Some tests run against the REAL repo + tags (the check reads git tags from the
repo root), so the empirical canary (#1459) is a permanent regression: injecting
spurious content into a shipped section MUST make the check fail, and the
untouched tree MUST pass. Tag-set scenarios that the real repo cannot exhibit
run in throwaway git repos with the script installed under ``scripts/``.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.git_env import isolated_git_env


@pytest.fixture(autouse=True)
def _no_inherited_git_env(monkeypatch):
    """The code under test runs git in temp repos. Under a git hook an
    inherited GIT_DIR would aim those commands at the real repository (#2608)."""
    from tests.git_env import GIT_EXECUTION_VARS

    for var in GIT_EXECUTION_VARS:
        monkeypatch.delenv(var, raising=False)


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


def _heading_pos(text: str, ver: str) -> int:
    """Offset of the ``## [<ver>]`` HEADING — anchored to the line start.

    A plain ``text.index(f"## [{ver}]")`` also matches a heading *quoted
    inside another section's prose*: a changelog fragment legitimately cites
    a version heading as an example (the #2862 fragment writes "removing the
    ``## [1.2.0rc6]`` section exited 0"). That mention lives in the new
    release section, ABOVE the real heading, so the naive lookup silently
    retargets the mutation at the wrong section — and the test then passes
    vacuously, because a newer untagged section is legitimately not pinned.

    Anchoring to the line start is exactly what the checker does
    (``_HEADING_RE.match``), so the tests locate the heading the checker
    would.
    """
    m = re.search(rf"^## \[{re.escape(ver)}\]", text, re.MULTILINE)
    assert m, f"no '## [{ver}]' heading found at line start"
    return m.start()


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
        i = _heading_pos(text, ver)
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
        pos = text.index("\n", _heading_pos(text, newest)) + 1
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
    start = _heading_pos(text, anchor)
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
    start = _heading_pos(text, ver)
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
        # Under a git hook an inherited GIT_DIR would point these commands at
        # the real repository (#2608).
        subprocess.run(
            ["git", *args],
            cwd=str(self.root),
            check=True,
            capture_output=True,
            env=isolated_git_env(),
        )

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


class TestWipedTaggedSections:
    """#2865 — a tree with no tagged section at all is ambiguous: a fresh,
    pre-first-release repo (must keep passing) or a wiped CHANGELOG (must
    fail). Release tags reachable from HEAD disambiguate the two. The
    enumeration stays ``--merged HEAD`` (#2861): another release line's tags
    must not red a branch that never carried their sections."""

    def test_wiping_every_tagged_section_fails(self, tmp_path):
        # The terminal case of the class: keep [Unreleased], drop every
        # shipped section. Before the fix this exited 0 silently — the exact
        # tail of the v1.1.0rc5 cross-branch merge failure mode.
        repo = _TempRepo(tmp_path / "repo")
        repo.set_sections(["1.1.1", "1.1.2"])
        repo.tag("v1.1.1")
        repo.tag("v1.1.2")
        repo.set_sections([])  # the wipe: [Unreleased] only
        result = repo.run_check()
        assert result.returncode == 1
        assert "v1.1.2" in result.stderr  # newest tag named
        assert "v1.1.1" in result.stderr  # the wiped set, not just the newest
        assert "no '## [...]'" in result.stderr  # no anchor to pin against
        assert "git show v1.1.2:CHANGELOG.md" in result.stderr  # restore source

    def test_unreleased_only_tree_without_release_tags_passes(self, tmp_path):
        # Direction (b): a genuinely fresh / pre-first-release tree — only
        # [Unreleased], no release tag reachable from HEAD — must NOT go
        # red. A naive "fail whenever there's no anchor" would break it.
        repo = _TempRepo(tmp_path / "repo")
        repo.set_sections([])  # init already leaves exactly this; spelled out
        assert repo.run_check().returncode == 0

    def test_unwiped_tree_with_the_same_tags_passes(self, tmp_path):
        # Non-tautology guard: the SAME fixture without the wipe passes, so
        # it is the wipe — not the tag set — that trips the new check.
        repo = _TempRepo(tmp_path / "repo")
        repo.set_sections(["1.1.1", "1.1.2"])
        repo.tag("v1.1.1")
        repo.tag("v1.1.2")
        assert repo.run_check().returncode == 0

    def test_side_branch_tags_still_do_not_demand_sections(self, tmp_path):
        # #2861 scoping must survive the wipe check: a tag on a side branch
        # (not reachable from HEAD) leaves the [Unreleased]-only tree green.
        repo = _TempRepo(tmp_path / "repo")
        repo.tag_on_side_branch("v1.1.3")
        assert repo.run_check().returncode == 0

    def test_non_release_tags_alone_do_not_demand_sections(self, tmp_path):
        # Only non-release tags reachable = still the fresh-repo case.
        repo = _TempRepo(tmp_path / "repo")
        repo._git("tag", "-a", "vnot-a-version", "-m", "x")
        assert repo.run_check().returncode == 0


@requires_shipped
def test_real_repo_full_wipe_is_caught(tmp_path):
    """#2865 empirical canary (#1459) on the real repo + tags: strip every
    tagged section from a COPY (real tags still back the enumeration) —
    before the fix this exited 0; now it must fail naming the newest tag."""
    sections = check._split_sections(CHANGELOG.read_text(encoding="utf-8"))
    anchor = next(ver for ver, _ in sections if check._tag_exists(f"v{ver}"))
    text = CHANGELOG.read_text(encoding="utf-8")
    wiped = text[: _heading_pos(text, anchor)]  # keep the prefix above the anchor
    copy = tmp_path / "CHANGELOG.md"
    copy.write_text(wiped, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(copy)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert f"v{anchor}" in result.stderr
    assert "git show v" in result.stderr  # names the restore source
