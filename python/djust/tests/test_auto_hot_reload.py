"""Auto-enable hot reload (HVR) in DEBUG via ``DjustConfig.ready()``.

As of v0.9.0, djust auto-calls ``enable_hot_reload()`` from its own
``DjustConfig.ready()`` whenever ``DEBUG=True``. The function itself
already gates on DEBUG / config / watchdog and is idempotent via
``hot_reload_server.is_running()``, so the auto-call is safe in
production (early-return) and safe alongside an explicit consumer call.

These tests pin the behavior:

1. The auto-enable call fires from ``ready()``.
2. The ``hot_reload_auto_enable`` config knob disables it.
3. ``PYTEST_CURRENT_TEST`` skips it (so test sessions don't spawn the watcher).
4. Idempotency: calling ``ready()`` twice doesn't double-start.
5. Startup-failure isolation: if ``enable_hot_reload`` raises, ``ready()`` still completes.
"""

from __future__ import annotations

import contextlib
import logging
import os
from unittest import mock

import pytest

from djust.apps import DjustConfig


@pytest.fixture
def fresh_config():
    """Reset the djust config singleton so per-test ``set()`` calls don't leak."""
    from djust.config import config

    snapshot = config.as_dict()
    yield config
    config._config = snapshot


@contextlib.contextmanager
def _no_pytest_env():
    """Context manager that hides ``PYTEST_CURRENT_TEST`` for the duration of the block.

    pytest re-sets this env var between fixture teardown and the test
    ``call`` phase, so a fixture-based pop doesn't survive into the test
    body. Use this inside the test body where the env var actually
    needs to be absent (i.e. wrapping the ``ready()`` call).
    """
    saved = os.environ.pop("PYTEST_CURRENT_TEST", None)
    try:
        yield
    finally:
        if saved is not None:
            os.environ["PYTEST_CURRENT_TEST"] = saved


def _make_app_config():
    """Construct a ``DjustConfig`` instance without going through Django's app registry.

    ``AppConfig.__init__`` requires ``app_name`` and ``app_module`` args; we
    don't need a real registered app for unit testing ``ready()`` — we just
    need an instance whose ``ready()`` method we can call. Pass a minimal
    stub module.
    """
    import djust

    return DjustConfig("djust", djust)


def test_ready_auto_calls_enable_hot_reload_when_pytest_env_cleared():
    """``ready()`` invokes ``enable_hot_reload()`` when ``PYTEST_CURRENT_TEST`` is unset.

    Tests the wiring at the apps.py layer: clearing the pytest env var
    means the auto-enable branch runs, which dispatches to
    ``djust.enable_hot_reload``. (The function's own gates — DEBUG,
    watchdog, etc. — are out of scope here; we patch enable_hot_reload
    to count calls.)
    """
    app = _make_app_config()
    with _no_pytest_env(), mock.patch("djust.enable_hot_reload") as mock_enable:
        app.ready()
    assert mock_enable.call_count == 1


def test_ready_skips_when_pytest_env_var_is_set():
    """The pytest env var (always set during a real pytest run) skips auto-enable.

    This is the test-isolation guard — we don't want every pytest invocation
    to spawn a watchdog thread.
    """
    # PYTEST_CURRENT_TEST is set by pytest for the duration of this test.
    assert "PYTEST_CURRENT_TEST" in os.environ
    app = _make_app_config()
    with mock.patch("djust.enable_hot_reload") as mock_enable:
        app.ready()
    assert mock_enable.call_count == 0


def test_ready_skips_when_hot_reload_auto_enable_is_false(fresh_config):
    """``LIVEVIEW_CONFIG['hot_reload_auto_enable'] = False`` opts out of auto-enable."""
    fresh_config.set("hot_reload_auto_enable", False)
    app = _make_app_config()
    with _no_pytest_env(), mock.patch("djust.enable_hot_reload") as mock_enable:
        app.ready()
    assert mock_enable.call_count == 0


def test_ready_is_idempotent_via_is_running_guard(settings):
    """Two ``ready()`` calls reach the inner ``hot_reload_server.start()``
    only once thanks to the ``is_running()`` guard at
    ``python/djust/__init__.py`` (the existing idempotency check that
    protects against double-starts when consumers also call
    ``enable_hot_reload()`` explicitly).

    Uses pytest-django's ``settings`` fixture to set ``DEBUG=True`` so
    ``enable_hot_reload()`` reaches the ``is_running()`` short-circuit
    instead of returning early at the DEBUG gate.
    """
    from djust.dev_server import hot_reload_server

    settings.DEBUG = True
    app = _make_app_config()
    with (
        _no_pytest_env(),
        mock.patch.object(hot_reload_server, "is_running", return_value=True) as mock_is_running,
        mock.patch.object(hot_reload_server, "start") as mock_start,
    ):
        app.ready()
        app.ready()
    # is_running() short-circuits enable_hot_reload() before start() is called.
    assert mock_start.call_count == 0
    # is_running() consulted at least once per ready() call (the idempotency guard).
    assert mock_is_running.call_count >= 2


def test_ready_swallows_enable_hot_reload_exceptions(caplog):
    """A raise inside ``enable_hot_reload()`` must NOT break Django startup.

    The try/except around the auto-enable call mirrors the observability
    setup pattern already in ``ready()`` — dev-mode plumbing must never
    take down app startup. Uses ``logger.exception()`` so the captured
    record's ``exc_info`` carries the traceback for debugability.
    """
    app = _make_app_config()
    with (
        _no_pytest_env(),
        caplog.at_level(logging.ERROR, logger="djust"),
        mock.patch("djust.enable_hot_reload", side_effect=RuntimeError("boom")),
    ):
        # Must not raise.
        app.ready()
    assert "auto-enable" in caplog.text
    # logger.exception() attaches exc_info to the LogRecord; verify that
    # a record with both the auto-enable message AND a traceback was emitted.
    matching = [r for r in caplog.records if "auto-enable" in r.message]
    assert matching, "expected an auto-enable failure log record"
    assert any(r.exc_info for r in matching), "expected exc_info on the log record"


def test_ready_completes_other_setup_even_when_auto_enable_skipped():
    """The auto-enable call is the LAST thing ``ready()`` does; the log-sanitizer
    filter install must still happen regardless of whether auto-enable
    fires.

    Snapshots the filter count BEFORE calling ``ready()`` and asserts the
    count grew by exactly 1 — proving this test's own ``ready()`` call
    actually installed a filter. (Asserting ``any(...)`` would pass
    trivially because prior tests in the file have already populated the
    logger.)
    """
    from djust.security import DjustLogSanitizerFilter

    djust_logger = logging.getLogger("djust")
    before = sum(1 for f in djust_logger.filters if isinstance(f, DjustLogSanitizerFilter))

    app = _make_app_config()
    app.ready()

    after = sum(1 for f in djust_logger.filters if isinstance(f, DjustLogSanitizerFilter))
    assert after == before + 1
