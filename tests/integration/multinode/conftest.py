"""Fixtures of the multi-process state-survival tests (#4000)."""

from __future__ import annotations

import importlib.util
import shutil
from typing import Iterator, Optional, Sequence, Set, Tuple

import pytest

from tests.integration.multinode import survival_table
from tests.integration.multinode.harness import Cluster, redis_server


class SurvivalRecorder:
    """Binds one test to the table cells that name it, both ways.

    ``observe(table, row, column, outcome)`` fails unless the cell names this
    test and says exactly ``outcome``. When the test passes, every cell that
    names it (within ``scope(rows=...)`` for a parametrized test) must have been
    observed.
    """

    def __init__(self, test: str) -> None:
        self.test = test
        self.observed: Set[Tuple[str, str, str]] = set()
        self.rows: Optional[Set[str]] = None
        self.columns: Optional[Set[str]] = None

    def scope(self, rows: Sequence[str] = (), columns: Sequence[str] = ()) -> None:
        """For one parameter of a parametrized test: the cells it proves."""
        self.rows = set(rows) or None
        self.columns = set(columns) or None

    def observe(self, table: str, row: str, column: str, outcome: str) -> None:
        key = (table, row, column)
        entry = survival_table.ALL_CELLS.get(key)
        assert isinstance(entry, survival_table.Cell), "no outcome cell %r in the table" % (key,)
        assert entry.test == self.test, "cell %r is proved by %s, not %s" % (
            key,
            entry.test,
            self.test,
        )
        assert outcome == entry.outcome, "cell %r: the table says %r, the run shows %r" % (
            key,
            entry.outcome,
            outcome,
        )
        self.observed.add(key)

    def missing(self) -> Set[Tuple[str, str, str]]:
        expected = set(survival_table.cells_of_test(self.test))
        if self.rows is not None:
            expected = {key for key in expected if key[1] in self.rows}
        if self.columns is not None:
            expected = {key for key in expected if key[2] in self.columns}
        return expected - self.observed


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    setattr(item, "_survival_" + report.when, report)


@pytest.fixture
def survival(request) -> Iterator[SurvivalRecorder]:
    recorder = SurvivalRecorder(request.node.originalname)
    yield recorder
    report = getattr(request.node, "_survival_call", None)
    if report is not None and report.passed:
        missing = recorder.missing()
        assert not missing, "%s passed without observing its table cells %s" % (
            recorder.test,
            sorted(missing),
        )


def _need_redis() -> None:
    if shutil.which("redis-server") is None:
        pytest.skip("redis-server is not installed: the Redis rows are not exercised here")
    if importlib.util.find_spec("channels_redis") is None:
        pytest.skip("channels_redis is not installed: the Redis rows are not exercised here")


@pytest.fixture(scope="module")
def redis_url() -> Iterator[str]:
    _need_redis()
    with redis_server() as url:
        yield url


@pytest.fixture(scope="module")
def db_cluster() -> Iterator[Cluster]:
    """Two processes, database sessions, an in-memory channel layer each."""
    with Cluster("db", "memory", None) as cluster:
        yield cluster


@pytest.fixture(scope="module")
def redis_cluster(redis_url) -> Iterator[Cluster]:
    """Two processes, database sessions, one Redis channel layer and presence."""
    with Cluster("db", "redis", redis_url) as cluster:
        yield cluster
