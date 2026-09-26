"""Root conftest — project-wide pytest plugins for all three test roots.

pytest loads this for any argument under the rootdir (the directory of
`pyproject.toml`), so it is the one place a plugin can be registered once for
`tests/`, `python/tests/` and `python/djust/tests/` alike. The per-root
conftests cannot share it: each is loaded only for its own root, which is why
the `_reset_djust_globals` fixture is a copy in all three (#2234). Mirrors the
other project-wide pytest plumbing — `[tool.pytest.ini_options]` in
`pyproject.toml` (`testpaths`, `pythonpath`, `addopts`) — rather than a
per-root file.

`pythonpath = ["."]` (pyproject.toml) is applied before initial conftests are
imported, so the dotted names below resolve from the repo root. `-p` in
`addopts` would NOT work here: `-p` plugins are imported during preparse,
before `pythonpath` is applied, so a bare `pytest` (not `python -m pytest`)
could not find them.
"""

import pytest

pytest_plugins = [
    "pytester",  # pytest's plugin-testing fixture, for tests/test_lost_items_guard.py
    "tests.lost_items_guard",  # #2746: a run that loses collected items goes red
    "tests.corpus_shards",  # share the full differential sweep within one CI shard
]


def pytest_sessionstart(session):
    """Stop the hot-reload file watcher in every pytest process (#3099).

    ``DjustConfig.ready()`` skips its DEBUG auto-enable when
    ``PYTEST_CURRENT_TEST`` is set, but pytest-django calls ``django.setup()``
    during ``pytest_configure``, before any test sets it, so with the demo
    settings' ``DEBUG=True`` each worker started a watchdog observer on the
    demo project. A test that writes a template there then pushed hot-reload
    frames into whichever WebSocket test was connected in that worker. This
    hook runs after configure in the controller and in every xdist worker.
    Tests that exercise the watcher start (and stop) their own.
    """
    try:
        from djust.dev_server import hot_reload_server
    except Exception:  # noqa: BLE001 — djust not importable: nothing started
        return
    hot_reload_server.stop()


@pytest.fixture(autouse=True)
def _isolate_system_check_registry():
    """Drop system checks registered by an app a test installed and then
    removed, so they cannot fire in a later test (#3177, `tests/check_registry.py`).

    This is what made `test_b008_*` fail with `daphne.E001` when an
    INSTALLED_APPS test ran before it in the same xdist worker (#3169).
    """
    from tests.check_registry import isolated_check_registry

    with isolated_check_registry():
        yield


@pytest.fixture(autouse=True)
def _no_inherited_git_env(monkeypatch):
    """Strip git's execution variables for every test (#2608, #3179).

    Under a git hook ``GIT_DIR`` / ``GIT_INDEX_FILE`` name the REAL repository,
    so any test that runs git, a script that runs git, or library code that
    runs git in-process (``djust.deploy_cli``, ``djust.scaffolding``) would
    otherwise act on it. A fixture's ``git init`` + ``git config user.name
    Test`` rewrote the real ``.git/config`` that way. Modules that spawn git
    also carry their own copy of this fixture, which
    ``tests/test_git_env_guard_3179.py`` enforces statically. This catch-all
    covers the in-process calls that a static scan cannot see.
    """
    from tests.git_env import GIT_EXECUTION_VARS

    for var in GIT_EXECUTION_VARS:
        monkeypatch.delenv(var, raising=False)
