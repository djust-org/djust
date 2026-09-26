"""#3201: an explicit view mounts fresh when its session no longer exists.

A session can vanish under a live page: a Redis flush or restart with cache
sessions, eviction, ``clearsessions``/expiry with DB sessions. The browser still
presents the old cookie. Legacy views mount fresh in that case; an explicit view
used to fail every mount ("Protected view operation failed") because the mount
bound its state to a session key that the first storage read had cleared.

These tests drive the real ``LiveViewConsumer`` over a ``WebsocketCommunicator``
with a real DB session store, like ``test_exposure_runtime``.
"""

import json

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model, login
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings

from djust._exposure_sessions import server_state_adapter
from djust.tests.test_exposure_runtime import RuntimeView, make_request

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

VIEW = "djust.tests.test_exposure_runtime.RuntimeView"


@pytest.fixture
def staged(monkeypatch):
    from djust import LiveView

    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)


async def _connect(session, user=None):
    from channels.testing import WebsocketCommunicator
    from djust.websocket import LiveViewConsumer

    comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    comm.scope["session"] = session
    comm.scope["user"] = user if user is not None else AnonymousUser()
    connected, _ = await comm.connect()
    assert connected
    await comm.receive_json_from(timeout=5)
    return comm


async def _mount(comm):
    await comm.send_json_to({"type": "mount", "view": VIEW, "url": "/runtime-explicit/"})
    return await comm.receive_json_from(timeout=5)


def _settings():
    return override_settings(
        LIVEVIEW_ALLOWED_MODULES=["djust.tests.test_exposure_runtime"],
        DEBUG=False,
        DJUST_CONFIG={},
        DJUST_TENANTS={},
    )


async def _persisted_then_deleted_session_key():
    """A session that held this view's persisted state, then vanished."""
    request = await sync_to_async(make_request)()
    key = request.session.session_key
    with _settings():
        comm = await _connect(SessionStore(key))
        try:
            frame = await _mount(comm)
            assert frame["type"] == "mount", frame
            await comm.send_json_to({"type": "event", "event": "increment", "params": {}})
            reply = await comm.receive_json_from(timeout=5)
            assert reply["type"] in {"patch", "html_update"}, reply
        finally:
            await comm.disconnect()
    await sync_to_async(SessionStore(key).delete)()
    assert not await sync_to_async(SessionStore().exists)(key)
    return key


async def test_mount_with_a_vanished_session_mounts_fresh_and_works(staged):
    key = await _persisted_then_deleted_session_key()
    with _settings():
        comm = await _connect(SessionStore(key))
        try:
            frame = await _mount(comm)
            assert frame["type"] == "mount", frame
            # mount() ran: the value is the mount default, not the restored 6.
            assert ">5<" in frame["html"]
            assert "SENTINEL" not in json.dumps(frame)
            # The socket keeps working under the replacement session: events
            # authorize, persist, and acknowledge.
            await comm.send_json_to({"type": "event", "event": "increment", "params": {}})
            reply = await comm.receive_json_from(timeout=5)
            assert reply["type"] in {"patch", "html_update"}, reply
        finally:
            await comm.disconnect()
    # The vanished key was not resurrected with new data.
    assert not await sync_to_async(SessionStore().exists)(key)


async def test_flushed_cache_session_mounts_fresh(staged):
    """The issue's setup: cache sessions, then the cache is flushed."""
    from django.contrib.sessions.backends.cache import SessionStore as CacheSession
    from django.core.cache import cache

    def cache_key():
        session = CacheSession()
        session.create()
        return session.session_key

    key = await sync_to_async(cache_key)()
    await sync_to_async(cache.clear)()
    with _settings():
        comm = await _connect(CacheSession(key))
        try:
            frame = await _mount(comm)
            assert frame["type"] == "mount", frame
            await comm.send_json_to({"type": "event", "event": "increment", "params": {}})
            reply = await comm.receive_json_from(timeout=5)
            assert reply["type"] in {"patch", "html_update"}, reply
        finally:
            await comm.disconnect()


async def test_vanished_session_state_lands_in_a_new_session_only(staged):
    from django.contrib.sessions.models import Session

    def keys():
        return set(Session.objects.values_list("session_key", flat=True))

    key = await _persisted_then_deleted_session_key()
    before = await sync_to_async(keys)()
    with _settings():
        comm = await _connect(SessionStore(key))
        try:
            frame = await _mount(comm)
            assert frame["type"] == "mount", frame
            await comm.send_json_to({"type": "event", "event": "increment", "params": {}})
            reply = await comm.receive_json_from(timeout=5)
            assert reply["type"] in {"patch", "html_update"}, reply
        finally:
            await comm.disconnect()
    created = await sync_to_async(keys)() - before
    # Exactly one replacement session, server-generated, never the old key.
    assert len(created) == 1 and key not in created, created
    fresh = await sync_to_async(make_request)(created.pop())
    adapter = await sync_to_async(server_state_adapter)(RuntimeView(), fresh)
    assert await adapter.aload() == {"count": 6, "hidden": "SERVER_SENTINEL"}


async def test_vanished_authenticated_session_mounts_anonymous(staged):
    """A vanished session cannot vouch for the user the socket connected as."""

    def authenticated_key():
        request = make_request()
        user = get_user_model().objects.create_user(username="gone", password="pw")
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        request.session.save()
        return request.session.session_key, user

    key, user = await sync_to_async(authenticated_key)()
    await sync_to_async(SessionStore(key).delete)()
    seen = []
    original = RuntimeView.mount

    def spy(self, request, **kwargs):
        seen.append(request.user.is_authenticated)
        return original(self, request, **kwargs)

    RuntimeView.mount = spy
    try:
        with _settings():
            # The scope user was resolved at connect, before the flush.
            comm = await _connect(SessionStore(key), user=user)
            try:
                frame = await _mount(comm)
                assert frame["type"] == "mount", frame
            finally:
                await comm.disconnect()
    finally:
        RuntimeView.mount = original
    assert seen == [False]


async def test_an_existing_session_keeps_its_identity(staged):
    """The fresh-mount path is for a missing session only, never a live one."""
    from django.contrib.sessions.models import Session

    def keys():
        return set(Session.objects.values_list("session_key", flat=True))

    request = await sync_to_async(make_request)()
    key = request.session.session_key
    before = await sync_to_async(keys)()
    with _settings():
        comm = await _connect(SessionStore(key))
        try:
            frame = await _mount(comm)
            assert frame["type"] == "mount", frame
            await comm.send_json_to({"type": "event", "event": "increment", "params": {}})
            reply = await comm.receive_json_from(timeout=5)
            assert reply["type"] in {"patch", "html_update"}, reply
        finally:
            await comm.disconnect()
    # No replacement session was created, and the state went to the live one.
    assert await sync_to_async(keys)() == before
    fresh = await sync_to_async(make_request)(key)
    adapter = await sync_to_async(server_state_adapter)(RuntimeView(), fresh)
    assert await adapter.aload() == {"count": 6, "hidden": "SERVER_SENTINEL"}


async def test_revoked_socket_recovers_on_the_reconnect_mount(staged):
    """The issue's 4403 population: a live socket whose session vanishes.

    Mid-socket, a vanished session is a revocation (logout deletes the
    session), so the event is refused and the socket closes with 4403. That
    ADR-038 guarantee is unchanged. client.js then reconnects with the same
    cookie, and that mount must now succeed fresh instead of failing forever.
    """
    request = await sync_to_async(make_request)()
    key = request.session.session_key
    with _settings():
        comm = await _connect(SessionStore(key))
        try:
            frame = await _mount(comm)
            assert frame["type"] == "mount", frame
            await sync_to_async(SessionStore(key).delete)()
            await comm.send_json_to({"type": "event", "event": "increment", "params": {}})
            reply = await comm.receive_json_from(timeout=5)
            assert reply["type"] == "error", reply
            closed = await comm.receive_output(timeout=5)
            assert closed == {"type": "websocket.close", "code": 4403}
        finally:
            await comm.disconnect()

        comm = await _connect(SessionStore(key))
        try:
            frame = await _mount(comm)
            assert frame["type"] == "mount", frame
            assert ">5<" in frame["html"]
        finally:
            await comm.disconnect()


def _session_rows():
    from django.contrib.sessions.models import Session

    return set(Session.objects.values_list("session_key", flat=True))


async def test_cookieless_sockets_create_no_sessions(staged):
    """Review I2 of #3206: only a present-then-vanished key is replaced.

    A socket with no session key at all behaves exactly as before the fix
    (the explicit binding refuses it) and creates no rows, so cookieless
    sockets cannot mint sessions.
    """
    before = await sync_to_async(_session_rows)()
    with _settings():
        for _ in range(3):
            comm = await _connect(SessionStore())
            try:
                frame = await _mount(comm)
                assert frame["type"] == "error", frame
            finally:
                await comm.disconnect()
    assert await sync_to_async(_session_rows)() == before


async def test_repeated_mounts_on_one_socket_reuse_one_replacement(staged):
    """Review I2 of #3206: mount frames on one socket share one replacement."""
    key = await _persisted_then_deleted_session_key()
    before = await sync_to_async(_session_rows)()
    with _settings():
        comm = await _connect(SessionStore(key))
        try:
            for _ in range(4):
                frame = await _mount(comm)
                assert frame["type"] == "mount", frame
        finally:
            await comm.disconnect()
    created = await sync_to_async(_session_rows)() - before
    assert len(created) == 1, created


async def test_replacement_session_expires_with_the_state_lifetime(staged):
    """No cookie points at the replacement, so it must not live two weeks."""
    from django.contrib.sessions.models import Session
    from django.utils import timezone

    key = await _persisted_then_deleted_session_key()
    before = await sync_to_async(_session_rows)()
    with override_settings(DJUST_SERVER_STATE_MAX_AGE=600), _settings():
        comm = await _connect(SessionStore(key))
        try:
            frame = await _mount(comm)
            assert frame["type"] == "mount", frame
        finally:
            await comm.disconnect()
    [created] = await sync_to_async(_session_rows)() - before
    row = await sync_to_async(Session.objects.get)(session_key=created)
    remaining = (row.expire_date - timezone.now()).total_seconds()
    assert 0 < remaining <= 600, remaining


async def test_vanished_cached_db_session_mounts_fresh(staged):
    """Review M4 of #3206: the cached_db backend takes the same path."""
    from django.contrib.sessions.backends.cached_db import SessionStore as CachedDB

    def cached_db_key():
        session = CachedDB()
        session.create()
        return session.session_key

    key = await sync_to_async(cached_db_key)()
    await sync_to_async(CachedDB(key).delete)()
    with _settings():
        comm = await _connect(CachedDB(key))
        try:
            frame = await _mount(comm)
            assert frame["type"] == "mount", frame
            await comm.send_json_to({"type": "event", "event": "increment", "params": {}})
            reply = await comm.receive_json_from(timeout=5)
            assert reply["type"] in {"patch", "html_update"}, reply
        finally:
            await comm.disconnect()
