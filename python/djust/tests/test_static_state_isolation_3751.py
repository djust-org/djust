"""#3751 — ``static()`` answered ``/app/static/...`` in a test that never set a script prefix.

Two CI runs failed on a worker's ``static()`` returning something other than
``/static/...`` (``test_debug_panel`` and ``test_djust_asset_tag_rust_engine``), passed on
re-run, and could not be reproduced by replaying the worker's tests serially. What can be
shown is the mechanism, and that the cleanup the suite had was incomplete:

* a relative ``STATIC_URL`` (the demo project's ``static/``) is frozen with the script prefix
  in force at its first read, in the settings cache, in the ``staticfiles`` storage instance
  held by ``storages``, and in the lazy proxy over that instance;
* Django empties those caches around an ``override_settings(STATIC_URL=...)``, and its exit
  re-reads the setting (``getattr(settings, key)`` in ``django/test/utils.py``), which is a
  first read: an override that exits while a script prefix is set refills the cache with the
  prefixed URL, and the prefix being restored afterwards does not undo it;
* clearing the settings cache and the lazy proxy, as ``test_ws_path_script_name_3186`` did,
  is not a reset: the proxy refills from the instance ``storages`` still holds.

``djust.test_isolation.reset_static_state`` clears all four and the autouse reset calls it
before every test.
"""

from __future__ import annotations

import os
import sys
import textwrap
import warnings

import pytest
from django.conf import settings
from django.core.files.storage import storages
from django.templatetags.static import static
from django.test import override_settings
from django.urls import get_script_prefix, set_script_prefix
from django.utils.functional import empty

from djust import test_isolation
from djust.test_isolation import StaticStateLeakWarning, reset_static_state

PANEL = "djust/debug-panel.js"


@pytest.fixture(autouse=True)
def _leave_static_state_clean():
    """These tests dirty the state on purpose; whatever happens, hand back a clean one."""
    yield
    reset_static_state()


def _freeze_under_prefix(prefix: str = "/app/") -> str:
    """The polluter: an ``override_settings(STATIC_URL=...)`` that exits under ``prefix``.

    The exit re-reads ``STATIC_URL`` (a first read, the caches having just been emptied), so
    the prefixed URL is frozen into the settings cache and then into the storage built from
    it. Returns what ``static()`` answers while the prefix is still set.
    """
    with override_settings(STATIC_URL="static/"):
        set_script_prefix(prefix)
    return static(PANEL)


def test_the_polluter_really_freezes_the_prefixed_url():
    """The premise of everything below: without this, the other tests prove nothing."""
    assert _freeze_under_prefix() == "/app/static/" + PANEL
    set_script_prefix("/")
    # The leak: the prefix is back to "/", the URL is not.
    assert static(PANEL) == "/app/static/" + PANEL


def test_the_old_partial_cleanup_left_the_prefixed_url_in_place():
    """Clearing the settings cache and the lazy proxy does not reset ``static()``.

    This is what ``test_ws_path_script_name_3186`` did; it is kept as a test so the
    premise of ``reset_static_state`` is checked against the installed Django rather than
    remembered. If a Django release makes the proxy rebuild from settings, this fails and
    the reset can shrink.
    """
    from django.contrib.staticfiles.storage import staticfiles_storage

    _freeze_under_prefix()
    set_script_prefix("/")
    for name in ("STATIC_URL", "MEDIA_URL"):
        settings.__dict__.pop(name, None)
    staticfiles_storage._wrapped = empty

    assert static(PANEL) == "/app/static/" + PANEL


def test_reset_static_state_clears_all_four_caches():
    _freeze_under_prefix()

    findings = reset_static_state()

    assert static(PANEL) == "/static/" + PANEL
    assert get_script_prefix() == "/"
    assert settings.STATIC_URL == "/static/"
    assert storages["staticfiles"]._base_url == "/static/"
    # Each cache that was dirty is named, so a leak can be told from its cause.
    assert any("script prefix" in f for f in findings), findings
    assert any("settings.STATIC_URL" in f for f in findings), findings
    assert any("staticfiles storage" in f for f in findings), findings


def test_the_autouse_reset_cures_a_polluter_in_the_previous_test():
    """The reset every test starts with is what makes a leaked prefix harmless."""
    _freeze_under_prefix()
    set_script_prefix("/")  # the polluter's own fixture restored only the prefix

    with pytest.warns(StaticStateLeakWarning):  # the reset reports what it had to clean
        test_isolation.reset_djust_globals()

    assert static(PANEL) == "/static/" + PANEL


def test_a_clean_state_is_reported_clean_and_left_alone():
    reset_static_state()
    held = storages["staticfiles"]

    assert reset_static_state() == []
    # Not rebuilt for nothing: only a dirty storage is a reason to lose the instance's state.
    assert static(PANEL) == "/static/" + PANEL
    assert storages["staticfiles"]._base_url == held._base_url


def test_other_storage_aliases_are_not_touched():
    """A module-scoped fixture may have put files in an in-memory ``default`` storage."""
    _freeze_under_prefix()  # empties ``storages._storages`` itself, so plant the sentinel after
    sentinel = object()
    storages._storages["default"] = sentinel
    try:
        reset_static_state()
        assert storages._storages.get("default") is sentinel
    finally:
        storages._storages.pop("default", None)


def test_a_dirty_state_warns_and_names_the_previous_test(monkeypatch):
    monkeypatch.setitem(
        test_isolation._STATIC_STATE_OWNER, "test", "pkg/test_polluter.py::test_leaves_a_prefix"
    )
    _freeze_under_prefix()

    with pytest.warns(StaticStateLeakWarning) as caught:
        test_isolation._reset_django_static_state()

    message = str(caught[0].message)
    assert "pkg/test_polluter.py::test_leaves_a_prefix" in message
    assert "script prefix" in message


def test_a_clean_state_does_not_warn():
    reset_static_state()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        test_isolation._reset_django_static_state()
    assert not [w for w in caught if issubclass(w.category, StaticStateLeakWarning)]


# --- The polluter and the victim in one process, with and without the reset. ----------------

_POLLUTER_AND_VICTIM = textwrap.dedent(
    """
    from django.templatetags.static import static
    from django.test import override_settings
    from django.urls import set_script_prefix


    def test_a_polluter():
        # The shape of the leak: an override of STATIC_URL that exits under a script
        # prefix, then a fixture that restores only the prefix.
        with override_settings(STATIC_URL="static/"):
            set_script_prefix("/app/")
        assert static("djust/debug-panel.js") == "/app/static/djust/debug-panel.js"
        set_script_prefix("/")


    def test_the_victim():
        assert static("djust/debug-panel.js") == "/static/djust/debug-panel.js"
    """
)

_RESET_CONFTEST = textwrap.dedent(
    """
    import pytest


    @pytest.fixture(autouse=True)
    def _reset_djust_globals():
        from djust.test_isolation import reset_djust_globals

        reset_djust_globals()
        yield
    """
)


def _run(pytester, monkeypatch, *, with_reset: bool):
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join(
            [p for p in sys.path if p] + [os.environ.get("PYTHONPATH", "")],
        ),
    )
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "demo_project.settings")
    if with_reset:
        pytester.makeconftest(_RESET_CONFTEST)
    pytester.makepyfile(test_pollution_3751=_POLLUTER_AND_VICTIM)
    return pytester.runpytest_subprocess(
        "-p", "no:cacheprovider", "-p", "no:xdist", "-p", "no:randomly", "-q"
    )


def test_victim_fails_after_the_polluter_without_the_reset(pytester, monkeypatch):
    """Gate-off: the same two tests in one process, with no autouse reset."""
    result = _run(pytester, monkeypatch, with_reset=False)

    result.assert_outcomes(passed=1, failed=1)
    result.stdout.fnmatch_lines(["*test_the_victim*", "*/app/static/djust/debug-panel.js*"])


def test_victim_passes_after_the_polluter_with_the_reset(pytester, monkeypatch):
    result = _run(pytester, monkeypatch, with_reset=True)

    result.assert_outcomes(passed=2)
    # The reset ran before the victim and said which test had left the state dirty.
    result.stdout.fnmatch_lines(["*StaticStateLeakWarning*test_a_polluter*"])
