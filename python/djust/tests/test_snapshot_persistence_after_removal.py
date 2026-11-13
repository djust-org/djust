"""What survives the removal of the legacy snapshot persistence (2.0).

2.0 removed the legacy signed back-navigation snapshot. What a view keeps
across a reconnect or a Back is now only what it declares:

* ``state(persist="server")`` is stored server-side and restored on reconnect;
* ``state(persist="client", client=True)`` travels in a signed token the
  browser echoes back, and a tampered, expired or foreign token is refused;
* ``handle_restore`` runs after either restore;
* a legacy-pinned view keeps nothing and mounts fresh.

Every case here drives a real ``WebsocketCommunicator`` against
``LiveViewConsumer`` with a real DB session, so the frames are the ones a
browser receives. The removed names are refused at the end of the file.
"""

from __future__ import annotations

import asyncio

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings

from djust import LiveView, event_handler
from djust.decorators import state

pytest.importorskip("channels")

pytestmark = pytest.mark.django_db(transaction=True)

MOD = __name__
PATH = "/kept/"
TOKEN = "state_snapshot_signed"


class ClientKept(LiveView):
    """``step`` is held by the browser, signed; ``scratch`` is held by nobody."""

    exposure_policy = "explicit"
    template = '<div dj-view="' + MOD + '.ClientKept" dj-id="0"><b>step={{ step }}</b></div>'
    step = state(0, persist="client", client=True)
    scratch = state(0)

    @event_handler()
    def bump(self, **kwargs):
        self.step += 1
        self.scratch += 1

    def get_context_data(self, **kwargs):
        return {"step": self.step}


class ServerKept(LiveView):
    """``count`` is stored server-side; ``handle_restore`` reports that it ran."""

    exposure_policy = "explicit"
    template = (
        '<div dj-view="' + MOD + '.ServerKept" dj-id="0">'
        "<b>count={{ count }}</b><i>restored={{ restored }}</i></div>"
    )
    count = state(0, persist="server")
    restored = state("no")

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1

    def handle_restore(self, request):
        self.restored = "yes:%d" % self.count

    def get_context_data(self, **kwargs):
        return {"count": self.count, "restored": self.restored}


class LegacyPlain(LiveView):
    """A legacy-pinned view with no persistence of any kind."""

    exposure_policy = "legacy"
    template = '<div dj-view="' + MOD + '.LegacyPlain" dj-id="0"><b>n={{ n }}</b></div>'

    def mount(self, request, **kwargs):
        self.n = 0

    @event_handler()
    def bump(self, **kwargs):
        self.n += 1


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=[MOD], DEBUG=False, DJUST_CONFIG={}, DJUST_TENANTS={}
    ):
        yield


def _fresh_key():
    store = SessionStore()
    store.create()
    return store.session_key


async def _connect(key):
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = SessionStore(key)
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from()  # connect ack
    return communicator


async def _until_frame(communicator, *types, timeout=15.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    frames = []
    while True:
        remaining = deadline - loop.time()
        assert remaining > 0, "no frame of type %r; got %r" % (types, frames)
        frames.append(await communicator.receive_json_from(timeout=remaining))
        if frames[-1].get("type") in types:
            return frames[-1]


async def _mount(communicator, cls, *, token=None, verb="mount"):
    frame = {"type": verb, "view": MOD + "." + cls.__name__, "url": PATH}
    if token is not None:
        frame["state_snapshot"] = {"view_slug": frame["view"], "state_json": token}
    await communicator.send_json_to(frame)
    reply = await _until_frame(communicator, "mount", "error")
    assert reply["type"] == "mount", reply
    return reply


async def _bump(communicator):
    await communicator.send_json_to({"type": "event", "event": "bump", "params": {}})
    reply = await _until_frame(communicator, "patch", "html_update", "noop", "error")
    assert reply["type"] != "error", reply
    return reply


async def _token_after_two_bumps(key):
    """Mount ``ClientKept``, move ``step`` to 2 and return the last token sent."""
    socket = await _connect(key)
    try:
        mounted = await _mount(socket, ClientKept)
        assert isinstance(mounted.get(TOKEN), str) and "step=0" in mounted["html"]
        await _bump(socket)
        token = (await _bump(socket)).get(TOKEN)
        assert isinstance(token, str) and token, "an event refreshes the client token"
        return token
    finally:
        await socket.disconnect()


# --------------------------------------------------------------------------- #
# state(persist="client", client=True): the signed client token
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("verb", ["mount", "live_redirect_mount"])
async def test_client_state_round_trips_through_the_signed_token(verb):
    """A reconnect (``mount``) and a Back (``live_redirect_mount``) both echo
    the token; the declared field comes back, the undeclared one does not."""
    key = await sync_to_async(_fresh_key)()
    token = await _token_after_two_bumps(key)

    socket = await _connect(key)
    try:
        restored = await _mount(socket, ClientKept, token=token, verb=verb)
        assert "step=2" in restored["html"], restored["html"]
        # The next event starts from the restored value, and from a fresh
        # ``scratch``: only the declared client field travelled.
        after = await _bump(socket)
        assert after["type"] in ("patch", "html_update"), after
        assert "step=3" in str(after), after
    finally:
        await socket.disconnect()


async def test_a_tampered_token_is_refused():
    key = await sync_to_async(_fresh_key)()
    token = await _token_after_two_bumps(key)
    forged = token.replace('"step":2', '"step":9')
    assert forged != token, "the control: the payload was really changed"

    socket = await _connect(key)
    try:
        for bad in (forged, token[:-1] + ("A" if token[-1] != "A" else "B"), '{"step":9}'):
            reply = await _mount(socket, ClientKept, token=bad)
            assert "step=0" in reply["html"], (bad, reply["html"])
    finally:
        await socket.disconnect()


class _FrozenTime:
    """``time`` as the snapshot code reads it, stopped at ``now`` (#4100).

    The token's age is decided by the clock the signer and the codec read, so
    the test sets that clock instead of waiting: however slow the run, the
    young token stays young and the old one is old.
    """

    def __init__(self, now: float) -> None:
        import time as _real_time

        self._real = _real_time
        self.now = now

    def time(self) -> float:
        return self.now

    def __getattr__(self, name: str):
        return getattr(self._real, name)


def _clock_at(now: float):
    """Set the clock of the snapshot codec and of Django's signer together."""
    import contextlib
    from unittest import mock

    import django.core.signing as signing

    import djust._exposure_snapshots as snapshots

    frozen = _FrozenTime(now)
    stack = contextlib.ExitStack()
    stack.enter_context(mock.patch.object(snapshots, "time", frozen))
    stack.enter_context(mock.patch.object(signing, "time", frozen))
    return stack


async def test_an_expired_token_is_refused():
    import time

    key = await sync_to_async(_fresh_key)()
    issued = float(int(time.time()))
    with override_settings(DJUST_STATE_SNAPSHOT_MAX_AGE=1):
        with _clock_at(issued):
            token = await _token_after_two_bumps(key)
        socket = await _connect(key)
        try:
            # The control, on the clock the token was issued at: valid while young.
            with _clock_at(issued):
                fresh = await _mount(socket, ClientKept, token=token)
            assert "step=2" in fresh["html"], "the control: the token is valid while young"
        finally:
            await socket.disconnect()
        socket = await _connect(key)
        try:
            # Past its one-second max age by the clock, not by waiting.
            with _clock_at(issued + 2):
                expired = await _mount(socket, ClientKept, token=token)
            assert "step=0" in expired["html"], expired["html"]
        finally:
            await socket.disconnect()


async def test_a_token_from_another_session_is_refused():
    key = await sync_to_async(_fresh_key)()
    token = await _token_after_two_bumps(key)
    other = await sync_to_async(_fresh_key)()

    socket = await _connect(other)
    try:
        reply = await _mount(socket, ClientKept, token=token)
        assert "step=0" in reply["html"], reply["html"]
    finally:
        await socket.disconnect()


# --------------------------------------------------------------------------- #
# state(persist="server") and handle_restore
# --------------------------------------------------------------------------- #


async def test_server_state_restores_on_reconnect_and_handle_restore_runs():
    key = await sync_to_async(_fresh_key)()
    first = await _connect(key)
    try:
        mounted = await _mount(first, ServerKept)
        assert "count=0" in mounted["html"] and "restored=no" in mounted["html"]
        await _bump(first)
        await _bump(first)
    finally:
        await first.disconnect()

    second = await _connect(key)
    try:
        restored = await _mount(second, ServerKept)
        # ``handle_restore`` ran after the stored value was back in place.
        assert "count=2" in restored["html"], restored["html"]
        assert "restored=yes:2" in restored["html"], restored["html"]
    finally:
        await second.disconnect()

    stranger = await _connect(await sync_to_async(_fresh_key)())
    try:
        fresh = await _mount(stranger, ServerKept)
        assert "count=0" in fresh["html"] and "restored=no" in fresh["html"], fresh["html"]
    finally:
        await stranger.disconnect()


# --------------------------------------------------------------------------- #
# A legacy-pinned view keeps nothing
# --------------------------------------------------------------------------- #


async def test_a_legacy_view_signs_nothing_and_mounts_fresh_on_reconnect():
    from djust.tests.test_exposure_snapshots import _removed_legacy_token

    key = await sync_to_async(_fresh_key)()
    first = await _connect(key)
    try:
        mounted = await _mount(first, LegacyPlain)
        assert TOKEN not in mounted, mounted.keys()
        changed = await _bump(first)
        assert changed["type"] in ("patch", "html_update") and TOKEN not in changed
    finally:
        await first.disconnect()

    second = await _connect(key)
    try:
        plain = await _mount(second, LegacyPlain)
        assert "n=0" in plain["html"], plain["html"]
        # A token djust 1.x signed for this view and session, still in a
        # browser after the upgrade, restores nothing.
        old = _removed_legacy_token('{"n":7}', MOD + ".LegacyPlain", key)
        for verb in ("mount", "live_redirect_mount"):
            echoed = await _mount(second, LegacyPlain, token=old, verb=verb)
            assert "n=0" in echoed["html"], (verb, echoed["html"])
    finally:
        await second.disconnect()


# --------------------------------------------------------------------------- #
# The removed names are refused, with the replacement and the guide
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", ["sign_snapshot", "unsign_snapshot", "SNAPSHOT_SALT"])
def test_importing_a_removed_signing_name_names_the_replacement(name):
    import djust.security

    with pytest.raises(ImportError) as raised:
        exec("from djust.security import %s" % name, {})
    message = str(raised.value)
    assert "removed in djust 2.0" in message
    assert 'persist="client", client=True' in message
    assert "migrating-to-2.0" in message
    with pytest.raises(ImportError, match="removed in djust 2.0"):
        getattr(djust.security, name)


def test_the_snapshot_lifetime_helper_is_still_importable_from_security():
    from djust._exposure_snapshots import DEFAULT_MAX_AGE, get_max_age
    from djust.security import DEFAULT_MAX_AGE as exported_default
    from djust.security import get_max_age as exported

    assert exported is get_max_age and exported_default == DEFAULT_MAX_AGE == 3600


def test_an_unknown_security_name_is_still_an_ordinary_attribute_error():
    import djust.security

    assert not hasattr(djust.security, "no_such_name_was_ever_here")
