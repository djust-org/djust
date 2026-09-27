"""#3247: a state save still running after its request ended re-checks its session.

Since #3212 a save made inside a request's executors (the SSE event POST) runs
on the dedicated save pool, so it can outlive the request. It writes through
the session object its turn captured, and a logout in another request does not
change that object. Before writing, a pool save now looks its session key up
once in the store and is dropped with a debug line when the key is gone, or
when the stored session names a different authenticated user.

A key rotation made through the save's OWN session object stays a legitimate
save: ``login()`` in a legacy handler calls ``cycle_key()`` on the very object
the save writes, and the store then holds the pre-login copy (no user) under
the new key. That save must land, or the login is never persisted.

The unit cases drive :func:`djust._late_save.check_session` against the real
``db`` and ``cache`` backends. The end-to-end cases drive the SSE event POST
as Django serves it (inside a ``ThreadSensitiveContext``, so the save goes to
the pool), hold the save on its pool thread, log out through
``django.contrib.auth.logout`` on another request, and release it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from importlib import import_module

import pytest
from asgiref.sync import ThreadSensitiveContext, sync_to_async
from django.contrib.auth import SESSION_KEY, get_user, get_user_model, login, logout
from django.test import RequestFactory, override_settings
from django.urls import path
from django.utils.functional import SimpleLazyObject

from djust import LiveView
from djust._late_save import LateSaveDropped, check_session, detached
from djust.decorators import event_handler, state
from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions

pytestmark = pytest.mark.django_db(transaction=True)

DB = "django.contrib.sessions.backends.db"
CACHE = "django.contrib.sessions.backends.cache"
BACKENDS = [DB, CACHE]
CACHED_DB = "django.contrib.sessions.backends.cached_db"
FILE = "django.contrib.sessions.backends.file"
#: The unit cases cover every server-side backend ``_stored_session`` branches on.
ALL_BACKENDS = [DB, CACHE, CACHED_DB, FILE]
_MOD = __name__
VALVE_S = 10.0


def _store(engine):
    return import_module(engine).SessionStore


def _exists(engine, key):
    return _store(engine)().exists(key)


def _persisted(engine, data=None):
    session = _store(engine)()
    session.update(data or {"seed": 1})
    session.create()
    return session


def _as_pool_save(fn):
    """Run ``fn`` as the save pool runs a job (the check is active only there)."""
    return detached(fn)()


# --------------------------------------------------------------------------- #
# check_session against the real backends
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("engine", ALL_BACKENDS)
def test_a_session_flushed_by_another_request_is_not_written_or_recreated(engine):
    save = _persisted(engine, {SESSION_KEY: "1", "seed": 1})
    key = save.session_key
    save["liveview_/x/"] = {"count": 1}

    _store(engine)(key).flush()  # logout in another request

    def body():
        check_session(save, key)
        save.save()

    with pytest.raises(LateSaveDropped):
        _as_pool_save(body)
    assert not _exists(engine, key)
    assert save.session_key == key, "the save must not have created a replacement"


@pytest.mark.parametrize("engine", ALL_BACKENDS)
def test_a_session_that_now_names_another_user_is_not_written(engine):
    save = _persisted(engine, {SESSION_KEY: "1"})
    key = save.session_key
    other = _store(engine)(key)
    other[SESSION_KEY] = "2"
    other.save()

    save["liveview_/x/"] = {"count": 1}
    with pytest.raises(LateSaveDropped):
        _as_pool_save(lambda: check_session(save, key))
    assert _store(engine)(key).load().get("liveview_/x/") is None


@pytest.mark.parametrize("engine", ALL_BACKENDS)
def test_a_lazily_loaded_session_that_vanished_is_not_recreated(engine):
    """The save's object has not loaded yet: its first write loads, finds the
    row gone and resets the key to None, and ``save()`` would then CREATE a new
    session. No backend guard stops that; the key read at the start does."""
    key = _persisted(engine).session_key
    save = _store(engine)(key)  # not loaded
    _store(engine)(key).flush()

    def body():
        expected = save.session_key
        save["liveview_/x/"] = {"count": 1}  # loads: the key resets to None
        check_session(save, expected)
        save.save()

    with pytest.raises(LateSaveDropped):
        _as_pool_save(body)
    assert save.session_key is None, "precondition: the load reset the key"
    assert not _exists(engine, key)


@pytest.mark.parametrize("engine", ALL_BACKENDS)
def test_a_login_rotating_the_saves_own_session_still_saves(engine):
    """``login()`` in a handler on the object the save writes: ``cycle_key()``
    stored the pre-login copy (no user) under the new key, and the object
    holds the new user. The save is the only write that persists the login."""
    save = _persisted(engine, {"seed": 1})
    old_key = save.session_key
    save.cycle_key()
    save[SESSION_KEY] = "7"
    new_key = save.session_key
    assert new_key != old_key
    assert _store(engine)(new_key).load().get(SESSION_KEY) is None, "precondition"

    def body():
        check_session(save, new_key)
        save["liveview_/x/"] = {"count": 1}
        save.save()

    _as_pool_save(body)
    stored = _store(engine)(new_key).load()
    assert stored.get(SESSION_KEY) == "7"
    assert stored.get("liveview_/x/") == {"count": 1}
    assert not _exists(engine, old_key)


@pytest.mark.parametrize("engine", ALL_BACKENDS)
def test_a_same_user_key_rotation_still_saves(engine):
    """``update_session_auth_hash`` / a re-login of the same user rotate the key
    and keep the user."""
    save = _persisted(engine, {SESSION_KEY: "7"})
    save.cycle_key()
    new_key = save.session_key

    def body():
        check_session(save, new_key)
        save["liveview_/x/"] = {"count": 2}
        save.save()

    _as_pool_save(body)
    assert _store(engine)(new_key).load().get("liveview_/x/") == {"count": 2}


@pytest.mark.parametrize("engine", ALL_BACKENDS)
def test_a_login_in_another_request_drops_the_old_sessions_save(engine):
    """Another tab logs in: ``cycle_key()`` there moves the data to a new key
    and deletes the old one. The late save for the old key must neither
    recreate it nor touch the new login session."""
    save = _persisted(engine, {"seed": 1})
    old_key = save.session_key
    tab = _store(engine)(old_key)
    tab.cycle_key()
    tab[SESSION_KEY] = "9"
    tab.save()
    new_key = tab.session_key

    save["liveview_/x/"] = {"count": 1}
    with pytest.raises(LateSaveDropped):
        _as_pool_save(lambda: (check_session(save, old_key), save.save()))
    assert not _exists(engine, old_key)
    stored = _store(engine)(new_key).load()
    assert stored.get(SESSION_KEY) == "9" and "liveview_/x/" not in stored


@pytest.mark.parametrize("engine", ALL_BACKENDS)
def test_off_the_pool_the_check_costs_nothing(engine, monkeypatch):
    """A WebSocket or ``PooledHTTP`` save is still awaited by its turn: no lookup."""
    save = _persisted(engine)
    key = save.session_key
    _store(engine)(key).flush()

    def no_lookup(self):
        raise AssertionError("the store was read off the save pool")

    monkeypatch.setattr(_store(engine), "load", no_lookup)
    check_session(save, key)  # returns without a lookup


def test_a_never_persisted_session_may_be_created():
    """No key means no stored session another request could have logged out."""
    save = _store(DB)()
    _as_pool_save(lambda: check_session(save, None))


# --------------------------------------------------------------------------- #
# End to end: the SSE event POST's late save, then a logout
# --------------------------------------------------------------------------- #

#: Holds a save on its pool thread until the test releases it.
GATE = threading.Event()
HELD = threading.Event()
ARMED = threading.Event()


def _hold_on_the_pool():
    if ARMED.is_set() and threading.current_thread().name.startswith("djust-state-save"):
        HELD.set()
        GATE.wait(timeout=VALVE_S)


class LegacyLate3247Page(LiveView):
    exposure_policy = "legacy"
    enable_state_snapshot = True
    template = "<div dj-root><span>{{ count }}</span></div>"

    def mount(self, request, **kwargs):
        self.count = 0

    def get_context_data(self, **kwargs):
        _hold_on_the_pool()  # the legacy save reads the context before writing
        return super().get_context_data(count=self.count, **kwargs)

    @event_handler()
    def increment(self, **kwargs):
        self.count += 1


class ExplicitLate3247Page(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root><span>{{ count }}</span></div>"
    count = state(0, persist="server")

    def mount(self, request, **kwargs):
        self.count = 0

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)

    @event_handler()
    def increment(self, **kwargs):
        self.count += 1


#: The user ``SignIn3247Page.sign_in`` logs in.
SIGN_IN = {}


class SignIn3247Page(LegacyLate3247Page):
    """A legacy page whose handler logs the user in on ``self.request``: on SSE
    that is the mount request, whose session object the event save writes."""

    @event_handler()
    def sign_in(self, **kwargs):
        user = get_user_model().objects.get(pk=SIGN_IN["pk"])
        login(self.request, user, backend="django.contrib.auth.backends.ModelBackend")
        self.count += 1


urlpatterns = [
    path("late-legacy/", LegacyLate3247Page.as_view()),
    path("late-explicit/", ExplicitLate3247Page.as_view()),
    path("late-sign-in/", SignIn3247Page.as_view()),
]
PAGES = {
    "legacy": (f"{_MOD}.LegacyLate3247Page", "/late-legacy/"),
    "explicit": (f"{_MOD}.ExplicitLate3247Page", "/late-explicit/"),
    "sign-in": (f"{_MOD}.SignIn3247Page", "/late-sign-in/"),
}


@pytest.fixture
def staged(monkeypatch):
    from djust._exposure_sessions import ServerStateSession

    original_capture = ServerStateSession._capture

    def held_capture(self, values):
        _hold_on_the_pool()  # the explicit save projects before writing
        return original_capture(self, values)

    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    monkeypatch.setattr(ServerStateSession, "_capture", held_capture)
    GATE.clear()
    HELD.clear()
    ARMED.clear()
    valve = threading.Timer(VALVE_S, GATE.set)
    valve.start()
    yield
    GATE.set()
    valve.cancel()
    ARMED.clear()
    _sse_sessions.clear()


def _request(engine, method, url, body, key):
    factory = RequestFactory()
    if method == "GET":
        request = factory.get(url, data=body)
    else:
        request = factory.post(url, data=json.dumps(body), content_type="application/json")
    request.session = _store(engine)(key)
    request.user = SimpleLazyObject(lambda: get_user(request))
    request.tenant = None
    return request


def _logged_in_key(engine):
    user = get_user_model().objects.create_user(username="u%s" % uuid.uuid4().hex[:8])
    request = RequestFactory().get("/")
    request.session = _store(engine)()
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    request.session.save()
    return request.session.session_key, user


def _logout_elsewhere(engine, key, user):
    request = RequestFactory().get("/logout/")
    request.session = _store(engine)(key)
    request.user = user
    logout(request)


def _pool_writes(engine, monkeypatch):
    """Record every store write a save-pool thread makes."""
    store = _store(engine)
    writes = []
    for name in ("save", "create"):
        original = getattr(store, name)

        def spy(self, *args, _original=original, _name=name, **kwargs):
            if threading.current_thread().name.startswith("djust-state-save"):
                writes.append(_name)
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(store, name, spy)
    return writes


async def _until(predicate, what, limit=VALVE_S):
    loop = asyncio.get_running_loop()
    end = loop.time() + limit
    while not predicate():
        if loop.time() > end:
            raise AssertionError("timed out waiting for " + what)
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["legacy", "explicit"])
@pytest.mark.parametrize("engine", BACKENDS)
async def test_a_late_save_after_a_logout_writes_nothing(
    engine, policy, staged, monkeypatch, caplog
):
    view_path, url = PAGES[policy]
    with override_settings(
        ROOT_URLCONF=_MOD,
        LIVEVIEW_ALLOWED_MODULES=[_MOD],
        DEBUG=False,
        SESSION_ENGINE=engine,
        DJUST_EXPLICIT_STATE_SAVE_TIMEOUT=0.05,
    ):
        key, user = await sync_to_async(_logged_in_key)(engine)
        sid = str(uuid.uuid4())
        get = await sync_to_async(_request)(
            engine, "GET", f"/djust/sse/{sid}/", {"view": view_path, "_djust_url": url}, key
        )
        assert (await DjustSSEStreamView().get(get, session_id=sid)).status_code == 200
        sse = _sse_sessions[sid]
        runtime = sse.runtime

        writes = _pool_writes(engine, monkeypatch)
        ARMED.set()
        post = await sync_to_async(_request)(
            engine,
            "POST",
            f"/djust/sse/{sid}/message/",
            {"type": "event", "event": "increment", "params": {}},
            key,
        )
        async with ThreadSensitiveContext():  # what Django's ASGIHandler wraps
            response = await DjustSSEMessageView().post(post, session_id=sid)
        assert response.status_code == 200
        assert HELD.is_set(), "the save never reached its pool thread; vacuous"
        pending = runtime._explicit_save_pending
        assert pending is not None and not pending.done(), "the save must outlive the POST"

        await sync_to_async(_logout_elsewhere)(engine, key, user)
        assert not await sync_to_async(_exists)(engine, key), "precondition: logged out"

        caplog.set_level(logging.DEBUG)
        GATE.set()
        await _until(pending.done, "the late save to finish")
        await asyncio.sleep(0)  # let the future's done callbacks run
        assert isinstance(pending.exception(), LateSaveDropped), pending.exception()
        assert writes == [], "the late save wrote to the store: %s" % writes
        assert not await sync_to_async(_exists)(engine, key), "the session was recreated"
        messages = [r.getMessage() for r in caplog.records]
        assert any("Late state save dropped" in m for m in messages), messages
        # Dropped at debug, not reported as a storage failure.
        assert not any("failed after its turn" in m for m in messages), messages


# --------------------------------------------------------------------------- #
# The other pool write sites: the legacy root and sticky-child saves, driven
# through the runtime inside a request's ``ThreadSensitiveContext``.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize("site", ["root", "sticky"])
async def test_a_legacy_runtime_save_on_the_pool_after_a_logout_writes_nothing(
    site, monkeypatch, caplog
):
    from djust.tests.test_legacy_save_deadline_3212 import StickyChild, _setup

    runtime, parent, key = await _setup()
    await sync_to_async(lambda: _store(DB)(key).flush())()  # logout elsewhere
    writes = _pool_writes(DB, monkeypatch)

    with caplog.at_level(logging.DEBUG):
        async with ThreadSensitiveContext():  # a request's executors: the pool
            if site == "root":
                assert await runtime._persist_state_after_event(parent, "increment") is False
            else:
                child = StickyChild()
                child.clicks = 3
                parent._register_child("side", child)
                await runtime._persist_sticky_child_after_event(child, "click")
    assert writes == [], writes
    assert not await sync_to_async(_exists)(DB, key)
    messages = [r.getMessage() for r in caplog.records]
    assert any("Late state save dropped" in m for m in messages), messages
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING], messages


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", BACKENDS)
async def test_a_login_in_the_handler_racing_the_late_save_still_saves(engine, staged, monkeypatch):
    """``login()`` rotates the save's own session (``cycle_key``) and stores the
    pre-login copy under the new key. The late save must land there: it is the
    only write that persists the login and the event's state."""
    view_path, url = PAGES["sign-in"]
    with override_settings(
        ROOT_URLCONF=_MOD, LIVEVIEW_ALLOWED_MODULES=[_MOD], DEBUG=False, SESSION_ENGINE=engine
    ):
        user = await sync_to_async(get_user_model().objects.create_user)(
            username="s%s" % uuid.uuid4().hex[:8]
        )
        SIGN_IN["pk"] = user.pk
        old_key = (await sync_to_async(_persisted)(engine)).session_key
        sid = str(uuid.uuid4())
        get = await sync_to_async(_request)(
            engine, "GET", f"/djust/sse/{sid}/", {"view": view_path, "_djust_url": url}, old_key
        )
        assert (await DjustSSEStreamView().get(get, session_id=sid)).status_code == 200
        runtime = _sse_sessions[sid].runtime

        ARMED.set()
        post = await sync_to_async(_request)(
            engine,
            "POST",
            f"/djust/sse/{sid}/message/",
            {"type": "event", "event": "sign_in", "params": {}},
            old_key,
        )
        async with ThreadSensitiveContext():
            response = await DjustSSEMessageView().post(post, session_id=sid)
        assert response.status_code == 200
        assert HELD.is_set(), "the save never reached its pool thread; vacuous"
        pending = runtime._explicit_save_pending
        new_key = get.session.session_key
        assert new_key != old_key, "precondition: login() rotated the save's session"

        GATE.set()
        await _until(pending.done, "the late save to finish")
        assert pending.exception() is None, pending.exception()

        def stored():
            return _store(engine)(new_key).load()

        data = await sync_to_async(stored)()
        assert data.get(SESSION_KEY) == str(user.pk), "the login was never persisted"
        assert data.get(f"liveview_{url}", {}).get("count") == 1, data
        assert not await sync_to_async(_exists)(engine, old_key)


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", BACKENDS)
async def test_an_explicit_turn_whose_save_finds_the_session_logged_out_asks_for_a_reload(
    engine, staged, monkeypatch, caplog
):
    """The turn is still waiting when its pool save finds the session gone: the
    success frame is withheld (E3) with the reload error, and no storage-failure
    warning or traceback is logged for an ordinary logout."""
    view_path, url = PAGES["explicit"]
    with override_settings(
        ROOT_URLCONF=_MOD,
        LIVEVIEW_ALLOWED_MODULES=[_MOD],
        DEBUG=False,
        SESSION_ENGINE=engine,
        DJUST_EXPLICIT_STATE_SAVE_TIMEOUT=5,
    ):
        key, user = await sync_to_async(_logged_in_key)(engine)
        sid = str(uuid.uuid4())
        get = await sync_to_async(_request)(
            engine, "GET", f"/djust/sse/{sid}/", {"view": view_path, "_djust_url": url}, key
        )
        assert (await DjustSSEStreamView().get(get, session_id=sid)).status_code == 200
        sse = _sse_sessions[sid]
        while not sse.queue.empty():
            sse.queue.get_nowait()

        ARMED.set()
        post = await sync_to_async(_request)(
            engine,
            "POST",
            f"/djust/sse/{sid}/message/",
            {"type": "event", "event": "increment", "params": {}},
            key,
        )

        async def serve():
            async with ThreadSensitiveContext():
                return await DjustSSEMessageView().post(post, session_id=sid)

        caplog.set_level(logging.DEBUG)
        task = asyncio.ensure_future(serve())
        await _until(HELD.is_set, "the save to reach its pool thread")
        await sync_to_async(_logout_elsewhere)(engine, key, user)
        GATE.set()
        await _until(task.done, "the POST to finish")
        assert task.result().status_code == 200

    frames = [f for f in list(sse.queue._queue) if f]
    errors = [f for f in frames if f.get("type") == "error"]
    assert errors and errors[0].get("code") == "state_error", frames
    assert not errors[0].get("transient"), errors
    assert not [f for f in frames if f.get("type") in ("patch", "html_update", "noop")], frames
    assert not await sync_to_async(_exists)(engine, key)
    messages = [r.getMessage() for r in caplog.records]
    assert any("Late state save dropped" in m for m in messages), messages
    warned = [
        r.getMessage()
        for r in caplog.records
        if r.levelno >= logging.WARNING and r.name.startswith("djust")
    ]
    assert not warned, warned


# --------------------------------------------------------------------------- #
# Review Y1: a lookup ERROR is not a logout
# --------------------------------------------------------------------------- #

from django.core.cache.backends.locmem import LocMemCache  # noqa: E402


class FlakyCache(LocMemCache):
    """A cache whose next ``failures`` reads raise, as Redis or memcached do
    during a blip. Class-level, because each thread builds its own instance."""

    failures = 0

    def _maybe_fail(self):
        if FlakyCache.failures > 0:
            FlakyCache.failures -= 1
            raise ConnectionError("cache unavailable")

    def get(self, *args, **kwargs):
        self._maybe_fail()
        return super().get(*args, **kwargs)

    def has_key(self, *args, **kwargs):
        self._maybe_fail()
        return super().has_key(*args, **kwargs)


_FLAKY = override_settings(
    CACHES={
        "default": {
            "BACKEND": f"{_MOD}.FlakyCache",
            "LOCATION": "late-save-3247-flaky",
        }
    },
    SESSION_ENGINE=CACHE,
)


def _lookup_warnings(caplog):
    return [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "session lookup failed" in r.getMessage()
    ]


@pytest.mark.parametrize("engine", [CACHE, CACHED_DB])
def test_a_cache_error_during_the_lookup_does_not_drop_the_save(engine, caplog):
    """Django's cache backend swallows the error in ``load()`` and reports the
    session as missing. The lookup must not: the save goes ahead, and the error
    is a warning with its traceback."""
    with _FLAKY:
        FlakyCache.failures = 0
        save = _persisted(engine, {SESSION_KEY: "1"})
        key = save.session_key
        save["liveview_/x/"] = {"count": 3}
        FlakyCache.failures = 1
        caplog.set_level(logging.DEBUG)

        def body():
            check_session(save, key)
            save.save()

        _as_pool_save(body)  # not LateSaveDropped
        FlakyCache.failures = 0
        assert _store(engine)(key).load().get("liveview_/x/") == {"count": 3}
    messages = [r.getMessage() for r in caplog.records]
    assert not any("Late state save dropped" in m for m in messages), messages
    if engine == CACHE:
        [warning] = _lookup_warnings(caplog)
        assert warning.exc_info and warning.exc_info[0] is ConnectionError
    else:
        # cached_db treats its cache as optional and asks the database.
        assert not _lookup_warnings(caplog)


@pytest.mark.asyncio
async def test_a_cache_blip_on_a_legacy_pool_save_keeps_the_save(monkeypatch, caplog):
    """The runtime's legacy save on the pool, with the cache failing once, for
    the lookup: the save lands and the failure is a warning, not a logout."""
    from djust.tests.test_legacy_save_deadline_3212 import Counter

    from djust.runtime import ViewRuntime
    from djust.tests.test_runtime_state_save_tt_1894 import MockTransport

    with _FLAKY:
        FlakyCache.failures = 0
        key = (await sync_to_async(_persisted)(CACHE)).session_key
        request = RequestFactory().get("/legacy-3212/")
        request.session = _store(CACHE)(key)
        await sync_to_async(request.session.get)("seed")  # loaded, as after mount
        view = Counter()
        view.count = 4
        view._djust_mount_request = request
        runtime = ViewRuntime(MockTransport())
        runtime.view_instance = view

        caplog.set_level(logging.DEBUG)
        FlakyCache.failures = 1
        async with ThreadSensitiveContext():
            assert await runtime._persist_state_after_event(view, "increment") is True
        FlakyCache.failures = 0
        stored = await sync_to_async(lambda: _store(CACHE)(key).load())()
    assert stored.get("liveview_/legacy-3212/") == {"count": 4}, stored
    assert _lookup_warnings(caplog)
    messages = [r.getMessage() for r in caplog.records]
    assert not any("Late state save dropped" in m for m in messages), messages


# --------------------------------------------------------------------------- #
# Review Y3: the key is read when the save body starts, on the real save path
# --------------------------------------------------------------------------- #


def _session_rows():
    from django.contrib.sessions.models import Session

    return Session.objects.count()


@pytest.mark.asyncio
@pytest.mark.parametrize("site", ["root", "sticky"])
async def test_an_unloaded_session_that_vanished_is_not_recreated_by_the_runtime_save(
    site, monkeypatch
):
    """The save's session object has not been loaded when the save body starts.
    The body's first write loads it, finds the row gone and resets the key to
    ``None``; checked at write time, ``None`` reads as "never persisted" and
    ``save()`` creates a new session. The key read at the start of the body
    prevents that.

    The stock runtime mount loads the session (its restore reads it), so this
    is defence in depth for a session that reaches the save still lazy.
    """
    from djust.tests.test_legacy_save_deadline_3212 import Counter, StickyChild

    from djust.runtime import ViewRuntime
    from djust.tests.test_runtime_state_save_tt_1894 import MockTransport

    key = (await sync_to_async(_persisted)(DB)).session_key
    request = RequestFactory().get("/legacy-3212/")
    request.session = _store(DB)(key)  # lazy: never loaded
    parent = Counter()
    parent.count = 4
    parent._djust_mount_request = request
    runtime = ViewRuntime(MockTransport())
    runtime.view_instance = parent

    await sync_to_async(lambda: _store(DB)(key).flush())()  # logout elsewhere
    rows = await sync_to_async(_session_rows)()
    writes = _pool_writes(DB, monkeypatch)

    async with ThreadSensitiveContext():
        if site == "root":
            assert await runtime._persist_state_after_event(parent, "increment") is False
        else:
            child = StickyChild()
            child.clicks = 3
            parent._register_child("side", child)
            await runtime._persist_sticky_child_after_event(child, "click")
    assert request.session.session_key is None, "precondition: the body's load reset the key"
    assert writes == [], writes
    assert await sync_to_async(_session_rows)() == rows, "the save created a new session"


# --------------------------------------------------------------------------- #
# Review N1: an expired ``file`` session is gone, and the lookup writes nothing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("corrupt", [False, True])
def test_an_expired_or_corrupt_file_session_lookup_writes_no_file(
    tmp_path, corrupt, caplog, monkeypatch
):
    """``file.load()`` deletes an expired or corrupt session and CREATES a new
    one, so a lookup through it wrote an orphan file and did not drop the save.
    An expired session is gone; a corrupt one exists with no user."""
    import os

    # The file backend caches its storage path on the class.
    monkeypatch.setattr(_store(FILE), "_storage_path", str(tmp_path))
    with override_settings(SESSION_FILE_PATH=str(tmp_path), SESSION_ENGINE=FILE):
        save = _store(FILE)()
        save[SESSION_KEY] = "1"
        if not corrupt:
            save.set_expiry(-10)  # already expired
        save.create()
        key = save.session_key
        if corrupt:
            path = save._key_to_file(key)
            with open(path, "w", encoding="ascii") as fh:
                fh.write("not-a-signed-session")
        before = sorted(os.listdir(tmp_path))
        assert len(before) == 1, before

        caplog.set_level(logging.DEBUG)
        if corrupt:
            _as_pool_save(lambda: check_session(save, key))  # exists, no user
        else:
            with pytest.raises(LateSaveDropped):
                _as_pool_save(lambda: check_session(save, key))
        assert sorted(os.listdir(tmp_path)) == before, "the lookup wrote a session file"
