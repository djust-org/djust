"""A test that rolls its database back leaves no rows behind for the next one (#3782).

``@pytest.mark.django_db`` (and the ``db`` fixture) wrap the TEST THREAD's connection in a
transaction and roll it back. A write that runs on another thread does not go through that
connection: ``sync_to_async(session.save)()`` runs on asgiref's executor thread, which has its own
connection, so the row autocommits there and no rollback removes it. Nothing fails at the polluter.
A later test that reads the table by position (``User.objects.first()``) reads the stray row, and
which tests share a pytest process is the scheduler's choice, so it fails in one cell of one CI run
(main, run 41774238410: a leftover ``User`` gave ``obj.users.first.password`` the wrong value).

The ``User`` shape is pinned at the source by ``python/tests/test_user_rows_do_not_leak_between_tests.py``
(an AST scan: such a test must be ``django_db(transaction=True)``). That scan cannot see a write
reached through a helper (``await sync_to_async(_make_db_session)()``), and measured over the suite
it found 5 tests where this plugin found ~300 in ~50 files, every one a ``Session`` row. Marking those
``transaction=True`` would flush the database after each, a cost of tens of milliseconds a test; this
plugin removes the row instead.

What it does, per test that holds a ROLLED-BACK database (``django_db`` without ``transaction=True``,
or the ``db`` fixture; a transactional test is flushed by Django, a ``django.test.TestCase`` subclass
manages its own atomics and is not covered):

1. before the test's own setup (so outside its transaction), it records the primary keys of the
   tracked tables (``User``, ``Group``, ``Permission``, ``Session``);
2. after the whole teardown, i.e. after the rollback, anything in those tables that was not there
   is a row some other connection committed during the test. It deletes them and counts them.

A test's OWN rows are gone after the rollback, so only another connection's writes can show up in
step 2 (a fixture's thread write during setup is one, and is swept too). The first database test of a
process is snapshotted when ``django_db_setup`` finishes, still before its transaction opens. The
snapshot is taken outside the test's transaction on purpose: see ``pytest_runtest_setup``.

It never fails or changes a test: sweeping is the cure and the terminal summary is the attribution
("these tests commit rows on another connection"), so the next person who wants the polluter fixed
at the source can find it. Under xdist each worker ships its list to the controller through
``config.workeroutput``, as ``tests/timing_report.py`` does.

Registered in the root ``conftest.py``; ``tests/test_db_row_isolation_3782.py`` pins it.
"""

from __future__ import annotations

from collections import Counter
from typing import Dict, List, Optional, Set, Tuple

import pytest

SECTION = "djust db row isolation (#3782)"
WORKEROUTPUT_KEY = "djust_db_row_isolation"

#: Fixtures that make a test transactional (flushed, not rolled back) or that need no sweep.
TRANSACTIONAL_FIXTURES = {
    "transactional_db",
    "live_server",
    "django_db_reset_sequences",
    "django_db_serialized_rollback",
}
#: Fixtures that give a test a rolled-back database.
DB_FIXTURES = {"db"}

#: How many of the worst files the summary names.
SUMMARY_FILES = 8

Snapshot = Dict[str, Set[object]]
#: ``(nodeid, table label, rows swept)``
Leak = Tuple[str, str, int]


def tracked_models() -> List[Tuple[str, type]]:
    """``[(label, model)]`` for the tables a thread write is known to leak into."""
    from django.apps import apps
    from django.contrib.auth import get_user_model

    models = [("User", get_user_model())]
    for label, app, name in (
        ("Group", "django.contrib.auth", "auth.Group"),
        ("Permission", "django.contrib.auth", "auth.Permission"),
        ("Session", "django.contrib.sessions", "sessions.Session"),
    ):
        if apps.is_installed(app):
            models.append((label, apps.get_model(name)))
    return models


def holds_a_rolled_back_database(item: pytest.Item) -> bool:
    """True for a function test whose database is rolled back, not flushed."""
    names = set(getattr(item, "fixturenames", ()))
    marker = item.get_closest_marker("django_db")
    if marker is None and not (names & DB_FIXTURES):
        return False
    transactional = bool(names & TRANSACTIONAL_FIXTURES)
    if marker is not None:
        from pytest_django.fixtures import validate_django_db

        marked_transactional, reset_sequences, *_ = validate_django_db(marker)
        transactional = transactional or marked_transactional or reset_sequences
    cls = getattr(item, "cls", None)
    if cls is not None:
        from django.test import SimpleTestCase

        if issubclass(cls, SimpleTestCase):  # a Django unittest: its own atomics
            return False
    return not transactional


def snapshot() -> Snapshot:
    return {
        label: set(model._default_manager.values_list("pk", flat=True))
        for label, model in tracked_models()
    }


def sweep(before: Snapshot) -> Tuple[Dict[str, int], List[str]]:
    """Delete rows that are in the tables now but were not in ``before``.

    Returns ``({label: rows swept}, [errors])``. A delete that Django refuses (a protected
    foreign key) is reported, not raised.
    """
    swept: Dict[str, int] = {}
    errors: List[str] = []
    for label, model in tracked_models():
        if label not in before:
            continue
        now = set(model._default_manager.values_list("pk", flat=True))
        stray = now - before[label]
        if not stray:
            continue
        try:
            model._default_manager.filter(pk__in=stray).delete()
        except Exception as exc:  # a diagnostic plugin must not fail a test
            errors.append("%s: could not delete %d stray row(s): %r" % (label, len(stray), exc))
            continue
        swept[label] = len(stray)
    return swept, errors


class DbRowIsolation:
    def __init__(self, config: pytest.Config) -> None:
        self.config = config
        self.is_worker = hasattr(config, "workerinput")  # stamped on xdist workers only
        self.before: Dict[str, Snapshot] = {}
        #: True once ``django_db_setup`` has built the test database (see ``pytest_runtest_setup``).
        self.test_database_exists = False
        self.setting_up: Optional[pytest.Item] = None
        self.leaks: List[Leak] = []
        self.errors: List[str] = []
        #: Controller only: worker id -> its ``{"leaks": [...], "errors": [...]}``.
        self.workers: Dict[str, Optional[dict]] = {}

    def _blocker(self):
        from pytest_django.plugin import blocking_manager_key

        return self.config.stash[blocking_manager_key]

    # -- hooks -------------------------------------------------------------------

    def _record(self, item: pytest.Item) -> None:
        """Snapshot for ``item`` if it holds a rolled-back database. Never raises."""
        try:
            if holds_a_rolled_back_database(item):
                with self._blocker().unblock():
                    self.before[item.nodeid] = snapshot()
        except Exception as exc:  # never the reason a test fails
            self.errors.append("%s: snapshot failed: %r" % (item.nodeid, exc))

    @pytest.hookimpl(hookwrapper=True)
    def pytest_fixture_setup(self, fixturedef: object, request: object):
        outcome = yield
        if getattr(fixturedef, "argname", "") == "django_db_setup" and outcome.excinfo is None:
            # The test database was just built, during the setup of the first database test of
            # this process. That test's own transaction opens after this fixture, so this is still
            # outside it: snapshot it here (it is the test that is being set up).
            self.test_database_exists = True
            if self.setting_up is not None:
                self._record(self.setting_up)

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_setup(self, item: pytest.Item):
        # BEFORE the test's own setup: the snapshot must not be read inside the test's
        # transaction. Under SQLite's shared cache a SELECT in an open transaction holds a table
        # lock, and the executor thread's INSERT into the same table then fails with
        # "database table is locked" (found the first time this ran, on a plain django_db test
        # that writes a Session on an executor thread).
        #
        # Before the test database exists there is nothing to read (and reading would reach
        # whatever DATABASES points at); the fixture hook above takes that one.
        self.setting_up = item
        if self.test_database_exists:
            self._record(item)
        yield
        self.setting_up = None

    def pytest_runtest_logfinish(self, nodeid: str, location: object) -> None:
        before = self.before.pop(nodeid, None)
        if before is None:
            return
        try:
            with self._blocker().unblock():
                swept, errors = sweep(before)
        except Exception as exc:
            self.errors.append("%s: sweep failed: %r" % (nodeid, exc))
            return
        for label, count in swept.items():
            self.leaks.append((nodeid, label, count))
        self.errors.extend("%s: %s" % (nodeid, e) for e in errors)

    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        if self.is_worker:
            self.config.workeroutput[WORKEROUTPUT_KEY] = {  # type: ignore[attr-defined]
                "leaks": self.leaks,
                "errors": self.errors,
            }

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node: object, error: object) -> None:
        output = getattr(node, "workeroutput", None) or {}
        worker_id = getattr(getattr(node, "gateway", None), "id", repr(node))
        self.workers[worker_id] = output.get(WORKEROUTPUT_KEY)

    # -- reporting ---------------------------------------------------------------

    def collected(self) -> Tuple[List[Leak], List[str]]:
        if not self.workers:
            return self.leaks, self.errors
        leaks: List[Leak] = []
        errors: List[str] = []
        for data in self.workers.values():
            if data:
                leaks.extend(tuple(x) for x in data["leaks"])  # type: ignore[misc]
                errors.extend(data["errors"])
        return leaks, errors

    def lines(self) -> List[str]:
        leaks, errors = self.collected()
        out: List[str] = []
        if leaks:
            tests = {nodeid for nodeid, _, _ in leaks}
            tables: Counter = Counter()
            by_file: Dict[str, Set[str]] = {}
            for nodeid, label, count in leaks:
                tables[label] += count
                by_file.setdefault(nodeid.split("::", 1)[0], set()).add(nodeid)
            files = Counter({path: len(ids) for path, ids in by_file.items()})
            out.append(
                "swept %d row(s) (%s) that %d test(s) committed on another connection under a "
                "rolled-back django_db; mark such a test transaction=True to fix it at the source"
                % (
                    sum(tables.values()),
                    ", ".join("%s %d" % kv for kv in sorted(tables.items())),
                    len(tests),
                )
            )
            out.extend(
                "  %s: %d test(s)" % (path, n) for path, n in files.most_common(SUMMARY_FILES)
            )
            if len(files) > SUMMARY_FILES:
                out.append("  ... and %d more file(s)" % (len(files) - SUMMARY_FILES))
        out.extend("guard error: %s" % e for e in errors[:SUMMARY_FILES])
        return out

    def pytest_terminal_summary(self, terminalreporter, exitstatus: int) -> None:
        if self.is_worker or self.config.option.collectonly:
            return
        lines = self.lines()
        if not lines:
            return
        terminalreporter.write_sep("-", SECTION)
        for line in lines:
            terminalreporter.write_line(line)


def pytest_configure(config: pytest.Config) -> None:
    config.pluginmanager.register(DbRowIsolation(config), "djust_db_row_isolation")
