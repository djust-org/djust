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
    "tests.slow_tier",  # mark tests > 1s in .test_durations `slow` (fast tier: -m "not slow")
    "tests.timing_report",  # #3715: each process reports its worst GC pause and stall
]


#: Seconds a Channels communicator waits for output that WILL arrive. Only a hung
#: turn reaches it; a loaded runner that stalls the loop for a few seconds does not.
COMMUNICATOR_WAIT_S = 30

#: ``receive_nothing`` is the one timeout that is a window for ABSENCE ("nothing
#: arrived within 0.1 s"), so lengthening it would slow every such check 300x.
COMMUNICATOR_ABSENCE_WINDOWS = frozenset({"receive_nothing"})


def _communicator_timeout_defaults():
    """``[(function, index into __defaults__)]`` for every communicator wait.

    Derived from the Channels classes, not listed by hand: the list that was
    written by hand (#3464) named ``disconnect`` / ``receive_output`` /
    ``receive_from`` / ``receive_json_from`` and left out ``connect``, ``wait`` and
    ``get_response`` (seven waits in all). ``connect``'s default is a 1 s wait for the consumer's ``accept``. A loaded runner timed
    that out in ``test_anonymous_ws_mount_is_refused`` (#3692). A method a
    Channels release adds is covered the day it appears.
    """
    import inspect

    from channels.testing import HttpCommunicator, WebsocketCommunicator

    found = {}
    classes = [
        cls
        for communicator in (WebsocketCommunicator, HttpCommunicator)
        for cls in communicator.__mro__
        if cls is not object
    ]
    for cls in classes:
        for name, func in vars(cls).items():
            if name.startswith("_") or name in COMMUNICATOR_ABSENCE_WINDOWS:
                continue
            if not inspect.iscoroutinefunction(func):
                continue
            params = inspect.signature(func).parameters
            timeout = params.get("timeout")
            if timeout is None or isinstance(timeout.default, bool):
                continue
            if not isinstance(timeout.default, (int, float)):
                continue
            defaulted = [
                n
                for n, p in params.items()
                if p.kind is p.POSITIONAL_OR_KEYWORD and p.default is not p.empty
            ]
            found[id(func)] = (func, defaulted.index("timeout"))
    return list(found.values())


@pytest.fixture(scope="session", autouse=True)
def _load_tolerant_ws_communicator_defaults():
    """Give Channels communicators time to finish under loaded test runners."""
    methods = _communicator_timeout_defaults()
    original = [(func, func.__defaults__) for func, _ in methods]
    try:
        for func, index in methods:
            defaults = list(func.__defaults__)
            defaults[index] = COMMUNICATOR_WAIT_S
            func.__defaults__ = tuple(defaults)
        yield
    finally:
        for func, defaults in original:
            func.__defaults__ = defaults


@pytest.fixture(scope="session", autouse=True)
def _accept_communicators_without_origin():
    """Let header-less Channels communicators connect.

    djust refuses a WebSocket handshake with no ``Origin`` unless
    ``DJUST_ALLOW_MISSING_ORIGIN`` is True, and most consumer tests build a
    ``WebsocketCommunicator`` without headers. The Origin tests remove the
    setting to run against the shipped default.
    """
    try:
        from django.conf import settings

        settings.DJUST_ALLOW_MISSING_ORIGIN = True
    except Exception:  # noqa: BLE001 — Django not configured: nothing to set
        yield
        return
    yield
    try:
        del settings.DJUST_ALLOW_MISSING_ORIGIN
    except AttributeError:
        pass


def pytest_collection_finish(session):
    """Take the heap collection built out of every later garbage collection (#3692).

    Collecting ~50k tests imports every test module and all they pull in: about
    1.5 million tracked objects, nearly all of it alive for the whole run. An
    xdist worker lives for the run, and each full (generation 2) collection
    re-walks all of it. A stop-the-world pause stalls the worker's event loop and
    every ``WebsocketCommunicator`` wait with it: measured on a full local run
    (3.12; the CI interpreters were not measured),
    median 0.32 s and up to 3.4 s (22 pauses over 1 s), which is longer than the
    1 s ``connect()`` default and the 3 s receive that failed on CI.

    ``gc.freeze()`` moves the objects that exist at this point into the
    permanent generation, which no later collection visits. Measured on the same
    run, median 0.10 s, max 1.05 s (one pause over 1 s). What
    tests create afterwards is still collected normally; only what was
    already alive when collection ended is exempt: live at that point,
    though a frozen cycle that dies later is never collected, and
    ``gc.get_referrers`` does not list a frozen object as a referrer (the one
    negative assertion built on it, ``test_bridged_context_memo_2914``, builds its
    objects inside the test).
    ``python/djust/tests/test_gc_frozen_after_collection_3692.py`` pins it.
    """
    import gc

    gc.collect()
    gc.freeze()


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
def _isolate_current_tenant():
    """Unbind djust's current tenant around every test (`tests/tenant_isolation.py`).

    A sync test that bound tenant 42 and never reset it made
    `tests/unit/test_server_push.py::TestPushToView::test_push_state` fail
    with an extra `tenant_scope` when both ran on the same xdist worker.
    Does nothing, and imports nothing, unless `djust.tenants.middleware` is
    already loaded.
    """
    from tests.tenant_isolation import isolated_current_tenant

    with isolated_current_tenant():
        yield


@pytest.fixture(autouse=True)
def _no_agent_hint_env(monkeypatch):
    """Disable inherited AI hints in subprocess check tests; I001 tests opt in.

    Agent shells may inherit CLAUDECODE or CODEX_* variables. Discovery tests
    explicitly remove this per-shell override when testing the I001 hint.
    """
    monkeypatch.setenv("DJUST_AI_HINTS", "0")


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
