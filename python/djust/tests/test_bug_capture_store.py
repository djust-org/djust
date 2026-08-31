"""Tests for djust.bug_capture_store (B7 iter C, #1561).

Three groups:

1. **Opaque ids and the in-memory store** — shape, TTL, bounded size.
2. **Config resolution + the encode/decode wire path** — including the
   default (no store configured) staying byte-identical to iter A.
3. **The Redis auth refusal** — exercised against *real* ``redis-server``
   processes (one open, one ``requirepass``-protected) started by the
   fixtures below, because the whole point of the check is that it asks
   the server rather than reading the URL. Skipped when ``redis-server``
   or ``redis-py`` is unavailable.

The security-relevant assertions are the ones that probe the *bypass*
rather than the happy path: a URL that looks authenticated against a
server that isn't (§3), and an indirect blob whose id tries to address a
key outside the store's namespace (§2).
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import time

import pytest
from django.test import override_settings

from djust.bug_capture import BugCapture
from djust.bug_capture_store import (
    DEFAULT_INLINE_LIMIT,
    InMemorySnapshotStore,
    SnapshotStore,
    UnauthenticatedRedisError,
    get_store,
    inline_limit,
    is_valid_snapshot_id,
    new_snapshot_id,
    reset_store_cache,
)

redis_py = pytest.importorskip("redis", reason="redis-py not installed")


@pytest.fixture(autouse=True)
def _clear_store_cache():
    reset_store_cache()
    yield
    reset_store_cache()


def _capture(**overrides) -> BugCapture:
    defaults = dict(
        state_before={"count": 0},
        state_after={"count": 1},
        vdom_patches=[{"op": "text", "path": [0], "text": "1"}],
        event_name="increment",
    )
    defaults.update(overrides)
    return BugCapture(**defaults)


def _big_capture() -> BugCapture:
    """A capture whose base64 body is comfortably over the inline limit."""
    blob = "x" * (DEFAULT_INLINE_LIMIT * 2)
    return _capture(state_before={"note": blob}, state_after={"note": blob + "!"})


# ---------------------------------------------------------------------------
# 1. Opaque ids + the in-memory store
# ---------------------------------------------------------------------------


class TestSnapshotIds:
    def test_generated_id_is_22_base64url_chars(self):
        for _ in range(20):
            assert is_valid_snapshot_id(new_snapshot_id())

    def test_generated_ids_are_unique(self):
        assert len({new_snapshot_id() for _ in range(200)}) == 200

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "short",
            "a" * 21,
            "a" * 23,
            "djust:session:abcdefghij",  # a key in someone else's namespace
            "aaaaaaaaaaaaaaaaaaaaa*",  # glob
            "aaaaaaaaaaaaaaaaaaaa/x",  # path separator
            "aaaaaaaaaaaaaaaaaaaa.x",  # would re-split the blob
            "aaaaaaaaaaaaaaaaaaaa\n1",  # CRLF-ish injection
            None,
            b"a" * 22,
            12345,
        ],
    )
    def test_invalid_ids_are_rejected(self, bad):
        assert is_valid_snapshot_id(bad) is False


class TestInMemoryStore:
    def test_put_get_round_trip(self):
        store = InMemorySnapshotStore()
        sid = store.put("payload-body")
        assert is_valid_snapshot_id(sid)
        assert store.get(sid) == "payload-body"

    def test_unknown_id_returns_none(self):
        assert InMemorySnapshotStore().get(new_snapshot_id()) is None

    def test_invalid_id_returns_none_without_raising(self):
        assert InMemorySnapshotStore().get("djust:session:1") is None

    def test_expired_entry_is_dropped(self):
        store = InMemorySnapshotStore()
        sid = store.put("gone-soon", ttl=0)
        time.sleep(0.01)
        assert store.get(sid) is None

    def test_store_is_bounded_and_evicts_oldest_first(self):
        store = InMemorySnapshotStore(max_entries=3)
        ids = [store.put("payload-%d" % i) for i in range(5)]
        assert store.get(ids[0]) is None
        assert store.get(ids[1]) is None
        assert store.get(ids[4]) == "payload-4"

    def test_is_a_snapshot_store(self):
        assert isinstance(InMemorySnapshotStore(), SnapshotStore)


# ---------------------------------------------------------------------------
# 2. Config resolution + the wire path
# ---------------------------------------------------------------------------


class _SpyStore(SnapshotStore):
    """Records every id it is asked for, so tests can prove it was NOT asked."""

    def __init__(self):
        self.default_ttl = 60
        self.requested = []
        self._data = {}

    def put(self, payload, ttl=None):
        sid = new_snapshot_id()
        self._data[sid] = payload
        return sid

    def get(self, snapshot_id):
        self.requested.append(snapshot_id)
        return self._data.get(snapshot_id)


class TestStoreConfigResolution:
    def test_default_is_no_store(self):
        with override_settings(LIVEVIEW_CONFIG={}):
            assert get_store() is None

    def test_explicit_none_is_no_store(self):
        with override_settings(LIVEVIEW_CONFIG={"bug_capture_store": None}):
            assert get_store() is None

    def test_memory_shorthand(self):
        with override_settings(LIVEVIEW_CONFIG={"bug_capture_store": "memory"}):
            assert isinstance(get_store(), InMemorySnapshotStore)

    def test_memory_dict_form_accepts_ttl(self):
        cfg = {"bug_capture_store": {"backend": "memory", "ttl": 90}}
        with override_settings(LIVEVIEW_CONFIG=cfg):
            store = get_store()
        assert isinstance(store, InMemorySnapshotStore)
        assert store.default_ttl == 90

    def test_instance_is_used_as_is(self):
        spy = _SpyStore()
        with override_settings(LIVEVIEW_CONFIG={"bug_capture_store": spy}):
            assert get_store() is spy

    def test_store_is_cached_between_calls(self):
        with override_settings(LIVEVIEW_CONFIG={"bug_capture_store": "memory"}):
            assert get_store() is get_store()

    def test_cache_invalidates_when_config_changes(self):
        with override_settings(LIVEVIEW_CONFIG={"bug_capture_store": "memory"}):
            first = get_store()
        with override_settings(LIVEVIEW_CONFIG={}):
            assert get_store() is None
        with override_settings(LIVEVIEW_CONFIG={"bug_capture_store": "memory"}):
            assert get_store() is not first

    def test_redis_backend_without_url_is_a_loud_error(self):
        with override_settings(LIVEVIEW_CONFIG={"bug_capture_store": {"backend": "redis"}}):
            with pytest.raises(ValueError, match="requires a 'url' key"):
                get_store()

    def test_bare_redis_string_is_a_loud_error(self):
        with override_settings(LIVEVIEW_CONFIG={"bug_capture_store": "redis"}):
            with pytest.raises(ValueError, match="needs a URL"):
                get_store()

    def test_unknown_type_is_a_loud_error(self):
        with override_settings(LIVEVIEW_CONFIG={"bug_capture_store": 42}):
            with pytest.raises(ValueError, match="must be None"):
                get_store()

    def test_unimportable_dotted_path_is_a_loud_error(self):
        cfg = {"bug_capture_store": "djust.nope.NoSuchStore"}
        with override_settings(LIVEVIEW_CONFIG=cfg):
            with pytest.raises(ValueError, match="could not be"):
                get_store()

    def test_dotted_path_producing_a_non_store_is_a_loud_error(self):
        cfg = {"bug_capture_store": "djust.bug_capture.BugCapture"}
        with override_settings(LIVEVIEW_CONFIG=cfg):
            with pytest.raises((ValueError, TypeError)):
                get_store()

    def test_inline_limit_is_configurable(self):
        with override_settings(LIVEVIEW_CONFIG={"bug_capture_inline_limit": 10}):
            assert inline_limit() == 10

    def test_inline_limit_falls_back_on_garbage(self):
        with override_settings(LIVEVIEW_CONFIG={"bug_capture_inline_limit": "big"}):
            assert inline_limit() == DEFAULT_INLINE_LIMIT


class TestEncodeDecodeWithStore:
    @pytest.fixture(autouse=True)
    def _debug_on(self, settings):
        """`BugCapture.encode` is gated off outside DEBUG (iter A)."""
        settings.DEBUG = True

    def test_no_store_configured_keeps_a_large_blob_inline(self):
        """The default must not change iter A's behaviour, however large."""
        with override_settings(LIVEVIEW_CONFIG={}):
            encoded = _big_capture().encode()
            assert ".store." not in encoded
            assert BugCapture.decode(encoded).state_after == _big_capture().state_after

    def test_small_blob_stays_inline_even_with_a_store(self):
        spy = _SpyStore()
        with override_settings(LIVEVIEW_CONFIG={"bug_capture_store": spy}):
            encoded = _capture().encode()
        assert ".store." not in encoded
        assert not spy._data, "a sub-threshold payload must never reach the store"

    def test_large_blob_becomes_an_indirect_reference(self):
        cfg = {"bug_capture_store": "memory"}
        with override_settings(LIVEVIEW_CONFIG=cfg):
            encoded = _big_capture().encode()
            assert encoded.startswith("djbug1.store.")
            assert is_valid_snapshot_id(encoded[len("djbug1.store.") :])
            # The whole point: the shared string is short.
            assert len(encoded) < 50

    def test_round_trip_through_the_store(self):
        original = _big_capture()
        cfg = {"bug_capture_store": "memory"}
        with override_settings(LIVEVIEW_CONFIG=cfg):
            decoded = BugCapture.decode(original.encode())
        assert decoded.state_before == original.state_before
        assert decoded.state_after == original.state_after
        assert decoded.vdom_patches == original.vdom_patches
        assert decoded.event_name == original.event_name

    def test_lowering_the_inline_limit_pushes_a_small_blob_to_the_store(self):
        cfg = {"bug_capture_store": "memory", "bug_capture_inline_limit": 8}
        with override_settings(LIVEVIEW_CONFIG=cfg):
            assert _capture().encode().startswith("djbug1.store.")

    def test_expired_snapshot_decodes_to_a_clear_error(self):
        cfg = {"bug_capture_store": {"backend": "memory", "ttl": 0}}
        with override_settings(LIVEVIEW_CONFIG=cfg):
            encoded = _big_capture().encode()
            time.sleep(0.01)
            with pytest.raises(ValueError, match="unknown or its TTL has expired"):
                BugCapture.decode(encoded)

    def test_indirect_blob_with_no_store_configured_is_a_clear_error(self):
        with override_settings(LIVEVIEW_CONFIG={}):
            with pytest.raises(ValueError, match="no store is configured"):
                BugCapture.decode("djbug1.store." + new_snapshot_id())

    # -- the bypass probes -------------------------------------------------

    @pytest.mark.parametrize(
        "hostile_id",
        [
            "djust:session:abc",  # another djust subsystem's key
            "*",  # glob
            "djust:bugcapture:" + "a" * 22,  # prefix re-supplied by the caller
            "a" * 500,  # oversized key
            "../../etc/passwd",
            "%2e%2e%2fadmin",  # percent-encoded traversal
            "\x00" + "a" * 21,  # NUL injection
        ],
    )
    def test_hostile_snapshot_id_never_reaches_the_store(self, hostile_id):
        """Validation happens BEFORE the lookup, so the store is never asked.

        Without this ordering, the replay viewer — which hands this path a
        URL segment verbatim — would be an arbitrary-key reader for whatever
        else lives in the same backend.
        """
        spy = _SpyStore()
        with override_settings(LIVEVIEW_CONFIG={"bug_capture_store": spy}):
            with pytest.raises(ValueError, match="malformed snapshot id"):
                BugCapture.decode("djbug1.store." + hostile_id)
        assert spy.requested == [], (
            "a malformed snapshot id must be rejected before the store is "
            "consulted; it reached the store as %r" % (spy.requested,)
        )

    def test_unknown_and_expired_ids_are_indistinguishable(self):
        """A probe must not learn that a guessed id was ever valid."""
        cfg = {"bug_capture_store": {"backend": "memory", "ttl": 0}}
        with override_settings(LIVEVIEW_CONFIG=cfg):
            expired = _big_capture().encode()
            time.sleep(0.01)
            with pytest.raises(ValueError) as expired_exc:
                BugCapture.decode(expired)
            with pytest.raises(ValueError) as unknown_exc:
                BugCapture.decode("djbug1.store." + new_snapshot_id())
        assert str(expired_exc.value) == str(unknown_exc.value)


# ---------------------------------------------------------------------------
# 3. The Redis auth refusal — against real servers
# ---------------------------------------------------------------------------

REDIS_SERVER = shutil.which("redis-server")
requires_redis_server = pytest.mark.skipif(REDIS_SERVER is None, reason="redis-server not on PATH")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _spawn_redis(*extra_args):
    """Start a throwaway redis-server and yield its port."""
    port = _free_port()
    proc = subprocess.Popen(
        [
            REDIS_SERVER,
            "--port",
            str(port),
            "--save",
            "",
            "--appendonly",
            "no",
            "--bind",
            "127.0.0.1",
            *extra_args,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("redis-server exited during startup")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)
    else:  # pragma: no cover - only on a very slow machine
        proc.kill()
        raise RuntimeError("redis-server did not start in time")
    return proc, port


@pytest.fixture(scope="module")
def open_redis():
    """A redis-server with NO authentication — the thing we must refuse."""
    if REDIS_SERVER is None:
        pytest.skip("redis-server not on PATH")
    proc, port = _spawn_redis()
    try:
        yield port
    finally:
        proc.kill()
        proc.wait()


@pytest.fixture(scope="module")
def authed_redis():
    """A redis-server that requires a password — the thing we must accept."""
    if REDIS_SERVER is None:
        pytest.skip("redis-server not on PATH")
    proc, port = _spawn_redis("--requirepass", "s3cret")
    try:
        yield port
    finally:
        proc.kill()
        proc.wait()


@requires_redis_server
class TestRedisAuthRefusal:
    def test_accepts_a_server_that_requires_auth(self, authed_redis):
        from djust.bug_capture_store import RedisSnapshotStore

        store = RedisSnapshotStore("redis://:s3cret@127.0.0.1:%d/0" % authed_redis)
        sid = store.put("payload")
        assert store.get(sid) == "payload"

    def test_refuses_a_server_with_no_auth_at_all(self, open_redis):
        from djust.bug_capture_store import RedisSnapshotStore

        with pytest.raises(UnauthenticatedRedisError, match="carrying NO credentials"):
            RedisSnapshotStore("redis://127.0.0.1:%d/0" % open_redis)

    @pytest.mark.parametrize(
        "url_template",
        [
            # Every one of these LOOKS authenticated to a check that reads
            # the URL string, and every one points at a wide-open server.
            "redis://:s3cret@127.0.0.1:%d/0",  # password in userinfo
            "redis://admin:s3cret@127.0.0.1:%d/0",  # username + password
            "redis://127.0.0.1:%d/0?password=s3cret",  # password in query
            "redis://%%61dmin:s3%%63ret@127.0.0.1:%d/0",  # percent-encoded userinfo
            "redis://:@127.0.0.1:%d/0",  # empty password, but there IS an '@'
            "redis://127.0.0.1:%d/0#user:pass@elsewhere",  # '@' in the fragment
        ],
    )
    def test_credentials_in_the_url_do_not_excuse_an_open_server(self, open_redis, url_template):
        """The refusal asks the SERVER, so no URL shape can talk it out of it.

        Each of these defeats a plausible naive check ("does the URL have an
        '@'?", "does it have a password?", "does it decode to a password?").
        The failure must be the unauthenticated-server refusal specifically —
        not an incidental error from the server rejecting an unnecessary AUTH,
        which would mean the URL, not the server, decided the outcome.
        """
        from djust.bug_capture_store import RedisSnapshotStore

        with pytest.raises(UnauthenticatedRedisError, match="carrying NO credentials"):
            RedisSnapshotStore(url_template % open_redis)

    def test_refusal_message_never_echoes_the_password(self, open_redis):
        from djust.bug_capture_store import RedisSnapshotStore

        url = "redis://admin:hunter2@127.0.0.1:%d/0" % open_redis
        with pytest.raises(UnauthenticatedRedisError) as exc:
            RedisSnapshotStore(url)
        assert "hunter2" not in str(exc.value)
        assert "<redacted>" in str(exc.value)

    def test_escape_hatch_attaches_to_an_open_server(self, open_redis):
        """`require_auth=False` is the documented mTLS/unix-socket escape."""
        from djust.bug_capture_store import RedisSnapshotStore

        store = RedisSnapshotStore("redis://127.0.0.1:%d/0" % open_redis, require_auth=False)
        sid = store.put("payload")
        assert store.get(sid) == "payload"

    def test_unreachable_server_fails_closed(self):
        from djust.bug_capture_store import RedisSnapshotStore

        with pytest.raises(UnauthenticatedRedisError, match="could not reach"):
            RedisSnapshotStore("redis://:pw@127.0.0.1:%d/0" % _free_port(), socket_timeout=0.25)

    def test_keys_are_namespaced(self, authed_redis):
        from djust.bug_capture_store import RedisSnapshotStore

        store = RedisSnapshotStore(
            "redis://:s3cret@127.0.0.1:%d/0" % authed_redis,
            key_prefix="djust:bugcapture:",
        )
        sid = store.put("payload")
        client = redis_py.from_url("redis://:s3cret@127.0.0.1:%d/0" % authed_redis)
        assert client.get("djust:bugcapture:" + sid) == b"payload"

    def test_ttl_is_applied(self, authed_redis):
        from djust.bug_capture_store import RedisSnapshotStore

        store = RedisSnapshotStore("redis://:s3cret@127.0.0.1:%d/0" % authed_redis, ttl=42)
        sid = store.put("payload")
        client = redis_py.from_url("redis://:s3cret@127.0.0.1:%d/0" % authed_redis)
        assert 0 < client.ttl("djust:bugcapture:" + sid) <= 42

    def test_hostile_id_never_reaches_redis(self, authed_redis):
        from djust.bug_capture_store import RedisSnapshotStore

        store = RedisSnapshotStore("redis://:s3cret@127.0.0.1:%d/0" % authed_redis)
        client = redis_py.from_url("redis://:s3cret@127.0.0.1:%d/0" % authed_redis)
        client.set("djust:session:secret", "not-yours")
        assert store.get("djust:session:secret") is None
        assert store.get("") is None

    def test_end_to_end_round_trip_via_config(self, authed_redis, settings):
        settings.DEBUG = True
        original = _big_capture()
        cfg = {
            "bug_capture_store": {
                "backend": "redis",
                "url": "redis://:s3cret@127.0.0.1:%d/0" % authed_redis,
                "ttl": 60,
            }
        }
        with override_settings(LIVEVIEW_CONFIG=cfg):
            encoded = original.encode()
            assert encoded.startswith("djbug1.store.")
            decoded = BugCapture.decode(encoded)
        assert decoded.state_before == original.state_before
        assert decoded.state_after == original.state_after


# ---------------------------------------------------------------------------
# 4. The replay viewer (iter B) is the real consumer of an indirect blob
# ---------------------------------------------------------------------------


class TestReplayViewWithStore:
    """End-to-end: the iter-B viewer resolves a ``djbug1.store.<id>`` URL.

    The viewer is where a caller-supplied blob meets the store, so it is the
    path that matters for the id-validation ordering — a unit test of
    ``BugCapture.decode`` alone would not prove the viewer never becomes an
    arbitrary-key reader for whatever else lives in the same backend.
    """

    @pytest.fixture(autouse=True)
    def _debug_on(self, settings):
        settings.DEBUG = True

    def _get(self, blob):
        from django.test import RequestFactory

        from djust.bug_capture_views import replay_view

        return replay_view(RequestFactory().get("/__djust__/replay/" + blob), blob)

    def test_renders_a_store_backed_blob(self):
        original = _big_capture()
        with override_settings(LIVEVIEW_CONFIG={"bug_capture_store": "memory"}):
            blob = original.encode()
            assert blob.startswith("djbug1.store.")
            response = self._get(blob)
        assert response.status_code == 200
        assert b"increment" in response.content

    def test_hostile_store_id_is_a_400_and_never_reaches_the_store(self):
        spy = _SpyStore()
        with override_settings(LIVEVIEW_CONFIG={"bug_capture_store": spy}):
            response = self._get("djbug1.store.djust:session:abc")
        assert response.status_code == 400
        assert spy.requested == []

    def test_unknown_store_id_is_a_400_not_a_crash(self):
        with override_settings(LIVEVIEW_CONFIG={"bug_capture_store": "memory"}):
            response = self._get("djbug1.store." + new_snapshot_id())
        assert response.status_code == 400
