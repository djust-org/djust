"""tests/db_row_isolation.py: rows another connection commits do not outlive their test (#3782).

The failure shape: an ``async`` test under a plain ``@pytest.mark.django_db`` writes a row through
``sync_to_async``. asgiref runs the callable on an executor thread with its own connection, so the
row autocommits there and the test's rollback never removes it. The next test on the same pytest
process sees it. That is exactly what ``test_the_polluter_is_seen_by_the_next_test_without_the_plugin``
shows with the plugin OFF, and what ``test_the_next_test_sees_a_clean_table`` shows with it ON.

Each check runs a throwaway suite in a subprocess (a real pytest session with pytest-django and the
demo project's settings), because the thing under test is what happens between two TESTS of a
session; a unit test of the sweep function would not show that the hooks run in the right order
(snapshot inside the transaction, sweep after the rollback).

Gate-off, per mechanism (#2135: the test that goes red when ONLY that mechanism is removed):

- ``sweep`` does not delete -> ``test_the_next_test_sees_a_clean_table``;
- ``holds_a_rolled_back_database`` says no for everything, or ``snapshot`` returns nothing ->
  ``test_the_next_test_sees_a_clean_table``;
- ``holds_a_rolled_back_database`` says yes for a transactional test ->
  ``test_a_transactional_test_and_a_tests_own_rows_are_not_reported``;
- the ``workeroutput`` relay or the ``pytest_testnodedown`` read is dropped ->
  ``test_under_xdist_the_controller_reports_what_the_workers_swept``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

#: One test writes on the executor thread under a plain django_db; the next reads the tables.
PAIR = """
import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import Group
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session


def _write():
    store = SessionStore()
    store.create()
    Group.objects.create(name="leaked-by-3782")


@pytest.mark.asyncio
@pytest.mark.django_db
async def test_1_commits_on_the_executor_thread():
    await sync_to_async(_write)()


@pytest.mark.django_db
def test_2_reads_the_tables_by_position():
    assert Session.objects.count() == 0, "a Session row from the test before is still here"
    assert not Group.objects.filter(name="leaked-by-3782").exists()
"""

#: Neither of these leaves a row another test could read, and neither may be reported.
QUIET = """
import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import Group
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_transactional_test_is_flushed_by_django():
    await sync_to_async(SessionStore().create)()


@pytest.mark.django_db
def test_a_tests_own_rows_roll_back_with_it():
    Group.objects.create(name="own-row")
    SessionStore().create()


@pytest.mark.django_db
def test_and_leave_nothing_behind():
    assert Session.objects.count() == 0
    assert not Group.objects.filter(name="own-row").exists()
"""


def _run(pytester, monkeypatch, *args, **files):
    """A throwaway pytest session against the demo project's settings."""
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "demo_project.settings")
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join([str(REPO), str(REPO / "examples" / "demo_project"), str(REPO / "python")]),
    )
    for name, source in files.items():
        pytester.makepyfile(**{name: source})
    return pytester.runpytest_subprocess("-p", "no:cacheprovider", "-W", "ignore", *args)


def test_the_polluter_is_seen_by_the_next_test_without_the_plugin(pytester, monkeypatch):
    """The premise: with nothing sweeping, the second test fails. Without this, the next test
    could pass for a reason other than the sweep and prove nothing."""
    result = _run(pytester, monkeypatch, test_pair=PAIR)
    result.assert_outcomes(passed=1, failed=1)
    result.stdout.fnmatch_lines(["*a Session row from the test before is still here*"])


def test_the_next_test_sees_a_clean_table(pytester, monkeypatch):
    result = _run(pytester, monkeypatch, "-p", "tests.db_row_isolation", test_pair=PAIR)
    result.assert_outcomes(passed=2)
    # Attributed: the summary names the table, the count, the test and its file.
    result.stdout.fnmatch_lines(
        ["*djust db row isolation (#3782)*", "swept 2 row(s) (Group 1, Session 1) that 1 test(s)*"]
    )
    result.stdout.fnmatch_lines(["  test_pair.py: 1 test(s)"])


def test_a_transactional_test_and_a_tests_own_rows_are_not_reported(pytester, monkeypatch):
    result = _run(pytester, monkeypatch, "-p", "tests.db_row_isolation", test_quiet=QUIET)
    result.assert_outcomes(passed=3)
    result.stdout.no_fnmatch_line("*swept*")
    result.stdout.no_fnmatch_line("*djust db row isolation*")


def test_under_xdist_the_controller_reports_what_the_workers_swept(pytester, monkeypatch):
    result = _run(
        pytester,
        monkeypatch,
        "-p",
        "tests.db_row_isolation",
        "-n",
        "2",
        "--dist",
        "loadfile",
        test_pair=PAIR,
        test_quiet=QUIET,
    )
    result.assert_outcomes(passed=5)
    result.stdout.fnmatch_lines(
        ["*djust db row isolation (#3782)*", "swept 2 row(s) (Group 1, Session 1) that 1 test(s)*"]
    )
    result.stdout.fnmatch_lines(["  test_pair.py: 1 test(s)"])
    result.stdout.no_fnmatch_line("  test_quiet.py*")


def test_the_plugin_is_registered_for_the_whole_suite():
    """It does nothing unless the root conftest loads it."""
    source = (REPO / "conftest.py").read_text(encoding="utf-8")
    assert '"tests.db_row_isolation"' in source


@pytest.mark.parametrize(
    "marks,fixtures,rolled_back",
    [
        ("pytest.mark.django_db", (), True),
        ("pytest.mark.django_db(transaction=True)", (), False),
        ("pytest.mark.django_db(reset_sequences=True)", (), False),
        (None, ("db",), True),
        (None, ("transactional_db",), False),
        (None, ("db", "transactional_db"), False),
        (None, (), False),
    ],
)
def test_which_tests_hold_a_rolled_back_database(marks, fixtures, rolled_back):
    """Only a rolled-back database is swept; a flushed one needs nothing and must not be touched."""
    from tests.db_row_isolation import holds_a_rolled_back_database

    class Item:
        cls = None
        fixturenames = fixtures

        def get_closest_marker(self, name):
            if name != "django_db" or marks is None:
                return None
            namespace = {"pytest": pytest}
            mark = eval(marks, namespace)  # noqa: S307 - a literal from this table
            return getattr(mark, "mark", mark)

    assert holds_a_rolled_back_database(Item()) is rolled_back
