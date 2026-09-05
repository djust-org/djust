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

import time
import uuid

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


@pytest.mark.django_db
def test_hot_reload_change_dispatcher_drops_the_library_cache(late_app, tmp_path):
    """The REAL watcher: ``enable_hot_reload`` → watchdog observer → debounced
    ``on_file_change`` → ``invalidate_installed_cache``. Not a unit call on
    the hook — the file lands on disk and the observer has to notice it."""
    pytest.importorskip("watchdog")
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

            # Warm the cache, then add a module under templatetags/.
            template_libraries._library_map()
            assert template_libraries._installed_cache is not None
            time.sleep(0.3)  # let the observer settle on macOS FSEvents
            new_module = pkg / "templatetags" / ("late_new_%s.py" % uuid.uuid4().hex[:6])
            new_module.write_text(_TAG_SOURCE)

            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and template_libraries._installed_cache is not None:
                time.sleep(0.1)
            assert template_libraries._installed_cache is None, (
                "the hot-reload dispatcher must drop _installed_cache when a .py "
                "file appears under a watched directory (#2602)"
            )
    finally:
        hot_reload_server.stop()
        config.set("hot_reload_watch_dirs", prev_dirs)
