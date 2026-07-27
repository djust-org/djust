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
    assert "2 of 3 checked failure(s) are PRE-EXISTING" in out, out
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
    assert "1 of 2 checked failure(s) are PRE-EXISTING" in out, out
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


# --- parsing the FAILED line ---------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "assert False",  # no hyphen — the only shape the old sed handled
        "AssertionError: pre-existing doc-snippet drift",
        "KeyError: 'main-health'",
        "AssertionError: assert 1 == -1",
        "AssertionError: expected exit 0, got 1: README.md:91 - claim out-of-band",
    ],
)
def test_a_hyphen_in_the_failure_MESSAGE_does_not_break_attribution(tmp_path, message):
    # `sed 's/ - [^-]*$//'` only substitutes when the message contains no
    # hyphen at all, which is false for most real pytest messages. The id then
    # kept the message glued on, was unresolvable at the merge-base, and was
    # announced as NEW — the same wrong answer as the bug it replaced, reached
    # from the other side.
    #
    # It fired on the exact case #2139 exists for: the doc-snippet tests that
    # made main red assert with messages full of hyphens.
    repo = _make_repo(
        tmp_path,
        {"test_pre.py": f'def test_pre(): assert False, "{message}"\n'},
        {"test_unrelated.py": "def test_unrelated(): assert True\n"},
    )
    out = _run(repo)
    assert "ALSO fail at the merge-base" in out, out
    assert "are new on this branch" not in out, (
        f"a hyphen in the MESSAGE must not make a pre-existing failure look new\n{out}"
    )


def test_an_id_whose_PARAMETER_contains_a_dash_survives(tmp_path):
    # The other direction, and the reason the greedy `sed 's/ - .*//'` was
    # wrong: a node id may contain " - " inside its [parameters].
    repo = _make_repo(
        tmp_path,
        {
            "test_pre.py": (
                "import pytest\n"
                '@pytest.mark.parametrize("v", ["a - b"])\n'
                "def test_dash(v): assert False\n"
            )
        },
        {"test_unrelated.py": "def test_unrelated(): assert True\n"},
    )
    out = _run(repo)
    assert "ALSO fail at the merge-base" in out, out


# --- never announce an answer it does not have ---------------------------


def test_an_all_unresolved_run_says_so_instead_of_saying_they_are_yours(tmp_path):
    # The report arms were gated on PRE_COUNT rather than on what was actually
    # RESOLVED, so a run where nothing could be checked printed the confident
    # headline "None of these fail at the merge-base — all N are new on this
    # branch" and then contradicted itself two lines later. The headline is the
    # part people read.
    #
    # Here the module imports cleanly on the branch and errors at the base, so
    # the merge-base run yields neither a pass nor a failure.
    repo = _make_repo(
        tmp_path,
        {"test_x.py": "import totally_missing_module_xyz\ndef test_x(): pass\n"},
        {"test_x.py": "def test_x(): assert False\n"},
    )
    out = _run(repo)
    assert "NOT" in out and "attributed" in out, out
    assert "are new on this branch" not in out, (
        f"nothing was resolved, so nothing may be called new\n{out}"
    )


def test_an_id_that_cannot_be_collected_HERE_is_unresolved_not_yours(tmp_path):
    # The backstop against the next parsing bug. An id that is absent at the
    # merge-base is normally a test the branch added — but it is also exactly
    # what a MIS-PARSED id looks like, and the two are indistinguishable at
    # that point. Both shipped parsing bugs ended by blaming the pusher for
    # this script's own defect.
    #
    # The id just failed in THIS tree, so it must be collectible here. If it
    # is not, the parse is what is broken. Simulated by a conftest that emits
    # a FAILED line for a test that does not exist — which is precisely the
    # shape a bad parse produces.
    repo = _make_repo(
        tmp_path,
        {"test_pre.py": "def test_pre(): assert False\n"},
        {
            "conftest.py": (
                "def pytest_sessionfinish(session, exitstatus):\n"
                "    print('\\nFAILED tests/ghost.py::test_ghost - simulated bad parse')\n"
            )
        },
    )
    out = _run(repo)
    assert "could not be checked" in out, out
    # The whole report is the assertion: with the ghost unresolved there is no
    # "new on this branch" section at all, so nothing was blamed on the pusher.
    assert "are new on this branch" not in out, (
        f"an uncollectible id must be reported as unknown, never as yours\n{out}"
    )


# --- every early exit still says something -------------------------------


def test_an_unresolvable_merge_base_says_it_is_not_attributed(tmp_path):
    repo = _make_repo(
        tmp_path,
        {"test_ok.py": "def test_ok(): assert True\n"},
        {"test_a.py": "def test_a(): assert False\n"},
    )
    out = _run(repo, merge_base="")
    assert "NOT attributed" in out, out


def test_head_being_the_merge_base_is_reported_as_all_pre_existing(tmp_path):
    repo = _make_repo(tmp_path, {"test_a.py": "def test_a(): assert False\n"}, {})
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()
    out = _run(repo, merge_base=head)
    assert "pre-existing" in out, out


def test_a_missing_rust_extension_says_it_is_not_attributed(tmp_path):
    # Without the built extension the merge-base run is not comparable, and
    # every failure would look pre-existing — the wrong answer delivered
    # confidently.
    repo = _make_repo(
        tmp_path,
        {"test_ok.py": "def test_ok(): assert True\n"},
        {"test_a.py": "def test_a(): assert False\n"},
    )
    for so in (repo / "python" / "djust").glob("_rust*.so"):
        so.unlink()
    out = _run(repo)
    assert "NOT attributed" in out, out


# --- the interpreter is resolved the same way on both runs ---------------


def test_the_venv_wrapper_resolves_from_inside_a_linked_worktree(tmp_path):
    # The merge-base run hardcoded "$REPO_ROOT/.venv/bin/python" while the main
    # run went through run-with-venv-python.sh. A linked worktree — where most
    # agent work in this repo happens — has no .venv of its own, so every id
    # failed to execute, landed in UNRESOLVED, and was then announced as "new
    # on this branch". Parallel-path drift (#1646) inside one 160-line file.
    #
    # The fixture rewrites that very line, so it cannot see this. Assert the
    # mechanism directly instead.
    wt = tmp_path / "wt"
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(wt), "HEAD"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    try:
        assert not (wt / ".venv/bin/python").exists(), "precondition: a worktree has no venv"
        resolved = subprocess.run(
            ["bash", "scripts/run-with-venv-python.sh", "--print"],
            cwd=wt,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert resolved and Path(resolved).is_file(), (
            f"the wrapper must resolve a real interpreter from a worktree; got {resolved!r}"
        )
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(wt)], cwd=ROOT, capture_output=True
        )


def test_the_merge_base_run_does_not_hardcode_a_venv_path():
    src = SCRIPT.read_text()
    assert "--print" in src, "the merge-base interpreter must come from the wrapper"
    body = src[src.index("PYBIN=") :]
    assert '"$PYBIN" -m pytest' in body, "the per-id run must use the resolved interpreter"
