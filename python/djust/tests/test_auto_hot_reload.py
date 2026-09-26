"""Auto-enable hot reload (HVR) in DEBUG via ``DjustConfig.ready()``.

As of v0.9.0, djust auto-calls ``enable_hot_reload()`` from its own
``DjustConfig.ready()`` whenever ``DEBUG=True``. The function itself
already gates on DEBUG / config / watchdog and is idempotent via
``hot_reload_server.is_running()``, so the auto-call is safe in
production (early-return) and safe alongside an explicit consumer call.

These tests pin the behavior:

1. The auto-enable call fires from ``ready()``.
2. The ``hot_reload_auto_enable`` config knob disables it.
3. A pytest run skips it (so test sessions don't spawn the watcher), including
   the ``django.setup()`` pytest-django makes before any test starts (#3157).
4. Idempotency: calling ``ready()`` twice doesn't double-start.
5. Startup-failure isolation: if ``enable_hot_reload`` raises, ``ready()`` still completes.
"""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import sys
import textwrap
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
        # pytest itself is imported in this process, which ``ready()`` also
        # treats as a pytest run (#3157); hide that signal too.
        with mock.patch("djust.apps._running_under_pytest", return_value=False):
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

    Strips every sanitizer filter from the ``djust`` logger first, then
    asserts ``ready()`` put exactly one back — proving this test's own
    ``ready()`` call installed it. (The install is idempotent since #2947, so
    a before/after count on a logger prior tests already populated would not
    move.)
    """
    from djust.security import DjustLogSanitizerFilter

    djust_logger = logging.getLogger("djust")
    for f in [f for f in djust_logger.filters if isinstance(f, DjustLogSanitizerFilter)]:
        djust_logger.removeFilter(f)

    app = _make_app_config()
    app.ready()

    after = sum(1 for f in djust_logger.filters if isinstance(f, DjustLogSanitizerFilter))
    assert after == 1


# ---------------------------------------------------------------------------
# Filter-bridge startup warm (cold-start fix): ``ready()`` eagerly runs the
# Django→Rust filter bridge so the FIRST mount/render doesn't pay the one-time
# ~20ms cost of lazily importing every Django templatetag library on the
# request path. Mirrors the HVR auto-enable gating (pytest-skip + opt-out).
# ---------------------------------------------------------------------------


def test_ready_warms_filter_bridge_when_pytest_env_cleared():
    """``ready()`` warms the filter bridge when ``PYTEST_CURRENT_TEST`` is unset."""
    app = _make_app_config()
    with (
        _no_pytest_env(),
        mock.patch("djust.enable_hot_reload"),
        mock.patch("djust.template_filters._ensure_custom_filters_bridged") as mock_warm,
    ):
        app.ready()
    assert mock_warm.call_count == 1


def test_ready_skips_filter_bridge_warm_under_pytest_env():
    """The pytest env var skips the startup warm (same isolation guard as HVR)."""
    assert "PYTEST_CURRENT_TEST" in os.environ
    app = _make_app_config()
    with mock.patch("djust.template_filters._ensure_custom_filters_bridged") as mock_warm:
        app.ready()
    assert mock_warm.call_count == 0


def test_warm_filter_bridge_sets_the_bridge_guard():
    """``_warm_filter_bridge()`` runs the bootstrap, flipping the one-shot
    ``_CUSTOM_FILTERS_BRIDGED`` guard.

    Resets the guard to False FIRST so the assertion is non-tautological
    (#1200): a no-op warm would leave it False and fail.
    """
    from djust import template_filters

    saved = template_filters._CUSTOM_FILTERS_BRIDGED
    template_filters._CUSTOM_FILTERS_BRIDGED = False
    try:
        app = _make_app_config()
        ran = app._warm_filter_bridge()
        assert ran is True
        assert template_filters._CUSTOM_FILTERS_BRIDGED is True
    finally:
        template_filters._CUSTOM_FILTERS_BRIDGED = saved


def test_warm_filter_bridge_opt_out(fresh_config):
    """``LIVEVIEW_CONFIG['filter_bridge_warm'] = False`` skips the warm (gate-off):
    the bridge guard is NOT flipped and the method reports it didn't run."""
    from djust import template_filters

    fresh_config.set("filter_bridge_warm", False)
    saved = template_filters._CUSTOM_FILTERS_BRIDGED
    template_filters._CUSTOM_FILTERS_BRIDGED = False
    try:
        app = _make_app_config()
        ran = app._warm_filter_bridge()
        assert ran is False
        assert template_filters._CUSTOM_FILTERS_BRIDGED is False
    finally:
        template_filters._CUSTOM_FILTERS_BRIDGED = saved


# ---------------------------------------------------------------------------
# #3157: pytest-django calls ``django.setup()`` from its configure hooks, before
# any test runs, so ``PYTEST_CURRENT_TEST`` is not set yet at ``ready()``.
# ---------------------------------------------------------------------------

_SETUP_LIKE_PYTEST_DJANGO = textwrap.dedent(
    """
    import os, sys
    os.environ.pop("PYTEST_CURRENT_TEST", None)
    if sys.argv[1] == "pytest":
        import pytest  # noqa: F401 - pytest-django's process has pytest imported

    import djust
    calls = []
    djust.enable_hot_reload = lambda: calls.append(1)

    from django.conf import settings
    settings.configure(
        DEBUG=True,
        SECRET_KEY="x",
        INSTALLED_APPS=["django.contrib.contenttypes", "django.contrib.auth", "djust"],
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        TEMPLATES=[{"BACKEND": "django.template.backends.django.DjangoTemplates"}],
        LIVEVIEW_CONFIG={"filter_bridge_warm": False},
    )
    import django
    django.setup()
    print("@@CALLS@@%d" % len(calls))
    """
)


def _setup_calls(mode: str) -> int:
    import djust

    pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(djust.__file__)))
    env = dict(os.environ)
    env.pop("PYTEST_CURRENT_TEST", None)
    env.pop("DJANGO_SETTINGS_MODULE", None)
    env["DJUST_NO_UPDATE_CHECK"] = "1"
    env["PYTHONPATH"] = pkg_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    proc = subprocess.run(
        [sys.executable, "-c", _SETUP_LIKE_PYTEST_DJANGO, mode],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("@@CALLS@@")]
    assert len(lines) == 1, proc.stdout + proc.stderr
    return int(lines[0][len("@@CALLS@@") :])


def test_django_setup_inside_a_pytest_process_does_not_start_the_watcher():
    """``django.setup()`` with pytest imported and no ``PYTEST_CURRENT_TEST``
    (what pytest-django does at configure time) must not auto-enable."""
    assert _setup_calls("pytest") == 0


def test_django_setup_outside_pytest_still_starts_the_watcher():
    """The same setup in a plain process (``runserver``) still auto-enables:
    the guard keys on pytest, not on the subprocess or the settings."""
    assert _setup_calls("plain") == 1
