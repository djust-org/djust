"""Private cursor claims are atomic, isolated and fail closed after expiry."""

from concurrent.futures import ThreadPoolExecutor
import shutil
import subprocess
import tempfile
import time
from uuid import uuid4

import pytest

from djust.state_backends import InMemoryStateBackend, RedisStateBackend


@pytest.fixture(scope="module")
def redis_url():
    executable = shutil.which("redis-server")
    if executable is None:
        pytest.skip("redis-server required for actual shared-backend observation proof")
    import redis

    with tempfile.TemporaryDirectory(prefix="djust-obs-") as directory:
        socket = directory + "/redis.sock"
        process = subprocess.Popen(
            [executable, "--port", "0", "--unixsocket", socket, "--save", "", "--appendonly", "no"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        url = "unix://" + socket
        client = redis.from_url(url)
        try:
            deadline = time.monotonic() + 5
            while True:
                if process.poll() is not None:
                    pytest.fail(
                        "temporary Redis exited before readiness: " + process.communicate()[0]
                    )
                try:
                    if client.ping():
                        break
                except redis.ConnectionError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.01)
            yield url
        finally:
            client.close()
            process.terminate()
            process.wait(timeout=5)


@pytest.fixture(params=["memory", "redis"])
def backend(request):
    if request.param == "memory":
        return InMemoryStateBackend()
    return RedisStateBackend(
        request.getfixturevalue("redis_url"), key_prefix="obs-test-" + uuid4().hex + ":"
    )


def test_claim_requires_registration_and_cannot_reset_high_water(backend):
    lifetime = "obs_" + uuid4().hex
    assert not backend._claim_observation(lifetime, 1)
    backend._register_observation(lifetime)
    assert backend._claim_observation(lifetime, 9)
    backend._register_observation(lifetime)
    assert not backend._claim_observation(lifetime, 9)
    assert not backend._claim_observation(lifetime, 3)
    assert backend._claim_observation(lifetime, 10)
    # These are bookkeeping entries, not Rust view sessions.
    assert backend.get_stats()["total_sessions"] == 0


def test_overlapping_duplicate_claims_invoke_only_one_observer(backend):
    lifetime = "obs_" + uuid4().hex
    backend._register_observation(lifetime)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: backend._claim_observation(lifetime, 1), range(40)))
    assert sum(results) == 1
    assert backend._claim_observation(lifetime, 2)


def test_out_of_order_concurrent_claims_cannot_decrease_high_water(backend):
    lifetime = "obs_" + uuid4().hex
    backend._register_observation(lifetime)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda n: backend._claim_observation(lifetime, n), range(100, 0, -1)))
    assert not backend._claim_observation(lifetime, 100)
    assert backend._claim_observation(lifetime, 101)


def test_expired_cursor_is_not_recreated_by_claim(backend, monkeypatch):
    lifetime = "obs_" + uuid4().hex
    backend._register_observation(lifetime)
    if isinstance(backend, InMemoryStateBackend):
        monkeypatch.setattr("djust.state_backends.memory.time.monotonic", lambda: float("inf"))
    else:
        # Expire this exact test-owned key, without sleeping or flushing a DB.
        backend._client.pexpire(backend._make_key("__observation__:" + lifetime), 0)
    assert not backend._claim_observation(lifetime, 1)
    assert not backend._claim_observation(lifetime, 2)


def test_distinct_redis_clients_share_failure_safe_claims(redis_url):
    prefix = "obs-test-" + uuid4().hex + ":"
    first = RedisStateBackend(redis_url, key_prefix=prefix)
    second = RedisStateBackend(redis_url, key_prefix=prefix)
    lifetime = "obs_" + uuid4().hex
    first._register_observation(lifetime)
    assert first._claim_observation(lifetime, 1)
    assert not second._claim_observation(lifetime, 1)
    assert second._claim_observation(lifetime, 2)
    assert not first._claim_observation(lifetime, 1)


def test_redis_claim_retains_ttl_and_propagates_storage_errors(redis_url, monkeypatch):
    backend = RedisStateBackend(redis_url, default_ttl=60, key_prefix=uuid4().hex + ":")
    lifetime = "obs_" + uuid4().hex
    backend._register_observation(lifetime)
    key = backend._make_key("__observation__:" + lifetime)
    backend._client.pexpire(key, 10000)
    assert backend._claim_observation(lifetime, 1)
    assert 0 < backend._client.pttl(key) <= 10000

    def fail(*args):
        raise ConnectionError("storage unavailable")

    monkeypatch.setattr(backend._client, "eval", fail)
    with pytest.raises(ConnectionError, match="storage unavailable"):
        backend._claim_observation(lifetime, 2)
