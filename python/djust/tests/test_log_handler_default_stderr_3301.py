"""#3301: djust WARNING+ must reach stderr in a project with no LOGGING config.

``install_handler()`` puts the ring-buffer handler on the ``djust`` logger. A
logger that has a handler no longer falls back to ``logging.lastResort``, so
in a stock project every djust warning went to the buffer only. The handler
now hands such records to ``lastResort`` itself, but only when no other
handler would have seen them, so a configured project never prints twice.

The end-to-end cases run in a subprocess: the in-process test runner has its
own root handlers (pytest's capture), which would mask the stock-project case.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import textwrap

import pytest

from djust.observability.log_handler import (
    ObservabilityLogHandler,
    _clear_logs,
)

_SCRIPT = textwrap.dedent(
    """
    import logging, sys
    import django
    from django.conf import settings

    kw = dict(
        INSTALLED_APPS=["django.contrib.contenttypes", "django.contrib.auth", "djust"],
        SECRET_KEY="x",
        DEBUG=False,
    )
    mode = sys.argv[1]
    if mode == "djust-handler":
        kw["LOGGING"] = {
            "version": 1,
            "disable_existing_loggers": False,
            "handlers": {"c": {"class": "logging.StreamHandler"}},
            "loggers": {"djust": {"handlers": ["c"], "level": "INFO"}},
        }
    elif mode == "root-handler":
        kw["LOGGING"] = {
            "version": 1,
            "disable_existing_loggers": False,
            "handlers": {"c": {"class": "logging.StreamHandler"}},
            "root": {"handlers": ["c"], "level": "WARNING"},
        }
    elif mode == "no-propagate":
        kw["LOGGING"] = {
            "version": 1,
            "disable_existing_loggers": False,
            "handlers": {"c": {"class": "logging.StreamHandler"}},
            "root": {"handlers": ["c"], "level": "WARNING"},
            "loggers": {"djust": {"propagate": False}},
        }
    settings.configure(**kw)
    django.setup()
    log = logging.getLogger("djust.runtime_probe")
    log.warning("probe-warning")
    log.error("probe-error")
    log.info("probe-info")
    """
)


def _run(mode: str) -> str:
    proc = subprocess.run(
        [sys.executable, "-c", _SCRIPT, mode],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""
    return proc.stderr


def test_stock_project_prints_djust_warning_once():
    err = _run("stock")
    assert err.count("probe-warning") == 1
    assert err.count("probe-error") == 1


def test_stock_project_does_not_print_info():
    assert "probe-info" not in _run("stock")


def test_djust_logger_handler_does_not_duplicate():
    err = _run("djust-handler")
    assert err.count("probe-warning") == 1
    assert err.count("probe-error") == 1


def test_root_handler_does_not_duplicate():
    err = _run("root-handler")
    assert err.count("probe-warning") == 1
    assert err.count("probe-info") == 0


def test_propagate_false_without_handler_still_prints_once():
    # The user cut djust off from the root handler and gave it none of its
    # own: that is the unconfigured case again, so lastResort applies once.
    err = _run("no-propagate")
    assert err.count("probe-warning") == 1


# --- unit level: the decision runs through the real logging machinery ----


@pytest.fixture
def probe_logger(monkeypatch):
    """A ``djust.*`` logger wired like production: only our handler on ``djust``.

    ``djust`` is re-parented onto a private root so pytest's own root-logger
    capture handlers (installed per test phase) cannot count as "configured".
    """
    _clear_logs()
    parent = logging.getLogger("djust")
    # Production has exactly one handler on ``djust`` (install_handler() is
    # idempotent). Other tests may have rebuilt logging config, so make sure
    # one is there without ever adding a second.
    added = None
    if not any(isinstance(h, ObservabilityLogHandler) for h in parent.handlers):
        added = ObservabilityLogHandler(level=logging.DEBUG)
        parent.addHandler(added)
    child = logging.getLogger("djust.unit_probe_3301")
    seen: list[str] = []

    class _Recorder(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())

    monkeypatch.setattr(logging, "lastResort", _Recorder(level=logging.WARNING))
    fake_root = logging.RootLogger(logging.WARNING)
    saved_parent = parent.parent
    parent.parent = fake_root
    try:
        yield child, seen, fake_root
    finally:
        parent.parent = saved_parent
        if added is not None:
            parent.removeHandler(added)
        _clear_logs()


def test_unit_unconfigured_warning_goes_to_last_resort(probe_logger):
    child, seen, _ = probe_logger
    child.warning("w")
    child.info("i")
    assert seen == ["w"]


def test_unit_root_handler_suppresses_last_resort(probe_logger):
    child, seen, root = probe_logger
    root.addHandler(logging.NullHandler())
    child.warning("w")
    assert seen == []


def test_unit_root_handler_above_record_level_does_not_count(probe_logger):
    child, seen, root = probe_logger
    h = logging.NullHandler()
    h.setLevel(logging.CRITICAL)
    root.addHandler(h)
    child.warning("w")
    assert seen == ["w"]


def test_unit_child_logger_handler_suppresses_last_resort(probe_logger):
    child, seen, _ = probe_logger
    h = logging.NullHandler()
    child.addHandler(h)
    try:
        child.warning("w")
    finally:
        child.removeHandler(h)
    assert seen == []


def test_unit_non_djust_logger_is_left_alone(probe_logger):
    # The handler is also attached to ``django``; Django owns its own output.
    _, seen, _ = probe_logger
    dj = logging.getLogger("django.unit_probe_3301")
    parent = logging.getLogger("django")
    handler = ObservabilityLogHandler(level=logging.DEBUG)
    parent.addHandler(handler)
    try:
        dj.warning("w")
    finally:
        parent.removeHandler(handler)
    assert seen == []
