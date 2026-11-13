"""Nothing skipped on the PR Django series goes unexecuted before merge (#4074).

Before merge the Python shards run on Django 5.2 only. ``python-tests-django6-only`` runs on
Django 6.1 every test those shards SKIPPED, from the ledger they write (``tests/skip_ledger.py``).
These tests pin both halves: the ledger records every kind of skip (marker, module-level, fixture,
test body) and nothing else, the selection runs exactly the listed tests, and the workflow wires
the 5.2 shards' ledgers into that job and gates the merge on it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/test.yml"

INI = """
[pytest]
markers =
    sweep: sweep
"""

PROJECT = """
import pytest

skip_mod = pytest.mark.skipif(True, reason="needs a newer framework")

@skip_mod
def test_marker():
    pass

def test_body():
    pytest.skip("not on this version")

@pytest.fixture
def needs_it():
    pytest.skip("fixture says no")

def test_fixture(needs_it):
    pass

@pytest.mark.xfail(reason="known")
def test_xfail():
    assert False

@pytest.mark.sweep
def test_unsampled():
    pytest.skip("not in this run's sample")

def test_runs():
    pass
"""


def _run(pytester: pytest.Pytester, monkeypatch, env: dict[str, str], *args: str):
    # The outer run may itself be recording (CI sets DJUST_RECORD_SKIPS): the inner run must
    # neither inherit that target nor see the outer session's skips.
    monkeypatch.delenv("DJUST_RECORD_SKIPS", raising=False)
    monkeypatch.delenv("DJUST_ONLY_IDS_FILE", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return pytester.runpytest(
        "-p", "tests.skip_ledger", "-p", "no:cacheprovider", "-p", "no:django", *args
    )


@pytest.fixture
def project(pytester):
    pytester.makeini(INI)
    pytester.makepyfile(test_p=PROJECT)
    pytester.makepyfile(
        test_mod="import pytest\npytest.skip('module needs 6.0', allow_module_level=True)\n"
    )
    return pytester


def test_the_ledger_records_every_kind_of_skip_and_nothing_else(project, monkeypatch):
    ledger = project.path / "skipped.txt"
    result = _run(project, monkeypatch, {"DJUST_RECORD_SKIPS": str(ledger)})
    result.assert_outcomes(passed=1, skipped=5, xfailed=1)
    assert ledger.read_text().splitlines() == sorted(
        ["test_mod.py", "test_p.py::test_body", "test_p.py::test_fixture", "test_p.py::test_marker"]
    )


def test_the_ledger_is_written_once_under_xdist(project, monkeypatch):
    ledger = project.path / "skipped.txt"
    _run(project, monkeypatch, {"DJUST_RECORD_SKIPS": str(ledger)}, "-n", "2")
    assert "test_p.py::test_body" in ledger.read_text().splitlines()


def test_the_selection_runs_exactly_the_listed_tests(project, monkeypatch):
    ids = project.path / "ids.txt"
    ids.write_text("test_p.py::test_runs\ntest_p.py::test_body\n")
    result = _run(project, monkeypatch, {"DJUST_ONLY_IDS_FILE": str(ids)}, "-q")
    # test_mod skips itself at collection (one more skip, not an item); 4 items deselected
    result.assert_outcomes(passed=1, skipped=2, deselected=4)


def test_a_listed_module_selects_every_test_in_it(pytester, monkeypatch):
    pytester.makepyfile(test_whole="def test_a():\n    pass\ndef test_b():\n    pass\n")
    pytester.makepyfile(test_other="def test_c():\n    pass\n")
    ids = pytester.path / "ids.txt"
    ids.write_text("test_whole.py\n")
    result = _run(pytester, monkeypatch, {"DJUST_ONLY_IDS_FILE": str(ids)}, "-q")
    result.assert_outcomes(passed=2, deselected=1)


def test_an_empty_or_missing_list_is_refused(project, monkeypatch):
    empty = project.path / "empty.txt"
    empty.write_text("\n")
    result = _run(project, monkeypatch, {"DJUST_ONLY_IDS_FILE": str(empty)})
    assert result.ret != 0 and "refusing to run nothing" in result.stderr.str()
    result = _run(project, monkeypatch, {"DJUST_ONLY_IDS_FILE": str(project.path / "nope")})
    assert result.ret != 0 and "cannot read it" in result.stderr.str()


# --- the workflow wiring ------------------------------------------------------------------------


def _jobs() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]


def _step(job: dict, name: str) -> dict:
    return next(s for s in job["steps"] if s.get("name", "").startswith(name))


def test_every_pr_shard_records_and_uploads_its_skips():
    shards = _jobs()["python-tests"]
    run = _step(shards, "Run Python tests")
    assert run["env"]["DJUST_RECORD_SKIPS"] == "${{ runner.temp }}/skipped.txt"
    upload = _step(shards, "Upload the tests this shard skipped")
    cond = str(upload["if"])
    # the PR cell: 3.12 on the PR Django series, every shard group, even when red
    assert "always()" in cond and "matrix.python-version == '3.12'" in cond
    assert "matrix.django-version == '5.2'" in cond
    pr_series = str(shards["strategy"]["matrix"]["django-version"]).rstrip("} ")
    assert pr_series.endswith("""fromJSON('["5.2"]')"""), "the upload must read the PR series"
    assert upload["with"]["name"] == "skipped-on-django-5.2-shard-${{ matrix.group }}"
    assert upload["with"]["if-no-files-found"] == "error"


def test_the_django6_job_runs_every_listed_skip_on_61_and_gates_the_merge():
    jobs = _jobs()
    job = jobs["python-tests-django6-only"]
    assert "python-tests" in job["needs"]
    download = _step(job, "Download what the Django 5.2 shards skipped")
    assert download["with"]["pattern"] == "skipped-on-django-5.2-shard-*"
    install = _step(job, "Install Python dependencies, Django 6.1")
    assert '"Django~=6.1.0"' in install["run"] and 'startswith("6.1.")' in install["run"]
    run = _step(job, "Run on Django 6.1")
    groups = jobs["python-tests"]["strategy"]["matrix"]["group"]
    assert f'"$lists" -ne {len(groups)}' in run["run"], "every shard's list must be present"
    assert "DJUST_ONLY_IDS_FILE" in run["env"] and '> "$DJUST_ONLY_IDS_FILE"' in run["run"]
    assert "tests/ python/tests/ python/djust/tests/" in run["run"]
    summary = jobs["test-summary"]
    assert "python-tests-django6-only" in summary["needs"]
    chain = _step(summary, "Check test results")["run"]
    assert '"${{ needs.python-tests-django6-only.result }}" == "success"' in chain


def test_the_ledger_and_selection_plugins_are_loaded_for_every_root():
    text = (ROOT / "conftest.py").read_text()
    assert '"tests.skip_ledger"' in text
    assert os.path.exists(ROOT / "tests/skip_ledger.py")
