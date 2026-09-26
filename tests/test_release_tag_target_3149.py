"""`make release` refuses a tag its release line cannot reach (#3149).

v1.3.0rc3 was tagged on ``release/1.3.0rc3`` and the release PR (#3131) was
squash-merged, so the tag never became an ancestor of ``main`` and
``tests/test_changelog_tagged_sections.py`` went red there until #3135 merged
the tagged commit back. ``scripts/check-release-tag-target.py`` runs before
``make release`` tags, and each case below builds that topology in a
throwaway repository with a bare ``origin``.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

from tests.git_env import GIT_EXECUTION_VARS

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check-release-tag-target.py"


@pytest.fixture(autouse=True)
def _no_inherited_git_env(monkeypatch):
    """Under a git hook an inherited GIT_DIR would aim these commands at the
    real repository (#2608)."""
    for var in GIT_EXECUTION_VARS:
        monkeypatch.delenv(var, raising=False)


def _load():
    spec = importlib.util.spec_from_file_location("check_release_tag_target", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(cwd: Path, message: str) -> None:
    (cwd / "f.txt").write_text(message)
    _git(cwd, "add", "f.txt")
    _git(cwd, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", message)


@pytest.fixture
def clone(tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", str(origin), str(work))
    _git(work, "switch", "-q", "-c", "main")
    _commit(work, "initial")
    _git(work, "push", "-q", "origin", "main")
    monkeypatch.chdir(work)
    return work


def test_main_after_the_release_pr_landed_is_accepted(clone):
    _commit(clone, "chore: release 9.9.0rc1")
    _git(clone, "push", "-q", "origin", "main")

    ok, message = _load().check()
    assert ok, message


def test_release_branch_is_refused_because_a_squash_merge_drops_its_commits(clone):
    """The v1.3.0rc3 shape: the tag would sit on release/X."""
    _git(clone, "switch", "-q", "-c", "release/9.9.0rc1")
    _commit(clone, "chore: release 9.9.0rc1")
    _git(clone, "push", "-q", "origin", "release/9.9.0rc1")

    ok, message = _load().check()
    assert not ok
    assert "release/9.9.0rc1" in message and "#3149" in message


def test_unpushed_commit_on_main_is_refused(clone):
    """A release commit that has not reached origin/main may never land there
    as this hash (a squash-merged PR rewrites it)."""
    _commit(clone, "chore: release 9.9.0rc1")

    ok, message = _load().check()
    assert not ok
    assert "not on origin/main" in message


def test_maintenance_branch_on_its_remote_is_accepted(clone):
    """A 1.2.x backport is tagged on the 1.2 line, which main never reaches."""
    _git(clone, "switch", "-q", "-c", "9.8")
    _commit(clone, "chore: release 9.8.3")
    _git(clone, "push", "-q", "origin", "9.8")

    ok, message = _load().check()
    assert ok, message


def test_makefile_release_runs_the_check_before_tagging():
    """The check only protects anything if `make release` calls it before
    `git tag`; the dry run calls it too, so a bad target is caught before a cut."""
    text = (REPO_ROOT / "Makefile").read_text()
    release = text[text.index("\nrelease:") : text.index("\n.PHONY: release-dry-run")]
    assert release.index("check-release-tag-target.py") < release.index("git tag -a")
    dry_run = text[text.index("\nrelease-dry-run:") :]
    dry_run = dry_run[: dry_run.index("\n.PHONY")]
    assert "check-release-tag-target.py" in dry_run
