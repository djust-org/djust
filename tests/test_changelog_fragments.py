"""``scripts/changelog-fragments.py`` — one ``changelog.d/`` fragment per PR.

Covers: compile order + idempotence on a fixture CHANGELOG, section
validation, the direct-``[Unreleased]``-edit refusal (against a real throwaway
git repo), the test-count claim check on a fragment, ``preview`` == what
``compile`` writes, and a round trip against the REAL ``CHANGELOG.md`` head.
"""

from __future__ import annotations

import difflib
import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "changelog-fragments.py"
REAL_CHANGELOG = REPO_ROOT / "CHANGELOG.md"


def _load():
    spec = importlib.util.spec_from_file_location("changelog_fragments", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


cf = _load()

FIXTURE = textwrap.dedent(
    """\
    # Changelog

    ## [Unreleased]

    ### Added

    - **Existing added bullet.** Already merged.

    ### Fixed

    - **Existing fixed bullet.** Already merged.

    ## [1.0.0] - 2026-01-01

    ### Added

    - **Shipped.** Frozen.
    """
)


def _write(tmp: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def _run(tmp: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--changelog", str(tmp / "CHANGELOG.md"), *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(tmp),
    )


# --------------------------------------------------------------------------
# compile: order, placement, idempotence
# --------------------------------------------------------------------------


class TestCompile:
    def test_new_headings_land_in_canonical_order_and_existing_ones_are_appended(self, tmp_path):
        _write(
            tmp_path,
            {
                "CHANGELOG.md": FIXTURE,
                # Filenames chosen so filename order != canonical order.
                "changelog.d/9.security.md": "- **Sec.** s\n",
                "changelog.d/5.changed.md": "- **Chg.** c\n",
                "changelog.d/2.fixed.md": "- **Fix two.** f\n",
                "changelog.d/1.removed.md": "- **Rem.** r\n",
                "changelog.d/README.md": "# not a fragment\n",
            },
        )
        proc = _run(tmp_path, "compile")
        assert proc.returncode == 0, proc.stderr
        out = (tmp_path / "CHANGELOG.md").read_text()
        expected = textwrap.dedent(
            """\
            # Changelog

            ## [Unreleased]

            ### Added

            - **Existing added bullet.** Already merged.

            ### Changed

            - **Chg.** c

            ### Fixed

            - **Existing fixed bullet.** Already merged.
            - **Fix two.** f

            ### Security

            - **Sec.** s

            ### Removed

            - **Rem.** r

            ## [1.0.0] - 2026-01-01

            ### Added

            - **Shipped.** Frozen.
            """
        )
        assert out == expected
        # Fragments are consumed; README survives.
        assert sorted(p.name for p in (tmp_path / "changelog.d").iterdir()) == ["README.md"]

    def test_fragments_within_a_section_sort_by_filename(self, tmp_path):
        _write(
            tmp_path,
            {
                "CHANGELOG.md": FIXTURE,
                # Bodies sort in the OPPOSITE order to filenames, so a sort on
                # anything but the filename fails this test (gate-off, #2129).
                "changelog.d/2600.added.md": "- **Mike.**\n",
                "changelog.d/2599.added.md": "- **Zulu.**\n",
                "changelog.d/zzz-slug.added.md": "- **Alpha.**\n",
            },
        )
        assert _run(tmp_path, "compile").returncode == 0
        added = (tmp_path / "CHANGELOG.md").read_text().split("### Added")[1].split("### Fixed")[0]
        assert added.index("- **Zulu.**") < added.index("- **Mike.**") < added.index("- **Alpha.**")

    def test_multi_paragraph_fragment_keeps_blank_line_separation(self, tmp_path):
        _write(
            tmp_path,
            {
                "CHANGELOG.md": FIXTURE,
                "changelog.d/1.added.md": "- **Multi.** first\n\n  second paragraph\n",
                "changelog.d/2.added.md": "- **Single.**\n",
            },
        )
        assert _run(tmp_path, "compile").returncode == 0
        out = (tmp_path / "CHANGELOG.md").read_text()
        assert (
            "- **Existing added bullet.** Already merged.\n\n- **Multi.** first\n\n"
            "  second paragraph\n\n- **Single.**\n\n### Fixed" in out
        )

    def test_empty_unreleased_after_a_release_cut(self, tmp_path):
        _write(
            tmp_path,
            {
                "CHANGELOG.md": "# Changelog\n\n## [Unreleased]\n\n## [1.0.0] - 2026-01-01\n\n- x\n",
                "changelog.d/1.fixed.md": "- **F.**\n",
                "changelog.d/2.added.md": "- **A.**\n",
            },
        )
        assert _run(tmp_path, "compile").returncode == 0
        assert (tmp_path / "CHANGELOG.md").read_text() == (
            "# Changelog\n\n## [Unreleased]\n\n### Added\n\n- **A.**\n\n### Fixed\n\n- **F.**\n\n"
            "## [1.0.0] - 2026-01-01\n\n- x\n"
        )

    def test_compile_is_idempotent(self, tmp_path):
        _write(tmp_path, {"CHANGELOG.md": FIXTURE, "changelog.d/1.added.md": "- **A.**\n"})
        assert _run(tmp_path, "compile").returncode == 0
        once = (tmp_path / "CHANGELOG.md").read_text()
        proc = _run(tmp_path, "compile")
        assert proc.returncode == 0
        assert "nothing to compile" in proc.stdout
        assert (tmp_path / "CHANGELOG.md").read_text() == once

    def test_dry_run_writes_nothing_and_keeps_fragments(self, tmp_path):
        _write(tmp_path, {"CHANGELOG.md": FIXTURE, "changelog.d/1.added.md": "- **A.**\n"})
        proc = _run(tmp_path, "compile", "--dry-run")
        assert proc.returncode == 0
        assert "- **A.**" in proc.stdout
        assert (tmp_path / "CHANGELOG.md").read_text() == FIXTURE
        assert (tmp_path / "changelog.d" / "1.added.md").exists()

    def test_preview_equals_what_compile_writes(self, tmp_path):
        _write(
            tmp_path,
            {
                "CHANGELOG.md": FIXTURE,
                "changelog.d/1.documentation.md": "- **Doc.**\n",
                "changelog.d/2.fixed.md": "- **Fix.**\n",
            },
        )
        preview = _run(tmp_path, "preview")
        assert preview.returncode == 0
        assert (tmp_path / "CHANGELOG.md").read_text() == FIXTURE  # preview is read-only
        assert _run(tmp_path, "compile").returncode == 0
        compiled = (tmp_path / "CHANGELOG.md").read_text()
        section = compiled[compiled.index("## [Unreleased]") : compiled.index("## [1.0.0]")]
        assert preview.stdout + "\n" == section  # the "\n" is the blank line before ## [1.0.0]

    def test_missing_unreleased_heading_is_an_error(self, tmp_path):
        _write(
            tmp_path,
            {"CHANGELOG.md": "# Changelog\n\n## [1.0.0]\n", "changelog.d/1.added.md": "- a\n"},
        )
        proc = _run(tmp_path, "compile")
        assert proc.returncode == 1
        assert "[Unreleased]" in proc.stderr


# --------------------------------------------------------------------------
# check: section + body validation, test-count claims
# --------------------------------------------------------------------------


class TestCheckValidation:
    @pytest.mark.parametrize(
        "name",
        ["1.md", "1.tests.md", "1.Added.md", "1.added.txt", "1.performance.md"],
    )
    def test_invalid_names_are_refused(self, tmp_path, name):
        _write(tmp_path, {"CHANGELOG.md": FIXTURE, f"changelog.d/{name}": "- **x.**\n"})
        proc = _run(tmp_path, "check")
        assert proc.returncode == 1, name
        assert name in proc.stderr

    @pytest.mark.parametrize("section", list(cf.SECTIONS))
    def test_every_canonical_section_is_accepted(self, tmp_path, section):
        _write(tmp_path, {"CHANGELOG.md": FIXTURE, f"changelog.d/1.{section}.md": "- **x.**\n"})
        proc = _run(tmp_path, "check")
        assert proc.returncode == 0, proc.stderr

    @pytest.mark.parametrize(
        "body", ["", "\n\n", "Not a bullet\n", "* star bullet\n", "  - indented\n"]
    )
    def test_body_must_be_a_top_level_dash_bullet(self, tmp_path, body):
        _write(tmp_path, {"CHANGELOG.md": FIXTURE, "changelog.d/1.fixed.md": body})
        proc = _run(tmp_path, "check")
        assert proc.returncode == 1, repr(body)
        assert "1.fixed.md" in proc.stderr

    def test_readme_is_not_a_fragment(self, tmp_path):
        _write(tmp_path, {"CHANGELOG.md": FIXTURE, "changelog.d/README.md": "# prose\n"})
        assert _run(tmp_path, "check").returncode == 0

    def test_test_count_claim_in_a_fragment_is_checked(self, tmp_path):
        _write(
            tmp_path,
            {
                "CHANGELOG.md": FIXTURE,
                "tests/unit/test_thing.py": "def test_a(): pass\ndef test_b(): pass\n",
                "changelog.d/1.fixed.md": "- **X.** 3 regression cases in `tests/unit/test_thing.py`.\n",
            },
        )
        proc = _run(tmp_path, "check")
        assert proc.returncode == 1
        assert "claims 3" in proc.stderr and "has 2" in proc.stderr

    def test_test_count_claim_that_matches_passes(self, tmp_path):
        _write(
            tmp_path,
            {
                "CHANGELOG.md": FIXTURE,
                "tests/unit/test_thing.py": "def test_a(): pass\ndef test_b(): pass\n",
                "changelog.d/1.fixed.md": "- **X.** 2 regression cases in `tests/unit/test_thing.py`.\n",
            },
        )
        assert _run(tmp_path, "check").returncode == 0

    def test_count_checker_script_scans_fragments_too(self, tmp_path):
        # The pre-commit test-count hook is re-pointed at changelog.d/ as well.
        _write(
            tmp_path,
            {
                "CHANGELOG.md": FIXTURE,
                "tests/unit/test_thing.py": "def test_a(): pass\n",
                "changelog.d/1.fixed.md": "- **X.** 4 regression cases in `tests/unit/test_thing.py`.\n",
            },
        )
        proc = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "check-changelog-test-counts.py"),
                str(tmp_path / "CHANGELOG.md"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 1
        assert "changelog.d/1.fixed.md says 4" in proc.stderr


# --------------------------------------------------------------------------
# check: direct [Unreleased] edit refusal (real git repo)
# --------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "HOME": str(repo),
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        },
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _write(
        tmp_path,
        {
            "CHANGELOG.md": FIXTURE,
            "changelog.d/README.md": "# fragments\n",
            "changelog.d/1.added.md": "- **Pending.**\n",
        },
    )
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path


class TestDirectEditRefusal:
    def test_staged_direct_unreleased_edit_is_refused(self, repo):
        text = FIXTURE.replace(
            "- **Existing fixed bullet.**", "- **Sneaky new bullet.**\n- **Existing fixed bullet.**"
        )
        (repo / "CHANGELOG.md").write_text(text)
        _git(repo, "add", "CHANGELOG.md")
        proc = _run(repo, "check", "--cached")
        assert proc.returncode == 1
        assert "edited directly" in proc.stderr

    def test_edit_below_unreleased_is_not_the_concern_of_this_check(self, repo):
        # Shipped sections are pinned by check-changelog-tagged-sections.py (#2028);
        # this check only guards the [Unreleased] body.
        (repo / "CHANGELOG.md").write_text(
            FIXTURE.replace("- **Shipped.** Frozen.", "- **Shipped.** Frozen!")
        )
        _git(repo, "add", "CHANGELOG.md")
        assert _run(repo, "check", "--cached").returncode == 0

    def test_release_cut_compile_is_allowed(self, repo):
        assert _run(repo, "compile").returncode == 0  # edits [Unreleased] AND deletes 1.added.md
        _git(repo, "add", "-A")
        proc = _run(repo, "check", "--cached")
        assert proc.returncode == 0, proc.stderr

    def test_allow_release_cut_flag_overrides(self, repo):
        (repo / "CHANGELOG.md").write_text(FIXTURE.replace("Already merged.", "Edited."))
        _git(repo, "add", "CHANGELOG.md")
        assert _run(repo, "check", "--cached").returncode == 1
        assert _run(repo, "check", "--cached", "--allow-release-cut").returncode == 0

    def test_range_form_sees_a_committed_direct_edit(self, repo):
        (repo / "CHANGELOG.md").write_text(FIXTURE.replace("Already merged.", "Edited."))
        _git(repo, "add", "CHANGELOG.md")
        _git(repo, "commit", "-q", "-m", "direct edit", "--no-verify")
        proc = _run(repo, "check", "--range", "HEAD~1..HEAD")
        assert proc.returncode == 1
        assert "edited directly" in proc.stderr
        # Gate-off sibling: the same range with only a fragment added is clean.
        (repo / "changelog.d" / "2.fixed.md").write_text("- **F.**\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "fragment", "--no-verify")
        assert _run(repo, "check", "--range", "HEAD~1..HEAD").returncode == 0

    def test_unchanged_changelog_passes(self, repo):
        (repo / "changelog.d" / "2.fixed.md").write_text("- **F.**\n")
        _git(repo, "add", "-A")
        assert _run(repo, "check", "--cached").returncode == 0


class TestFragmentMigration:
    """#2603 — moving a pre-fragment ``[Unreleased]`` bullet INTO ``changelog.d/``
    is a removal-only diff whose text reappears in a staged fragment. That is
    not a direct edit and must pass; every neighbouring shape must still fail.
    """

    MIGRATED = "- **Existing fixed bullet.** Already merged.\n"

    def _remove_fixed_bullet(self, repo: Path) -> None:
        text = FIXTURE.replace("\n### Fixed\n\n- **Existing fixed bullet.** Already merged.\n", "")
        assert text != FIXTURE
        (repo / "CHANGELOG.md").write_text(text)

    def test_staged_migration_into_a_fragment_is_allowed(self, repo):
        self._remove_fixed_bullet(repo)
        (repo / "changelog.d" / "2.fixed.md").write_text(self.MIGRATED)
        _git(repo, "add", "-A")
        proc = _run(repo, "check", "--cached")
        assert proc.returncode == 0, proc.stderr

    def test_committed_migration_is_allowed_in_range_form(self, repo):
        self._remove_fixed_bullet(repo)
        (repo / "changelog.d" / "2.fixed.md").write_text(self.MIGRATED)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "migrate", "--no-verify")
        proc = _run(repo, "check", "--range", "HEAD~1..HEAD")
        assert proc.returncode == 0, proc.stderr

    def test_migration_tolerates_rewrapped_whitespace(self, repo):
        self._remove_fixed_bullet(repo)
        (repo / "changelog.d" / "2.fixed.md").write_text(
            "- **Existing fixed bullet.**\n  Already merged.\n"
        )
        _git(repo, "add", "-A")
        assert _run(repo, "check", "--cached").returncode == 0

    def test_removal_without_a_fragment_is_still_refused(self, repo):
        self._remove_fixed_bullet(repo)
        _git(repo, "add", "-A")
        proc = _run(repo, "check", "--cached")
        assert proc.returncode == 1
        assert "edited directly" in proc.stderr

    def test_removal_with_a_fragment_carrying_different_text_is_refused(self, repo):
        self._remove_fixed_bullet(repo)
        (repo / "changelog.d" / "2.fixed.md").write_text("- **Something else entirely.**\n")
        _git(repo, "add", "-A")
        assert _run(repo, "check", "--cached").returncode == 1

    def test_migration_plus_a_new_body_line_is_refused(self, repo):
        self._remove_fixed_bullet(repo)
        text = (repo / "CHANGELOG.md").read_text()
        (repo / "CHANGELOG.md").write_text(
            text.replace(
                "- **Existing added bullet.**", "- **Sneaky.**\n- **Existing added bullet.**"
            )
        )
        (repo / "changelog.d" / "2.fixed.md").write_text(self.MIGRATED)
        _git(repo, "add", "-A")
        assert _run(repo, "check", "--cached").returncode == 1

    def test_a_pre_existing_untouched_fragment_does_not_license_the_removal(self, repo):
        # The committed 1.added.md is not part of the change; only fragments the
        # change adds/modifies count as the destination of a migration.
        text = FIXTURE.replace("- **Existing added bullet.** Already merged.\n", "- **Pending.**\n")
        (repo / "CHANGELOG.md").write_text(text)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "seed", "--no-verify")
        (repo / "CHANGELOG.md").write_text(text.replace("- **Pending.**\n", ""))
        _git(repo, "add", "-A")
        assert _run(repo, "check", "--cached").returncode == 1


# --------------------------------------------------------------------------
# round trip against the REAL CHANGELOG.md
# --------------------------------------------------------------------------


class TestRealChangelog:
    def test_split_render_is_byte_identical(self):
        text = REAL_CHANGELOG.read_text(encoding="utf-8")
        assert cf.split_changelog(text).render() == text

    def test_compiling_a_fixture_fragment_into_a_copy_adds_only_that_bullet(self, tmp_path):
        text = REAL_CHANGELOG.read_text(encoding="utf-8")
        _write(
            tmp_path,
            {
                "CHANGELOG.md": text,
                "changelog.d/zz-roundtrip.deprecated.md": "- **ROUNDTRIP-FIXTURE bullet.**\n",
            },
        )
        assert _run(tmp_path, "compile").returncode == 0
        out = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
        before, after = text.splitlines(), out.splitlines()
        inserted: list[str] = []
        for op, _i1, _i2, j1, j2 in difflib.SequenceMatcher(
            a=before, b=after, autojunk=False
        ).get_opcodes():
            assert op in ("equal", "insert"), f"compile must only insert lines, got {op}"
            if op == "insert":
                inserted.extend(after[j1:j2])
        assert inserted == ["", "### Deprecated", "", "- **ROUNDTRIP-FIXTURE bullet.**"]
        # Everything from the first shipped heading down is untouched (the #2028 pin).
        first_shipped = text.index("\n## [", text.index("## [Unreleased]") + 1)
        assert out.endswith(text[first_shipped:])
        # The new heading sits inside [Unreleased], after every existing one.
        unreleased = out[out.index("## [Unreleased]") : out.index(text[first_shipped:])]
        assert unreleased.rstrip().endswith("### Deprecated\n\n- **ROUNDTRIP-FIXTURE bullet.**")

    def test_real_fragment_dir_passes_check(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "check"], capture_output=True, text=True, check=False
        )
        assert proc.returncode == 0, proc.stderr
