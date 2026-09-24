"""#2947: the log sanitizer must see records from ``djust.*`` child loggers.

Python runs a logger's filters only for records logged on that logger. The
filter used to sit on ``djust`` alone, so a CRLF argument logged through
``djust.websocket`` reached every handler raw. ``install_log_sanitizer()``
(called from ``DjustConfig.ready()``) attaches the filter to every framework
logger, existing or created later.
"""

from __future__ import annotations

import logging
import uuid

import pytest

from djust.security.log_sanitizer import DjustLogSanitizerFilter, install_log_sanitizer


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@pytest.fixture
def capture():
    handler = _Capture()
    root = logging.getLogger("djust")
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)


def _sanitizers(logger: logging.Logger) -> int:
    return sum(isinstance(f, DjustLogSanitizerFilter) for f in logger.filters)


def test_existing_child_logger_record_is_sanitized(capture):
    child = logging.getLogger("djust.websocket")
    install_log_sanitizer()
    child.warning("event %s", "evil\r\nFAKE LINE")
    assert capture.messages[-1] == "event evil FAKE LINE"


def test_child_logger_created_after_install_is_sanitized(capture):
    install_log_sanitizer()
    name = "djust.lazy_%s" % uuid.uuid4().hex
    late = logging.getLogger(name)
    assert _sanitizers(late) == 1
    late.warning("value %s", "a\nb\x1b[31mred")
    assert capture.messages[-1] == "value a bred"


def test_grandchild_via_get_child_is_covered(capture):
    install_log_sanitizer()
    name = "djust.mixins.child_%s" % uuid.uuid4().hex
    logging.getLogger("djust.mixins").getChild(name.rsplit(".", 1)[1])
    assert _sanitizers(logging.getLogger(name)) == 1


def test_install_is_idempotent():
    install_log_sanitizer()
    install_log_sanitizer()
    for name in ("djust", "djust.websocket", "djust.runtime"):
        assert _sanitizers(logging.getLogger(name)) == 1
    manager = logging.Logger.manager
    assert getattr(manager.getLogger, "_djust_log_sanitizer", False)
    # Wrapped once: the wrapper's inner function is not itself a wrapper.
    assert not getattr(manager.getLogger.__wrapped__, "_djust_log_sanitizer", False)


def test_loggers_outside_the_djust_namespace_are_untouched():
    install_log_sanitizer()
    for name in (
        "djustfoo_%s" % uuid.uuid4().hex,
        "django.djust.updates",
        "myapp.views_%s" % uuid.uuid4().hex,
    ):
        assert _sanitizers(logging.getLogger(name)) == 0


def test_exc_info_is_not_touched(capture):
    install_log_sanitizer()
    child = logging.getLogger("djust.runtime")
    records: list[logging.LogRecord] = []

    class _Keep(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    keep = _Keep()
    child.addHandler(keep)
    try:
        try:
            raise ValueError("line1\nline2")
        except ValueError:
            child.error("failed: %s", "x\ny", exc_info=True)
    finally:
        child.removeHandler(keep)
    assert records[-1].getMessage() == "failed: x y"
    assert records[-1].exc_info is not None
    assert "line1\nline2" in str(records[-1].exc_info[1])


def test_app_ready_installs_on_child_loggers():
    from django.apps import apps

    child = logging.getLogger("djust.state_backends")
    for f in [f for f in child.filters if isinstance(f, DjustLogSanitizerFilter)]:
        child.removeFilter(f)
    apps.get_app_config("djust").ready()
    assert _sanitizers(child) == 1
