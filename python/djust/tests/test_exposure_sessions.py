"""Real session-store tests for the internal ADR-038 persistence adapter."""

from dataclasses import replace

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.backends.signed_cookies import SessionStore as CookieStore

from djust._exposure import ExposureContract, ExposureError, FieldExposure
from djust._exposure_sessions import ServerStateSession, StateBinding


@pytest.fixture
def contract():
    return ExposureContract("app.View", {"count": FieldExposure(persist="server")})


@pytest.fixture
def store(db, contract):
    session = SessionStore()
    session.create()
    binding = StateBinding(session.session_key, "user:1", "tenant:1", "/orders/1/")
    return ServerStateSession(session, contract, binding, max_age=60)


def test_database_round_trip_is_detached_and_cookie_is_only_an_identifier(store):
    values = {"count": [1], "internal": "DO_NOT_STORE"}
    store.save(values)
    values["count"].append(2)
    other = ServerStateSession(SessionStore(store.binding.session), store.contract, store.binding)
    assert other.load() == {"count": [1]}
    assert "DO_NOT_STORE" not in repr(other.session.load())
    assert len(other.session.session_key) == 32
    restored = other.load()
    restored["count"].append(3)
    assert other.load() == {"count": [1]}


@pytest.mark.parametrize("store_class", [CookieStore, type("Disguised", (CookieStore,), {})])
def test_readable_sessions_rejected_before_values_are_stored(store_class, contract):
    session = store_class()
    with pytest.raises(ExposureError, match="server-side"):
        ServerStateSession(session, contract, StateBinding("session123", "anonymous", "none", "/"))
    assert dict(session.items()) == {}


def test_unknown_custom_backends_do_not_inherit_a_confidentiality_grant(contract):
    class Unknown(SessionStore):
        pass

    with pytest.raises(ExposureError, match="server-side"):
        ServerStateSession(
            Unknown(), contract, StateBinding("session123", "anonymous", "none", "/")
        )


@pytest.mark.parametrize("part", ["session", "user", "tenant", "view"])
def test_cross_identity_restore_rejected_before_returning_values(store, part):
    store.save({"count": 7})
    changed = replace(store.binding, **{part: "different123"})
    # Copy the envelope to the other identity's slot to test validation, not a cache miss.
    payload = store.session[store.key]
    if part == "session":
        other_session = SessionStore()
        other_session.create()
        changed = replace(changed, session=other_session.session_key)
    else:
        other_session = store.session
    other = ServerStateSession(other_session, store.contract, changed)
    other.session[other.key] = payload
    with pytest.raises(ExposureError, match="binding"):
        other.load()


def test_rotation_invalidates_existing_adapter(store):
    store.save({"count": 7})
    store.session.cycle_key()
    with pytest.raises(ExposureError, match="session"):
        store.load()
    with pytest.raises(ExposureError, match="session"):
        store.save({"count": 8})


@pytest.mark.parametrize("elapsed", [-1, 60, 61])
def test_expired_and_future_envelopes_fail_closed(store, monkeypatch, elapsed):
    monkeypatch.setattr("djust._exposure_sessions.time.time", lambda: 1000)
    store.save({"count": 7})
    monkeypatch.setattr("djust._exposure_sessions.time.time", lambda: 1000 + elapsed)
    with pytest.raises(ExposureError, match="expired|timestamp"):
        store.load()


def test_current_shorter_ttl_applies_to_old_state(store, monkeypatch):
    monkeypatch.setattr("djust._exposure_sessions.time.time", lambda: 1000)
    store.save({"count": 7})
    monkeypatch.setattr("djust._exposure_sessions.time.time", lambda: 1030)
    tighter = ServerStateSession(store.session, store.contract, store.binding, max_age=20)
    with pytest.raises(ExposureError, match="expired"):
        tighter.load()


@pytest.mark.parametrize("mutation", ["legacy", "extra", "schema", "fields", "timestamp"])
def test_invalid_payload_cannot_restore(store, mutation):
    store.save({"count": 7})
    payload = store.session[store.key]
    if mutation == "legacy":
        payload = {"count": 7}
    elif mutation == "extra":
        payload["extra"] = "secret"
    elif mutation == "schema":
        payload["state"]["schema"] = "old"
    elif mutation == "fields":
        payload["state"]["values"]["internal"] = "secret"
    else:
        payload["created"] = True
    store.session[store.key] = payload
    with pytest.raises(ExposureError):
        store.load()


def test_storage_failure_propagates_without_client_fallback(store, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("storage unavailable")

    monkeypatch.setattr(store.session, "save", fail)
    with pytest.raises(OSError, match="storage unavailable"):
        store.save({"count": 7})


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_async_database_round_trip(contract):
    session = SessionStore()
    await session.acreate()
    binding = StateBinding(session.session_key, "user:1", "tenant:1", "/orders/1/")
    first = ServerStateSession(session, contract, binding)
    await first.asave({"count": 42})
    second = ServerStateSession(SessionStore(session.session_key), contract, binding)
    assert await second.aload() == {"count": 42}


@pytest.mark.parametrize("part", ["session", "user", "tenant", "view"])
@pytest.mark.parametrize("value", ["", None, 1, "x" * 2049])
def test_binding_requires_bounded_explicit_identifiers(part, value):
    values = {"session": "session123", "user": "anonymous", "tenant": "none", "view": "/"}
    values[part] = value
    with pytest.raises(ExposureError):
        StateBinding(**values)


@pytest.mark.parametrize("backend", ["db", "cached_db", "cache", "file"])
def test_supported_backend_round_trip(db, contract, settings, tmp_path, backend):
    from importlib import import_module

    settings.SESSION_FILE_PATH = str(tmp_path)
    cls = import_module("django.contrib.sessions.backends." + backend).SessionStore
    session = cls()
    session.create()
    binding = StateBinding(session.session_key, "anonymous", "none", "/")
    adapter = ServerStateSession(session, contract, binding)
    assert adapter.load() is None
    adapter.save({"count": 3})
    assert ServerStateSession(cls(session.session_key), contract, binding).load() == {"count": 3}
    session.delete()


def test_deleted_session_is_rejected_after_lazy_load(store):
    store.save({"count": 7})
    stale = ServerStateSession(SessionStore(store.binding.session), store.contract, store.binding)
    store.session.delete()
    with pytest.raises(ExposureError, match="session"):
        stale.load()


def test_deleted_session_cannot_be_recreated_by_write(store):
    store.save({"count": 7})
    stale = ServerStateSession(SessionStore(store.binding.session), store.contract, store.binding)
    store.session.delete()
    with pytest.raises(ExposureError, match="session"):
        stale.save({"count": 9})
    assert stale.session.session_key is None


def test_envelope_overhead_counts_towards_storage_budget(store):
    from djust._exposure import StateLimits

    small = ExposureContract(
        "app.View", {"count": FieldExposure(persist="server")}, limits=StateLimits(max_bytes=100)
    )
    adapter = ServerStateSession(store.session, small, store.binding)
    assert small.project({"count": 1}, "server") == {"count": 1}
    with pytest.raises(ExposureError, match="size limit"):
        adapter.save({"count": 1})
    assert adapter.key not in store.session


@pytest.mark.parametrize("value", [0, -1, True, 1.5, 86401])
def test_invalid_ttl_rejected(store, value):
    with pytest.raises(ExposureError, match="lifetime"):
        ServerStateSession(store.session, store.contract, store.binding, max_age=value)
