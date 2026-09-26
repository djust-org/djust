"""#3099: no hot-reload file watcher may run under pytest.

``DjustConfig.ready()`` skips the auto-enable when ``PYTEST_CURRENT_TEST`` is
set, but pytest-django runs ``django.setup()`` (and so ``ready()``) during
``pytest_configure``, before any test has set that variable. With the demo
settings' ``DEBUG=True`` every xdist worker therefore started a watchdog
observer on ``examples/demo_project``. A fixture that writes a template into
that tree (``tests/integration/test_dj_root_in_script_comment_2663.py`` writes
``demo_app/templates/demo2663/``) made the watcher broadcast ``hotreload`` to
the ``djust_hotreload`` group, and a WebSocket test consumer connected in that
worker received a stray hot-reload frame (``reload``, or a ``patch`` carrying
``hotreload: true``) in place of its own ``error`` / ``patch`` frame.

The root ``conftest.py`` stops the watcher at session start in every worker.
Tests that exercise the watcher start their own on a temporary directory
(``python/tests/test_late_templatetags_2602.py``).
"""


def test_no_hot_reload_watcher_runs_under_pytest():
    from djust.dev_server import hot_reload_server

    assert not hot_reload_server.is_running(), (
        "a hot-reload watcher is running in this pytest worker: any test that "
        "writes into the demo project will push reload frames into unrelated "
        "WebSocket tests (#3099)"
    )
