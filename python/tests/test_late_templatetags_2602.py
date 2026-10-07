"""#2602 — a ``templatetags/`` module added while the process runs must be seen.

``template_libraries._installed_cache`` (the ``get_installed_libraries()``
scan) was warmed on the first ``{% load %}`` and dropped only by
``reassert()``. Django's runserver restarts on a new module; djust's HVR does
not, so ``{% load late_tags %}`` stayed "not a registered tag library" for the
life of the dev server. Two fixes, each pinned through the real path:

* the LOADER re-scans once on a miss before refusing (``_find_library``) —
  exercised through ``DjustTemplateBackend.from_string`` → the Rust parser →
  ``load_libraries`` → ``_find_library``;
* the HOT-RELOAD dispatcher (``djust.enable_hot_reload``'s ``on_file_change``)
  drops the cache on any ``.py`` change — exercised with the real watchdog
  observer watching a temp dir that receives a new ``templatetags/*.py`` file.

Each test uses a uniquely named app + library so nothing leaks through the
process-global tag registries between tests or workers.
"""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

import pytest
from django.conf import settings
from django.template import TemplateSyntaxError
from django.test import override_settings

from djust import template_libraries
from djust.template.backend import DjustTemplateBackend

_TAG_SOURCE = """from django import template

register = template.Library()


@register.simple_tag
def late_hello(name):
    return "hello %s" % name
"""


@pytest.fixture
def late_app(tmp_path, monkeypatch):
    """A fresh importable Django app with an EMPTY ``templatetags`` package."""
    name = "lateapp_%s" % uuid.uuid4().hex[:8]
    pkg = tmp_path / name
    (pkg / "templatetags").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "templatetags" / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    with override_settings(INSTALLED_APPS=[*settings.INSTALLED_APPS, name]):
        yield name, pkg


def _backend():
    return DjustTemplateBackend({"NAME": "djust2602", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}})


def test_load_sees_a_templatetags_module_added_after_the_cache_was_warmed(late_app):
    _name, pkg = late_app
    lib = "late_%s" % uuid.uuid4().hex[:8]
    source = "{%% load %s %%}{%% late_hello 'world' %%}" % lib
    backend = _backend()

    # Warm the installed-library scan with the module ABSENT: Django's exact refusal.
    with pytest.raises(TemplateSyntaxError, match="not a registered tag library"):
        backend.from_string(source)
    assert template_libraries._installed_cache is not None, "the scan must be cached now"
    assert lib not in template_libraries._installed_cache

    # The developer adds the module while the process runs.
    (pkg / "templatetags" / ("%s.py" % lib)).write_text(_TAG_SOURCE)

    rendered = str(backend.from_string(source).render({}))
    assert rendered == "hello world", rendered


class _WatcherProbe:
    """What the real watcher did, seen from the test.

    ``events`` are the file events the observer delivered to the handler;
    ``drops`` are the dispatcher's calls to ``invalidate_installed_cache``, each
    with whether the cache was gone right after that call. The test waits on
    these, not on a fixed sleep and a poll of ``_installed_cache`` (#3359).
    """

    def __init__(self):
        self.events = []  # (monotonic time, file name)
        self.drops = []  # (monotonic time, cache dropped by the call)
        self._cond = threading.Condition()

    def record_event(self, path):
        with self._cond:
            self.events.append((time.monotonic(), Path(path).name))
            self._cond.notify_all()

    def record_drop(self, dropped):
        with self._cond:
            self.drops.append((time.monotonic(), dropped))
            self._cond.notify_all()

    def wait_for(self, predicate, timeout):
        deadline = time.monotonic() + timeout
        with self._cond:
            while not predicate():
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self._cond.wait(left)
        return True

    def event_time(self, name):
        for t, n in self.events:
            if n == name:
                return t
        return None

    def drops_after(self, t):
        return [dropped for when, dropped in self.drops if when > t]


@pytest.fixture
def watcher_probe(monkeypatch):
    """Hooks that record the watcher's events and the dispatcher's cache drops.

    Both are call-through: the real handler and the real
    ``invalidate_installed_cache`` still run.
    """
    pytest.importorskip("watchdog")
    from djust.dev_server import DjustFileChangeHandler

    probe = _WatcherProbe()
    real_schedule = DjustFileChangeHandler.schedule_reload
    real_invalidate = template_libraries.invalidate_installed_cache

    def schedule_reload(self, path):
        probe.record_event(path)
        return real_schedule(self, path)

    def invalidate():
        real_invalidate()
        probe.record_drop(template_libraries._installed_cache is None)

    monkeypatch.setattr(DjustFileChangeHandler, "schedule_reload", schedule_reload)
    monkeypatch.setattr(template_libraries, "invalidate_installed_cache", invalidate)
    return probe


def _run_dispatcher_against_a_new_module(late_app, tmp_path, probe):
    """Start the real watcher on ``tmp_path``, add a ``templatetags`` module, and
    return the dispatcher's cache drops that this module's event caused.

    Waits on the watcher's own signals, not on a fixed sleep: the observer's
    start-up latency (FSEvents on macOS, a loaded machine) is not something the
    test can know, and a file written before the observer is live is never
    reported (the original test failed that way when the observer started late).
    """
    from djust import enable_hot_reload
    from djust.config import config
    from djust.dev_server import hot_reload_server

    _name, pkg = late_app
    if hot_reload_server.is_running():
        # The demo settings may have auto-enabled the watcher on the project
        # dir; this test needs it on tmp_path (enable_hot_reload is idempotent).
        hot_reload_server.stop()

    prev_dirs = config.get("hot_reload_watch_dirs")
    config.set("hot_reload_watch_dirs", [str(tmp_path)])
    try:
        with override_settings(DEBUG=True):
            enable_hot_reload()
            assert hot_reload_server.is_running(), "enable_hot_reload must start the watcher"

            # The observer starts asynchronously (FSEvents on macOS, an inotify
            # thread elsewhere): a file written before it is live is never
            # reported. Write a throwaway file until the observer reports one;
            # that is the only proof it is watching, a sleep is a guess.
            for attempt in range(30):
                ready = "ready_%d_%s.py" % (attempt, uuid.uuid4().hex[:6])
                (tmp_path / ready).write_text("")
                if probe.wait_for(lambda: probe.event_time(ready) is not None, 2):
                    break
            else:
                pytest.fail(
                    "the hot-reload watcher never reported a file written under %s" % tmp_path
                )

            # Warm the cache, then add a module under templatetags/.
            template_libraries._library_map()
            assert template_libraries._installed_cache is not None
            new_module = pkg / "templatetags" / ("late_new_%s.py" % uuid.uuid4().hex[:6])
            new_module.write_text(_TAG_SOURCE)

            # The observer delivers the new file's event ...
            assert probe.wait_for(lambda: probe.event_time(new_module.name) is not None, 60), (
                "the watcher never reported the new module (%s)" % new_module.name
            )
            seen = probe.event_time(new_module.name)
            # ... and the debounced dispatcher runs after it (it waits 0.5 s after
            # the LAST event, so a call after this event is this burst's call).
            assert probe.wait_for(lambda: bool(probe.drops_after(seen)), 60), (
                "the hot-reload dispatcher did not call invalidate_installed_cache "
                "after a .py file appeared under a watched directory (#2602)"
            )
            return probe.drops_after(seen)
    finally:
        hot_reload_server.stop()
        config.set("hot_reload_watch_dirs", prev_dirs)


@pytest.mark.django_db
def test_hot_reload_change_dispatcher_drops_the_library_cache(late_app, tmp_path, watcher_probe):
    """The REAL watcher: ``enable_hot_reload`` → watchdog observer → debounced
    ``on_file_change`` → ``invalidate_installed_cache``. Not a unit call on
    the hook — the file lands on disk and the observer has to notice it."""
    drops = _run_dispatcher_against_a_new_module(late_app, tmp_path, watcher_probe)
    assert drops and all(drops), (
        "the hot-reload dispatcher must drop _installed_cache when a .py "
        "file appears under a watched directory (#2602)"
    )
