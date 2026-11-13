"""Run on Django 6.1 exactly the tests the Django 5.2 shards skipped (#4074).

Pull requests, the merge queue and the push to ``main`` run the Python shards on ONE Django
series (5.2, the LTS the dev environment uses); 6.0 and 6.1 run nightly. About a hundred tests
only exist on Django 6 (template partials, the 6.1 CSP nonce tag, ...) and skip on 5.2, so a
5.2-only PR run would execute none of them. The ``python-tests (Django 6.1, skipped on 5.2)``
job closes that gap from what the 5.2 shards actually did, not from a hand list or a guess at
skip conditions: a marker, a module-level ``pytest.skip``, a fixture that skips and a skip in
the test body are all the same to it.

* ``DJUST_RECORD_SKIPS=<file>``: the controller writes the node id of every test that was
  skipped (not an ``xfail``, and not a sweep case the sample left out: ``tests/sweep_sample.py``)
  to ``<file>``, one per line, at the end of the session.
  A module that skips itself at import is recorded by its module id.
* ``DJUST_ONLY_IDS_FILE=<file>``: keep only the collected tests whose node id (or module id) is
  listed, and deselect the rest. ``DJUST_ONLY_IDS_FILE`` that is missing or empty is an error: an empty
  list would make the job green while running nothing.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

RECORD_ENV = "DJUST_RECORD_SKIPS"
ONLY_ENV = "DJUST_ONLY_IDS_FILE"


def _is_worker(config: pytest.Config) -> bool:
    return hasattr(config, "workerinput")


class _Ledger:
    """One per session: a module-level list would be shared with any nested in-process run
    (``pytester``), which then writes the outer session's skips as its own."""

    def __init__(self, target: str) -> None:
        self.target = target
        self.skipped: list[str] = []

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if not report.skipped or hasattr(report, "wasxfail"):
            return
        # A sweep case the sample did not draw is not a Django-version skip: it runs nightly.
        if "sweep" in getattr(report, "keywords", {}):
            return
        self.skipped.append(report.nodeid)

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        # A module that skips itself at import (``pytest.skip(allow_module_level=True)``) never
        # reaches the run phase: its collect report is the only trace, keyed by the module's id.
        if report.skipped and report.nodeid:
            self.skipped.append(report.nodeid)

    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        if not _is_worker(session.config):
            Path(self.target).write_text("".join(f"{n}\n" for n in sorted(set(self.skipped))))


def pytest_configure(config: pytest.Config) -> None:
    target = os.environ.get(RECORD_ENV)
    if target:
        config.pluginmanager.register(_Ledger(target), "djust-skip-ledger")


def read_ids(path: str) -> set[str]:
    try:
        text = Path(path).read_text()
    except OSError as exc:
        raise pytest.UsageError(f"{ONLY_ENV}={path}: cannot read it ({exc})") from exc
    ids = {line.strip() for line in text.splitlines() if line.strip()}
    if not ids:
        raise pytest.UsageError(f"{ONLY_ENV}={path} lists no tests: refusing to run nothing")
    return ids


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    path = os.environ.get(ONLY_ENV)
    if not path:
        return
    wanted = read_ids(path)
    # A listed module id (a module that skipped itself whole) selects every test in it.
    modules = {w for w in wanted if "::" not in w}

    def listed(item: pytest.Item) -> bool:
        return item.nodeid in wanted or item.nodeid.split("::", 1)[0] in modules

    keep = [item for item in items if listed(item)]
    drop = [item for item in items if not listed(item)]
    if drop:
        config.hook.pytest_deselected(items=drop)
    items[:] = keep
