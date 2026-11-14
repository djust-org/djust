"""Stores that one process cannot share with another are reported (#4103, #4104).

Each one works on a single process and silently does less on several:

* ``djust.C031`` — the session store is a process-local cache (#4103);
* ``djust.C032`` — the channel layer is in-memory (#4104);
* ``djust.C033`` — presence is counted in memory and a view uses it (#4104).

All three are Warnings, reported only with ``DEBUG`` off (a development server
is one process).
"""

from __future__ import annotations

from unittest import mock

import pytest
from django.test import override_settings

from djust import LiveView
from djust.checks import configuration
from djust.presence import PresenceMixin

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
REDIS = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache"}}
CACHE = "django.contrib.sessions.backends.cache"
CACHED_DB = "django.contrib.sessions.backends.cached_db"
DB = "django.contrib.sessions.backends.db"


def _ids(check, *, production=True):
    errors: list = []
    with mock.patch.object(configuration, "_is_production", return_value=production):
        check(errors)
    return [e.id for e in errors], errors


# ---------------------------------------------------------------------- C031


@pytest.mark.parametrize("engine", [CACHE, CACHED_DB])
def test_c031_reports_a_session_cache_local_to_one_process(engine):
    with override_settings(SESSION_ENGINE=engine, CACHES=LOCMEM, SESSION_CACHE_ALIAS="default"):
        ids, errors = _ids(configuration._check_process_local_session_cache)
    assert ids == ["djust.C031"]
    assert errors[0].level == 30  # Warning
    assert "LocMemCache" in errors[0].msg
    assert "redis" in errors[0].hint.lower() and "db" in errors[0].hint


def test_c031_names_the_alias_the_sessions_use():
    caches = {**REDIS, "sessions": LOCMEM["default"]}
    with override_settings(SESSION_ENGINE=CACHE, CACHES=caches, SESSION_CACHE_ALIAS="sessions"):
        ids, errors = _ids(configuration._check_process_local_session_cache)
    assert ids == ["djust.C031"] and "'sessions'" in errors[0].msg
    with override_settings(SESSION_ENGINE=CACHE, CACHES=caches, SESSION_CACHE_ALIAS="default"):
        assert _ids(configuration._check_process_local_session_cache)[0] == []


@pytest.mark.parametrize(
    "engine, caches",
    [
        (DB, LOCMEM),
        (CACHE, REDIS),
        (CACHED_DB, REDIS),
        ("django.contrib.sessions.backends.file", LOCMEM),
    ],
)
def test_c031_is_quiet_for_shared_stores(engine, caches):
    with override_settings(SESSION_ENGINE=engine, CACHES=caches, SESSION_CACHE_ALIAS="default"):
        assert _ids(configuration._check_process_local_session_cache)[0] == []


def test_c031_is_quiet_in_development_and_when_suppressed():
    with override_settings(SESSION_ENGINE=CACHE, CACHES=LOCMEM, SESSION_CACHE_ALIAS="default"):
        assert _ids(configuration._check_process_local_session_cache, production=False)[0] == []
        with override_settings(DJUST_CONFIG={"suppress_checks": ["C031"]}):
            assert _ids(configuration._check_process_local_session_cache)[0] == []


# ---------------------------------------------------------------------- C032


@pytest.mark.parametrize(
    "backend",
    [
        "channels.layers.InMemoryChannelLayer",
        "djust.layers.InMemoryChannelLayer",
        "djust.layers.MultiLoopInMemoryChannelLayer",
    ],
)
def test_c032_reports_an_in_memory_channel_layer(backend):
    with override_settings(CHANNEL_LAYERS={"default": {"BACKEND": backend}}):
        ids, errors = _ids(configuration._check_in_memory_channel_layer)
    assert ids == ["djust.C032"]
    assert errors[0].level == 30
    assert "RedisChannelLayer" in errors[0].hint


def test_c032_is_quiet_for_a_shared_layer_in_development_and_when_suppressed():
    redis = {"default": {"BACKEND": "channels_redis.core.RedisChannelLayer"}}
    with override_settings(CHANNEL_LAYERS=redis):
        assert _ids(configuration._check_in_memory_channel_layer)[0] == []
    memory = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
    with override_settings(CHANNEL_LAYERS=memory):
        assert _ids(configuration._check_in_memory_channel_layer, production=False)[0] == []
        with override_settings(DJUST_CONFIG={"suppress_checks": ["C032"]}):
            assert _ids(configuration._check_in_memory_channel_layer)[0] == []


# ---------------------------------------------------------------------- C033


class _RoomView(PresenceMixin, LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root></div>"


@pytest.mark.parametrize("config", [{}, {"PRESENCE_BACKEND": "memory"}])
def test_c033_reports_memory_presence_used_by_a_view(config):
    with override_settings(DJUST_CONFIG=config):
        ids, errors = _ids(configuration._check_memory_presence_backend)
    assert ids == ["djust.C033"]
    assert errors[0].level == 30
    assert "_RoomView" in errors[0].msg


def test_c033_is_quiet_for_redis_presence_in_development_and_without_presence_views():
    with override_settings(DJUST_CONFIG={"PRESENCE_BACKEND": "redis"}):
        assert _ids(configuration._check_memory_presence_backend)[0] == []
    with override_settings(DJUST_CONFIG={}):
        assert _ids(configuration._check_memory_presence_backend, production=False)[0] == []
        with mock.patch.object(configuration, "_walk_subclasses", return_value=[]):
            assert _ids(configuration._check_memory_presence_backend)[0] == []
    with override_settings(DJUST_CONFIG={"suppress_checks": ["C033"]}):
        assert _ids(configuration._check_memory_presence_backend)[0] == []


def test_the_checks_run_from_the_registered_configuration_check():
    """Wired into the registered entry point, not only callable on their own."""
    import inspect

    source = inspect.getsource(configuration)
    for name in (
        "_check_process_local_session_cache(errors)",
        "_check_in_memory_channel_layer(errors)",
        "_check_memory_presence_backend(errors)",
    ):
        assert source.count(name) == 1, name


def test_c031_says_a_dummy_cache_loses_sessions_even_with_one_process():
    dummy = {"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}}
    with override_settings(SESSION_ENGINE=CACHE, CACHES=dummy, SESSION_CACHE_ALIAS="default"):
        ids, errors = _ids(configuration._check_process_local_session_cache, production=False)
    assert ids == ["djust.C031"]
    assert "stores nothing" in errors[0].msg and "even with one process" in errors[0].msg
    assert "more than one process" not in errors[0].msg
