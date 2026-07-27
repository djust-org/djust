"""The attribution logic, RUN rather than grepped (#2139).

The first version of `scripts/pre-push-pytest.sh` shipped with twelve tests,
all of them source-greps. You could invert the partition (`comm -23` →
`comm -13`), swapping "yours" and "pre-existing", or delete the merge-base run
entirely, and the suite stayed green. That is #1859 in a tool whose entire
value is that its answer is trusted — and it is why two 🔴s shipped:

* pytest resolves **every** argument before collecting, so one id that does not
  exist at the merge-base aborted the whole session with ``no tests ran`` and
  zero results. The guard whitelisted that string, so `PRE` came back empty and
  every genuinely pre-existing failure was reported as *new on this branch* —
  the confidently wrong answer, in exactly the mixed case the tool exists for.
* `$FAILED` was unquoted, so a parametrized id containing a space
  (``test_x[a b]``) split into two unresolvable args and poisoned the same run.

These build a real git repo with a real merge-base and run the **actual**
script against it. The synthetic suite is trivial; the attribution logic under
test is byte-identical to what ships.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/pre-push-pytest.sh"
VENV_PY = ROOT / ".venv/bin/python"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )


def _make_repo(tmp_path: Path, base_files: dict[str, str], branch_files: dict[str, str]) -> Path:
    """A repo whose merge-base has `base_files` and whose HEAD adds `branch_files`."""
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "scripts").mkdir()
    (repo / "python" / "djust").mkdir(parents=True)

    # The script under test, verbatim — only PATHS is narrowed to the fixture.
    src = SCRIPT.read_text().replace(
        "PATHS=(tests/ python/tests/ python/djust/tests/)", "PATHS=(tests/)"
    )
    # The fixture repo has no venv; point at the real one.
    src = src.replace('"$REPO_ROOT/.venv/bin/python"', f'"{VENV_PY}"')
    src = src.replace(
        'bash scripts/run-with-venv-python.sh -m pytest "${PATHS[@]}" -q',
        f'"{VENV_PY}" -m pytest "${{PATHS[@]}}" -q',
    )
    src = src.replace(
        'WT="$(bash scripts/run-with-venv-python.sh --worktree-pythonpath 2>/dev/null || true)"',
        'WT=""',
    )
    (repo / "scripts" / "pre-push-pytest.sh").write_text(src)
    # The script requires a built extension to consider the base comparable.
    (repo / "python" / "djust" / "_rust.cpython-000-x.so").write_text("")

    _git(repo, "init", "-q", "-b", "main")
    for name, body in base_files.items():
        (repo / "tests" / name).write_text(textwrap.dedent(body))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "branch", "-f", "work")
    _git(repo, "checkout", "-q", "work")
    for name, body in branch_files.items():
        (repo / "tests" / name).write_text(textwrap.dedent(body))
    if branch_files:
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "branch")
    return repo


def _run(repo: Path, merge_base: str | None = None) -> str:
    script = (repo / "scripts" / "pre-push-pytest.sh").read_text()
    if merge_base is None:
        merge_base = subprocess.run(
            ["git", "rev-parse", "main"], cwd=repo, capture_output=True, text=True
        ).stdout.strip()
    # The fixture has no `origin`, so pin the merge-base the same way the real
    # script derives it.
    script = script.replace(
        'MERGE_BASE=$(git merge-base HEAD "origin/$BASE" 2>/dev/null || true)',
        f"MERGE_BASE={merge_base}",
    )
    (repo / "scripts" / "pre-push-pytest.sh").write_text(script)
    # The fixture repo is not a Django project, and the venv's pytest
    # auto-loads pytest-django from the real repo's config. Disable plugin
    # autoload for the fixture only — the attribution logic under test does
    # not depend on any plugin.
    r = subprocess.run(
        ["bash", "scripts/pre-push-pytest.sh"],
        cwd=repo,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": ""},
    )
    return r.stdout + r.stderr


pytestmark = pytest.mark.skipif(
    not VENV_PY.is_file() or shutil.which("git") is None,
    reason="needs the repo venv and git",
)


def test_a_branch_local_failure_is_reported_as_new(tmp_path):
    repo = _make_repo(
        tmp_path,
        {"test_ok.py": "def test_ok(): assert True\n"},
        {"test_new.py": "def test_new(): assert False\n"},
    )
    out = _run(repo)
    assert re.sub(r"\s+", " ", out).count("all 1 are new on this branch"), out


def test_a_failure_present_at_the_base_is_reported_as_pre_existing(tmp_path):
    repo = _make_repo(
        tmp_path,
        {"test_pre.py": "def test_pre(): assert False\n"},
        {"test_unrelated.py": "def test_unrelated(): assert True\n"},
    )
    out = _run(repo)
    assert "ALSO fail at the merge-base" in out, out
    assert "Your branch did not cause them" in out, out


def test_a_MIXED_run_partitions_correctly(tmp_path):
    # THE case the tool exists for, and the one the first version answered
    # backwards: main red with 2, branch adding 1. Passing all three ids in one
    # pytest invocation aborted on the unresolvable one and reported all three
    # as new — blaming the contributor for main's breakage.
    repo = _make_repo(
        tmp_path,
        {"test_pre.py": "def test_pre_one(): assert False\ndef test_pre_two(): assert False\n"},
        {"test_new.py": "def test_new(): assert False\n"},
    )
    out = _run(repo)
    assert "2 of 3 failure(s) are PRE-EXISTING" in out, out
    assert re.sub(r"\s+", " ", out).count("1 are new on this branch"), out
    assert "tests/test_new.py::test_new" in out, out
    assert "test_pre_one" not in out.split("are new on this branch:")[-1], out


def test_a_renamed_test_is_new_not_unresolvable(tmp_path):
    # A test that exists at the base under a DIFFERENT name is absent there —
    # which must make it new, not poison the run for everyone else.
    repo = _make_repo(
        tmp_path,
        {"test_pre.py": "def test_pre(): assert False\ndef test_old_name(): assert False\n"},
        {"test_pre.py": "def test_pre(): assert False\ndef test_new_name(): assert False\n"},
    )
    out = _run(repo)
    assert "1 of 2 failure(s) are PRE-EXISTING" in out, out
    assert "test_new_name" in out, out


def test_a_parametrized_id_containing_a_space_does_not_poison_the_run(tmp_path):
    # `$FAILED` was unquoted, so `test_x[a b]` split into two unresolvable
    # args and every failure in the run was then reported as new. One hostile
    # id is all it takes, and pytest does not sanitise them.
    repo = _make_repo(
        tmp_path,
        {
            "test_pre.py": (
                "import pytest\n"
                "def test_plain(): assert False\n"
                '@pytest.mark.parametrize("v", ["a b"])\n'
                "def test_spaced(v): assert False\n"
            )
        },
        {"test_unrelated.py": "def test_unrelated(): assert True\n"},
    )
    out = _run(repo)
    assert "ALSO fail at the merge-base" in out, out
    assert "are new on this branch" not in out, out


def test_a_green_run_never_touches_the_merge_base(tmp_path):
    repo = _make_repo(tmp_path, {"test_ok.py": "def test_ok(): assert True\n"}, {})
    out = _run(repo)
    assert "merge-base" not in out, out
    assert "failing test(s)" not in out, out
