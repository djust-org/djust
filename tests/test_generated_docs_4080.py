"""Generated doc regions from one-file-per-entry sources (#4080).

Covers ``scripts/lib/generated_docs.py``, ``scripts/system-check-docs.py`` and
``scripts/removed-names-docs.py``: the round trip, every way the committed docs
can drift from their sources, and a real two-branch ``git merge`` showing that
two changes adding different entries no longer conflict while two changes taking
the same check ID do.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"

#: What a copy of the repo needs for both generators to run.
FILES = (
    "docs/system-checks.md",
    "docs/website/guides/error-codes.md",
    "docs/website/guides/migrating-to-2.0.md",
    "docs/API_STABILITY.md",
    "python/djust/_removed_settings.py",
    "scripts/system-check-docs.py",
    "scripts/removed-names-docs.py",
    "scripts/lib/__init__.py",
    "scripts/lib/generated_docs.py",
    ".gitattributes",
)
DIRS = ("docs/checks", "docs/removed")


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


checks_docs = _load("system_check_docs", "system-check-docs.py")
removed_docs = _load("removed_names_docs", "removed-names-docs.py")
gd = checks_docs.gd


def _copy_repo(dest: Path) -> Path:
    for rel in FILES:
        src = ROOT / rel
        if not src.exists():  # scripts/lib/__init__.py may not exist
            continue
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dest / rel)
    for rel in DIRS:
        shutil.copytree(ROOT / rel, dest / rel)
    return dest


def _check(root: Path) -> int:
    return checks_docs._run("check", root) or removed_docs.run("check", root)


def _write(root: Path) -> None:
    assert checks_docs._run("write", root) == 0
    assert removed_docs.run("write", root) == 0


V028 = """---
id: V028
severity: Warning
summary: A brand-new check for the test
---

<!-- doc: reference -->
### V028 — a brand-new check

- **Severity**: Warning

<!-- doc: guide -->
### V028: a brand-new check

**Fix**: none needed.
"""


def test_the_committed_docs_match_their_sources() -> None:
    assert _check(ROOT) == 0


def test_write_then_check_round_trips(tmp_path: Path) -> None:
    root = _copy_repo(tmp_path)
    before = {p: (root / p).read_text() for p in FILES if (root / p).exists()}
    _write(root)
    assert {p: (root / p).read_text() for p in before} == before
    assert _check(root) == 0


def test_a_hand_edit_inside_a_generated_region_fails(tmp_path: Path, capsys) -> None:
    root = _copy_repo(tmp_path)
    doc = root / "docs/system-checks.md"
    doc.write_text(
        doc.read_text().replace("| V027 | LiveView | Warning |", "| V027 | LiveView | Error |")
    )
    assert _check(root) == 1
    assert "generated region differs from its sources" in capsys.readouterr().err


def test_a_new_entry_appears_only_after_write_and_a_removed_one_disappears(
    tmp_path: Path, capsys
) -> None:
    root = _copy_repo(tmp_path)
    (root / "docs/checks/V028.md").write_text(V028)
    assert _check(root) == 1, "a source added without regenerating must fail check"
    assert "generated region differs" in capsys.readouterr().err
    _write(root)
    sc = (root / "docs/system-checks.md").read_text()
    ec = (root / "docs/website/guides/error-codes.md").read_text()
    assert "| V028 | LiveView | Warning | A brand-new check for the test |" in sc
    assert "### V028 — a brand-new check" in sc and "### V028: a brand-new check" in ec
    # Sorted after V027, before the S family.
    assert sc.index("### V027 —") < sc.index("### V028 —") < sc.index("### S001 —")
    assert _check(root) == 0

    (root / "docs/checks/V028.md").unlink()
    _write(root)
    assert "V028" not in (root / "docs/system-checks.md").read_text()
    assert "V028" not in (root / "docs/website/guides/error-codes.md").read_text()


def test_a_prose_edit_needs_a_refreshed_fingerprint(tmp_path: Path, capsys) -> None:
    root = _copy_repo(tmp_path)
    doc = root / "docs/API_STABILITY.md"
    doc.write_text(doc.read_text().replace("## Removed in 2.0.0", "## Removed in 2.0.0 (edited)"))
    assert _check(root) == 1
    assert "fingerprint on its last line was not refreshed" in capsys.readouterr().err
    _write(root)
    assert _check(root) == 0
    assert "## Removed in 2.0.0 (edited)" in doc.read_text()


def test_two_fingerprint_lines_mean_a_union_merge_of_two_prose_edits(
    tmp_path: Path, capsys
) -> None:
    root = _copy_repo(tmp_path)
    doc = root / "docs/system-checks.md"
    doc.write_text(doc.read_text() + "<!-- generated-doc prose-sha256: 000000000000 -->\n")
    assert _check(root) == 1
    assert "2 prose fingerprint lines" in capsys.readouterr().err


def test_a_file_whose_id_disagrees_with_its_name_is_refused(tmp_path: Path, capsys) -> None:
    root = _copy_repo(tmp_path)
    (root / "docs/checks/V028.md").write_text(V028.replace("id: V028", "id: V027"))
    assert _check(root) == 1
    assert "front matter 'id: V027' does not match the file name" in capsys.readouterr().err


def test_a_table_family_entry_needs_severity_and_summary(tmp_path: Path, capsys) -> None:
    root = _copy_repo(tmp_path)
    (root / "docs/checks/V028.md").write_text(
        V028.replace("summary: A brand-new check for the test\n", "")
    )
    assert _check(root) == 1
    assert "V028.md: needs a 'summary' line" in capsys.readouterr().err


def test_registry_flags_an_id_in_code_with_no_file_and_says_where_to_add_it() -> None:
    entries = checks_docs.load()
    emitted = dict(checks_docs.emitted_ids())
    emitted["V099"] = ["python/djust/checks/components.py:1"]
    problems = checks_docs.registry_problems(entries, emitted)
    assert len(problems) == 1
    assert "djust.V099" in problems[0]
    assert "Add docs/checks/V099.md" in problems[0]
    assert "make new-check-id FAMILY=V" in problems[0]


def test_registry_refuses_a_retired_or_reserved_id_in_code() -> None:
    entries = checks_docs.load()
    emitted = dict(checks_docs.emitted_ids())
    emitted["V011"] = ["x.py:1"]
    emitted["S010"] = ["y.py:2"]
    problems = checks_docs.registry_problems(entries, emitted)
    assert any("djust.V011" in p and "is retired" in p for p in problems), problems
    assert any("djust.S010" in p and "is reserved" in p for p in problems), problems


def test_next_id_skips_retired_and_reserved_ids(tmp_path: Path) -> None:
    for stem, status in (
        ("V001", "live"),
        ("V002", "retired"),
        ("C005", "live"),
        ("C301", "live"),
        ("S010", "reserved"),
    ):
        (tmp_path / ("%s.md" % stem)).write_text("---\nid: %s\nstatus: %s\n---\n" % (stem, status))
    entries = checks_docs.load(tmp_path)
    assert checks_docs.next_id("V", entries) == "V003"
    assert checks_docs.next_id("C", entries) == "C006"  # the 0xx range, not C302
    assert checks_docs.next_id("C3", entries) == "C302"
    assert checks_docs.next_id("S", entries) == "S011"
    assert checks_docs.next_id("Y", entries) == "Y001"


def test_a_removed_name_in_code_without_a_doc_row_fails(tmp_path: Path, capsys) -> None:
    root = _copy_repo(tmp_path)
    runtime = root / "python/djust/_removed_settings.py"
    runtime.write_text(
        runtime.read_text().replace(
            '    "DJUST_REDIS_URL": "DJUST_CONFIG[\'REDIS_URL\']",\n',
            '    "DJUST_REDIS_URL": "DJUST_CONFIG[\'REDIS_URL\']",\n    "DJUST_NEW_THING": "x",\n',
        )
    )
    assert _check(root) == 1
    err = capsys.readouterr().err
    assert "REMOVED_FLAT_SETTINGS['DJUST_NEW_THING']" in err
    assert "docs/removed/2.0/migration/" in err


def test_a_removed_row_that_covers_an_unknown_key_fails(tmp_path: Path, capsys) -> None:
    root = _copy_repo(tmp_path)
    (root / "docs/removed/2.0/migration/315-bogus.md").write_text(
        "---\ntable: settings\ncovers: NOT_A_REMOVED_SETTING\nremoved: x\nuse: y\n---\n"
    )
    assert _check(root) == 1
    assert "covers 'NOT_A_REMOVED_SETTING'" in capsys.readouterr().err


# --- The acceptance test: a real two-branch merge ---------------------------------


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
        cwd=repo,
        check=check,
        capture_output=True,
        text=True,
    )


def _run_script(repo: Path, script: str, mode: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(repo / "scripts" / script), mode],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def _branch(repo: Path, name: str, files: dict[str, str]) -> None:
    _git(repo, "checkout", "-q", "-b", name, "base")
    for rel, text in files.items():
        (repo / rel).write_text(text)
    assert _run_script(repo, "system-check-docs.py", "write").returncode == 0
    assert _run_script(repo, "removed-names-docs.py", "write").returncode == 0
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", name)


REMOVED_ROW = "---\ntable: python\nremoved: `djust.%s`\nuse: `djust.%s_v2`\n---\n"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    root = _copy_repo(tmp_path / "repo")
    _git(root, "init", "-q", "-b", "base")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return root


def test_two_branches_adding_different_entries_merge_without_a_conflict(repo: Path) -> None:
    _branch(
        repo,
        "a",
        {
            "docs/checks/V028.md": V028,
            "docs/removed/2.0/migration/320-old-a.md": REMOVED_ROW % ("old_a", "old_a"),
        },
    )
    _branch(
        repo,
        "b",
        {
            "docs/checks/V029.md": V028.replace("V028", "V029").replace("brand-new", "second"),
            "docs/removed/2.0/migration/320-old-b.md": REMOVED_ROW % ("old_b", "old_b"),
        },
    )
    _git(repo, "checkout", "-q", "a")
    merge = _git(repo, "merge", "--no-edit", "b", check=False)
    assert merge.returncode == 0, merge.stdout + merge.stderr
    assert "CONFLICT" not in merge.stdout

    # Both entries are present; after regeneration the docs are sorted and clean.
    assert _run_script(repo, "system-check-docs.py", "write").returncode == 0
    assert _run_script(repo, "removed-names-docs.py", "write").returncode == 0
    assert _run_script(repo, "system-check-docs.py", "check").returncode == 0
    assert _run_script(repo, "removed-names-docs.py", "check").returncode == 0
    sc = (repo / "docs/system-checks.md").read_text()
    assert sc.index("| V027 |") < sc.index("| V028 |") < sc.index("| V029 |") < sc.index("| S001 |")
    mg = (repo / "docs/website/guides/migrating-to-2.0.md").read_text()
    assert mg.index("`djust.old_a`") < mg.index("`djust.old_b`")
    assert mg.count("`djust.old_a`") == 1 and mg.count("`djust.old_b`") == 1


def test_two_branches_taking_the_same_check_id_conflict(repo: Path) -> None:
    _branch(repo, "a", {"docs/checks/V028.md": V028})
    _branch(repo, "b", {"docs/checks/V028.md": V028.replace("brand-new", "different")})
    _git(repo, "checkout", "-q", "a")
    merge = _git(repo, "merge", "--no-edit", "b", check=False)
    assert merge.returncode != 0
    assert "CONFLICT (add/add): Merge conflict in docs/checks/V028.md" in merge.stdout
