"""One flushed JSON line per test — what makes a segfault recoverable.

``RecordingResult`` emits ``{"event": "start", "id": ...}`` before a test and
``{"event": "result", "id", "status", "message", "touched", "ms"}`` after it,
appending to the file named by ``$DJUST_SUITE_OUT`` and flushing per line. A
child that dies mid-test leaves a ``start`` with no ``result``; the outer
loop reads that as the crash victim, writes it plus every finished id to
``$DJUST_SUITE_SKIP_IDS``, and relaunches. ``DjustSuiteRunner.build_suite``
drops the ids in that file.

Status precedence is ERROR > FAIL > OK. A subtest failure marks its parent
(unittest never calls ``addSuccess`` for a test whose subtest failed); an
unexpected success is a FAIL, as unittest treats it. ``message`` is
``"{ExcType}: {first line}"`` with addresses and temp paths normalised and a
300-character cap, so two runs diff cleanly.
"""

from __future__ import annotations

import json
import os
import re
import time
import unittest
from typing import IO, Any

from django.test.runner import DiscoverRunner
from django.test.utils import iter_test_cases

from .adapter import TOUCH

_ADDR_RE = re.compile(r"0x[0-9a-fA-F]+")
_TMP_RE = re.compile(r"/(?:private/)?(?:var/folders|tmp)/[^\s'\"]*")
_MESSAGE_CAP = 300
_RANK = {"OK": 0, "SKIP": 0, "XFAIL": 0, "FAIL": 1, "ERROR": 2}


def normalize(message: str) -> str:
    """Make a failure message stable across runs (addresses, temp paths)."""
    message = _ADDR_RE.sub("0x…", message)
    message = _TMP_RE.sub("<tmp>", message)
    return message


def first_line(err: tuple[Any, Any, Any]) -> str:
    """``"{ExcType}: {first line of str(value)}"``, normalised and capped."""
    exc_type, value, _tb = err
    lines = str(value).strip().splitlines()
    head = lines[0] if lines else ""
    return normalize("%s: %s" % (exc_type.__name__, head))[:_MESSAGE_CAP]


class RecordingResult(unittest.TextTestResult):
    """``TextTestResult`` that also writes one JSON line per test."""

    def __init__(self, stream: Any, descriptions: bool, verbosity: int, **kwargs: Any) -> None:
        super().__init__(stream, descriptions, verbosity, **kwargs)
        self.records: list[dict[str, Any]] = []
        self._sink: IO[str] | None = None
        self._current: dict[str, Any] = {}
        self._touch_before = 0
        self._started_at = 0.0

    # -- the sink -------------------------------------------------------------
    def _emit(self, record: dict[str, Any]) -> None:
        self.records.append(record)
        if self._sink is None:
            path = os.environ.get("DJUST_SUITE_OUT")
            if not path:
                return
            self._sink = open(path, "a", encoding="utf-8")  # noqa: SIM115 — lives for the run
        self._sink.write(json.dumps(record) + "\n")
        self._sink.flush()

    # -- lifecycle ------------------------------------------------------------
    def startTest(self, test: unittest.TestCase) -> None:  # noqa: N802 — unittest API
        self._current = {"id": test.id(), "status": None, "message": ""}
        self._touch_before = TOUCH["count"]
        self._started_at = time.perf_counter()
        self._emit({"event": "start", "id": test.id()})
        super().startTest(test)

    def stopTest(self, test: unittest.TestCase) -> None:  # noqa: N802 — unittest API
        super().stopTest(test)
        record = self._current
        record["event"] = "result"
        if record["status"] is None:
            record["status"] = "OK"
        record["touched"] = TOUCH["count"] > self._touch_before
        record["ms"] = round((time.perf_counter() - self._started_at) * 1000, 1)
        self._emit(
            {
                "event": "result",
                "id": record["id"],
                "status": record["status"],
                "message": record["message"],
                "touched": record["touched"],
                "ms": record["ms"],
            }
        )

    def _mark(self, status: str, err: tuple[Any, Any, Any] | None = None) -> None:
        current = self._current.get("status")
        if current is not None and _RANK[status] <= _RANK[current]:
            return
        self._current["status"] = status
        if err is not None:
            self._current["message"] = first_line(err)

    # -- outcomes -------------------------------------------------------------
    def addSuccess(self, test: unittest.TestCase) -> None:  # noqa: N802
        super().addSuccess(test)
        self._mark("OK")

    def addFailure(self, test: unittest.TestCase, err: Any) -> None:  # noqa: N802
        super().addFailure(test, err)
        self._mark("FAIL", err)

    def addError(self, test: unittest.TestCase, err: Any) -> None:  # noqa: N802
        super().addError(test, err)
        self._mark("ERROR", err)

    def addSkip(self, test: unittest.TestCase, reason: str) -> None:  # noqa: N802
        super().addSkip(test, reason)
        if self._current.get("status") is None:
            self._current["status"] = "SKIP"
            self._current["message"] = normalize(str(reason))[:_MESSAGE_CAP]

    def addExpectedFailure(self, test: unittest.TestCase, err: Any) -> None:  # noqa: N802
        super().addExpectedFailure(test, err)
        if self._current.get("status") is None:
            self._current["status"] = "XFAIL"

    def addUnexpectedSuccess(self, test: unittest.TestCase) -> None:  # noqa: N802
        super().addUnexpectedSuccess(test)
        self._mark("FAIL", (AssertionError, AssertionError("unexpected success"), None))

    def addSubTest(self, test: unittest.TestCase, subtest: Any, err: Any) -> None:  # noqa: N802
        super().addSubTest(test, subtest, err)
        if err is not None:
            status = "FAIL" if issubclass(err[0], AssertionError) else "ERROR"
            self._mark(status, err)


class DjustSuiteRunner(DiscoverRunner):
    """``DiscoverRunner`` with the recording result and the id-skip filter."""

    def get_resultclass(self) -> type[unittest.TextTestResult]:
        return RecordingResult

    def build_suite(self, test_labels: Any = None, **kwargs: Any) -> unittest.TestSuite:
        suite = super().build_suite(test_labels, **kwargs)
        skip_file = os.environ.get("DJUST_SUITE_SKIP_IDS")
        if not skip_file or not os.path.exists(skip_file):
            return suite
        with open(skip_file, encoding="utf-8") as handle:
            skip = {line.strip() for line in handle if line.strip()}
        kept = [test for test in iter_test_cases(suite) if test.id() not in skip]
        return self.test_suite(kept)
