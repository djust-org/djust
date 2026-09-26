"""``djust.C023``: channels_redis core layer with redis-py 8's 5 s socket timeout (#3199).

redis-py 8 lowered its default ``socket_timeout`` from ``None`` to 5 s, the
same value channels_redis' core layer passes to ``BZPOPMIN``. The socket read
then times out as the blocking command returns, the ``TimeoutError`` escapes
``await_many_dispatch`` and the idle LiveView's WebSocket dies
(django/channels_redis#422). The check warns when every condition holds:
the core ``RedisChannelLayer``, redis-py >= 8 installed, and a host whose
effective ``socket_timeout`` is 5 s or less.
"""

from __future__ import annotations

import pytest
from django.test import override_settings

from djust.checks import configuration
from djust.checks.configuration import _check_redis_channel_layer_socket_timeout

CORE = "channels_redis.core.RedisChannelLayer"


def _run(hosts=None, backend=CORE, config=None):
    layer = {"BACKEND": backend}
    if config is not None:
        layer["CONFIG"] = config
    elif hosts is not None:
        layer["CONFIG"] = {"hosts": hosts}
    errors: list = []
    with override_settings(CHANNEL_LAYERS={"default": layer}):
        _check_redis_channel_layer_socket_timeout(errors)
    return [e.id for e in errors]


@pytest.fixture
def redis_version(monkeypatch):
    def set_version(version):
        monkeypatch.setattr(configuration, "_installed_redis_version", lambda: version)

    set_version("8.1.0")
    return set_version


@pytest.mark.parametrize(
    "hosts",
    [
        ["redis://localhost:6379/0"],
        [("127.0.0.1", 6379)],
        [{"address": "redis://localhost:6379"}],
        [{"address": "redis://localhost:6379", "socket_timeout": 5}],
        [{"address": "redis://localhost:6379", "socket_timeout": 10}, "redis://other:6379"],
    ],
)
def test_warns_for_a_host_left_at_the_5s_default(redis_version, hosts):
    assert _run(hosts) == ["djust.C023"]


def test_warns_when_hosts_is_omitted(redis_version):
    # channels_redis then defaults to localhost:6379 with no socket_timeout.
    assert _run(config={}) == ["djust.C023"]
    assert _run() == ["djust.C023"]


@pytest.mark.parametrize(
    "hosts",
    [
        [{"address": "redis://localhost:6379", "socket_timeout": 10}],
        [{"address": "redis://localhost:6379", "socket_timeout": None}],
        [{"host": "localhost", "port": 6379, "socket_timeout": 5.5}],
        ["redis://localhost:6379/0?socket_timeout=10"],
    ],
)
def test_silent_when_every_host_sets_a_longer_timeout(redis_version, hosts):
    assert _run(hosts) == []


def test_silent_on_redis_py_7(redis_version):
    redis_version("7.4.0")
    assert _run(["redis://localhost:6379"]) == []


def test_silent_without_redis_py(redis_version):
    redis_version(None)
    assert _run(["redis://localhost:6379"]) == []


def test_silent_for_other_layers(redis_version):
    assert (
        _run(["redis://localhost:6379"], backend="channels_redis.pubsub.RedisPubSubChannelLayer")
        == []
    )
    assert _run(backend="channels.layers.InMemoryChannelLayer") == []


def test_suppressible(redis_version):
    with override_settings(DJUST_CONFIG={"suppress_checks": ["C023"]}):
        assert _run(["redis://localhost:6379"]) == []


def test_registered_in_check_configuration(redis_version):
    from djust.checks import check_configuration

    with override_settings(
        CHANNEL_LAYERS={"default": {"BACKEND": CORE, "CONFIG": {"hosts": ["redis://x:6379"]}}}
    ):
        ids = [e.id for e in check_configuration(None)]
    assert "djust.C023" in ids
