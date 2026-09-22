"""ADR-038 E2-9: contract versions, server-state lifetime, migration and codec.

Decisions D-i (codec is ``json-primitives-v1`` only) and D-j (old envelopes are
rejected and the view remounts; no translation guesses). The runtime tests use
real mount dispatch over DB sessions; only the staged construction gate is
bypassed. Remount is observed at the destination: the mounted view value and the
mount frame's rendered HTML.
"""

import json
import logging
import time
import uuid
from datetime import datetime
from decimal import Decimal

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, override_settings

from djust import LiveView, event_handler
from djust._exposure import ExposureContract, ExposureError
from djust._exposure_sessions import save_server_state, server_state_adapter
from djust.decorators import state
from djust.runtime import ViewRuntime
from djust.tests.test_runtime_state_save_tt_1894 import MockTransport

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]


class VersionedView(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root><span>{{ count }}</span></div>"
    count = state(0, persist="server")
    hidden = state("SERVER_SENTINEL", persist="server")

    def mount(self, request, **kwargs):
        self.count = 5
        self.public_note = "PUBLIC_SENTINEL"

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)

    @event_handler()
    def increment(self):
        self.count += 1


def make_request(session_key=None):
    request = RequestFactory().get("/schema-versions/")
    request.user = AnonymousUser()
    request.tenant = None
    request.session = SessionStore(session_key)
    if session_key is None:
        request.session.create()
    return request


async def mount(request):
    transport = MockTransport()
    transport.build_request = lambda: request

    async def fresh_event_request(view):
        return await sync_to_async(make_request)(request.session.session_key)

    transport.explicit_event_request = fresh_event_request
    runtime = ViewRuntime(transport)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=["djust"]):
        await runtime.dispatch_mount(
            {"type": "mount", "view": __name__ + ".VersionedView", "url": request.path}
        )
    assert not transport.errors, transport.errors
    frame = next(frame for frame in transport.sent if frame.get("type") == "mount")
    return runtime, transport, frame


async def saved_six():
    """Mount, increment once and persist ``count == 6`` under the current version."""
    request = await sync_to_async(make_request)()
    runtime, _, _ = await mount(request)
    await runtime.dispatch_event({"type": "event", "event": "increment", "params": {}})
    assert runtime.view_instance.count == 6
    return request.session.session_key


async def remount(session_key):
    fresh = await sync_to_async(make_request)(session_key)
    return await mount(fresh)


def raw_session(session_key):
    return SessionStore(session_key).load()


@pytest.fixture
def staged(monkeypatch):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)


# ---- (a) class-level contract version (D-j) ---------------------------------


async def test_unchanged_version_restores_the_envelope_control(staged):
    key = await saved_six()
    runtime, transport, frame = await remount(key)
    assert runtime.view_instance.count == 6
    assert ">6<" in frame["html"]


async def test_bumped_contract_version_rejects_old_envelope_and_remounts(staged, monkeypatch):
    key = await saved_six()
    monkeypatch.setattr(VersionedView, "exposure_schema_version", 2, raising=False)
    runtime, transport, frame = await remount(key)
    assert runtime.view_instance.count == 5
    assert ">5<" in frame["html"]
    assert ">6<" not in json.dumps(transport.sent)


def test_contract_version_is_read_from_the_view_class(staged, monkeypatch):
    assert ExposureContract.from_view_class(VersionedView).version == 1
    before = ExposureContract.from_view_class(VersionedView).schema
    monkeypatch.setattr(VersionedView, "exposure_schema_version", 3, raising=False)
    contract = ExposureContract.from_view_class(VersionedView)
    assert contract.version == 3
    assert contract.schema != before


@pytest.mark.parametrize("value", [0, -1, True, "2", 2.0, None, 2**31])
def test_invalid_contract_version_is_rejected(staged, monkeypatch, value):
    monkeypatch.setattr(VersionedView, "exposure_schema_version", value, raising=False)
    with pytest.raises(ExposureError, match="exposure_schema_version"):
        ExposureContract.from_view_class(VersionedView)


def test_contract_version_property_is_not_evaluated(staged, monkeypatch):
    def forbidden(self):
        raise AssertionError("version descriptors must not be evaluated")

    monkeypatch.setattr(
        VersionedView, "exposure_schema_version", property(forbidden), raising=False
    )
    with pytest.raises(ExposureError, match="exposure_schema_version"):
        ExposureContract.from_view_class(VersionedView)


# ---- (b) DJUST_SERVER_STATE_MAX_AGE ------------------------------------------


@pytest.mark.parametrize("elapsed,restored", [(29, 6), (31, 5)])
async def test_server_state_max_age_setting_is_honored(staged, monkeypatch, elapsed, restored):
    real = time.time
    base = int(real())
    monkeypatch.setattr("djust._exposure_sessions.time.time", lambda: base)
    with override_settings(DJUST_SERVER_STATE_MAX_AGE=30):
        key = await saved_six()
        stored = await sync_to_async(raw_session)(key)
        envelope = next(v for k, v in stored.items() if k.startswith("_djust_explicit_"))
        assert envelope["expires"] - envelope["created"] == 30
        monkeypatch.setattr("djust._exposure_sessions.time.time", lambda: base + elapsed)
        runtime, _, frame = await remount(key)
    assert runtime.view_instance.count == restored
    assert f">{restored}<" in frame["html"]


async def test_default_server_state_lifetime_is_unchanged(staged):
    key = await saved_six()
    stored = await sync_to_async(raw_session)(key)
    envelope = next(v for k, v in stored.items() if k.startswith("_djust_explicit_"))
    assert envelope["expires"] - envelope["created"] == 3600


@pytest.mark.parametrize("value", [0, -5, 86401, "60", 60.0, True, None])
def test_invalid_max_age_setting_fails_closed(staged, value):
    request = make_request()
    with override_settings(DJUST_SERVER_STATE_MAX_AGE=value):
        with pytest.raises(ExposureError, match="DJUST_SERVER_STATE_MAX_AGE"):
            server_state_adapter(object.__new__(VersionedView), request)


@pytest.mark.parametrize(
    "value,flagged",
    [
        (0, True),
        (86401, True),
        ("60", True),
        (True, True),
        (None, True),
        (1, False),
        (86400, False),
    ],
)
def test_max_age_system_check(value, flagged):
    from djust.checks.configuration import _check_server_state_max_age

    errors = []
    with override_settings(DJUST_SERVER_STATE_MAX_AGE=value):
        _check_server_state_max_age(errors)
    assert [e.id for e in errors] == (["djust.C018"] if flagged else [])


def test_max_age_system_check_silent_when_unset():
    from django.conf import settings

    from djust.checks.configuration import _check_server_state_max_age

    assert not hasattr(settings, "DJUST_SERVER_STATE_MAX_AGE")
    errors = []
    _check_server_state_max_age(errors)
    assert errors == []


# ---- (c) migrate_state hook ---------------------------------------------------


async def test_migration_hook_translates_v1_envelope_to_v2(staged, monkeypatch):
    key = await saved_six()
    calls = []

    def migrate_state(self, old_schema, values):
        calls.append((old_schema, dict(values)))
        return {"count": values["count"] * 10, "hidden": values["hidden"]}

    monkeypatch.setattr(VersionedView, "exposure_schema_version", 2, raising=False)
    monkeypatch.setattr(VersionedView, "migrate_state", migrate_state, raising=False)
    runtime, _, frame = await remount(key)
    assert calls == [(1, {"count": 6, "hidden": "SERVER_SENTINEL"})]
    assert runtime.view_instance.count == 60
    assert ">60<" in frame["html"]
    # The next save is written under the current version and restores directly.
    await runtime.dispatch_event({"type": "event", "event": "increment", "params": {}})
    calls.clear()
    second, _, _ = await remount(key)
    assert second.view_instance.count == 61
    assert calls == []


async def test_migration_hook_not_called_for_current_version(staged, monkeypatch):
    key = await saved_six()

    def migrate_state(self, old_schema, values):
        raise AssertionError("current envelopes must not be migrated")

    monkeypatch.setattr(VersionedView, "migrate_state", migrate_state, raising=False)
    runtime, _, _ = await remount(key)
    assert runtime.view_instance.count == 6


@pytest.mark.parametrize(
    "result",
    [
        {"count": 60, "hidden": "SERVER_SENTINEL", "public_note": "MIGRATED_EXTRA_SENTINEL"},
        {"count": 60},
        {"count": Decimal("60"), "hidden": "SERVER_SENTINEL"},
        ["MIGRATED_EXTRA_SENTINEL"],
        None,
    ],
)
async def test_migration_output_is_validated_like_a_fresh_envelope(staged, monkeypatch, result):
    key = await saved_six()
    monkeypatch.setattr(VersionedView, "exposure_schema_version", 2, raising=False)
    monkeypatch.setattr(
        VersionedView, "migrate_state", lambda self, old, values: result, raising=False
    )
    runtime, transport, frame = await remount(key)
    assert runtime.view_instance.count == 5
    assert runtime.view_instance.public_note == "PUBLIC_SENTINEL"
    assert ">5<" in frame["html"]
    assert "MIGRATED_EXTRA_SENTINEL" not in json.dumps(transport.sent)


async def test_raising_migration_hook_remounts_with_value_free_log(staged, monkeypatch, caplog):
    key = await saved_six()

    def migrate_state(self, old_schema, values):
        raise RuntimeError("MIGRATION_SECRET_SENTINEL " + values["hidden"])

    monkeypatch.setattr(VersionedView, "exposure_schema_version", 2, raising=False)
    monkeypatch.setattr(VersionedView, "migrate_state", migrate_state, raising=False)
    with caplog.at_level(logging.DEBUG):
        runtime, transport, frame = await remount(key)
    assert runtime.view_instance.count == 5
    assert ">5<" in frame["html"]
    records = [r for r in caplog.records if "migrate_state" in r.getMessage()]
    assert records, caplog.text  # the failure is reported, not silent
    assert all(r.exc_info is None for r in records)
    assert "MIGRATION_SECRET_SENTINEL" not in caplog.text
    assert "SERVER_SENTINEL" not in caplog.text
    assert "SENTINEL" not in json.dumps(transport.sent)


async def test_downgraded_version_is_not_migrated(staged, monkeypatch):
    monkeypatch.setattr(VersionedView, "exposure_schema_version", 3, raising=False)
    key = await saved_six()
    calls = []
    monkeypatch.setattr(VersionedView, "exposure_schema_version", 2, raising=False)
    monkeypatch.setattr(
        VersionedView,
        "migrate_state",
        lambda self, old, values: calls.append(old) or dict(values),
        raising=False,
    )
    runtime, _, _ = await remount(key)
    assert runtime.view_instance.count == 5
    assert calls == []


async def test_unindexed_prototype_envelope_is_rejected(staged, monkeypatch):
    """A stored envelope without the contract version (v1 format) remounts."""
    key = await saved_six()
    calls = []
    monkeypatch.setattr(
        VersionedView,
        "migrate_state",
        lambda self, old, values: calls.append(old) or dict(values),
        raising=False,
    )

    def strip():
        session = SessionStore(key)
        name = next(k for k in session.keys() if k.startswith("_djust_explicit_"))
        envelope = session[name]
        envelope.pop("schema_version", None)
        envelope["version"] = 1
        session[name] = envelope
        session.save()

    await sync_to_async(strip)()
    runtime, _, _ = await remount(key)
    assert runtime.view_instance.count == 5
    assert calls == []


# ---- (d) D-i: json-primitives-v1 only ------------------------------------------


class Reprish:
    def __repr__(self):
        return "REPR_CODEC_SENTINEL"

    __str__ = __repr__


def _values():
    from django.contrib.auth.models import User

    return [
        Decimal("1.5"),
        datetime(2026, 1, 2, 3, 4, 5),
        uuid.UUID("12345678-1234-5678-1234-567812345678"),
        User(username="CODEC_MODEL_SENTINEL", password="CODEC_PASSWORD_SENTINEL"),
        Reprish(),
        (1, 2),
    ]


@pytest.mark.parametrize("index", range(6))
def test_non_primitive_server_values_are_rejected_not_stringified(staged, index):
    value = _values()[index]
    request = make_request()
    view = object.__new__(VersionedView)
    view.count = value
    with pytest.raises(ExposureError):
        save_server_state(view, request)
    stored = json.dumps(raw_session(request.session.session_key), default=str)
    assert not any(
        k.startswith("_djust_explicit_") for k in raw_session(request.session.session_key)
    )
    for marker in ("1.5", "2026-01-02", "12345678", "CODEC_", "REPR_CODEC_SENTINEL"):
        assert marker not in stored


def test_primitive_server_values_are_saved_control(staged):
    request = make_request()
    view = object.__new__(VersionedView)
    view.count = 7
    save_server_state(view, request)
    assert any(k.startswith("_djust_explicit_") for k in raw_session(request.session.session_key))


class SnapshotCodecView(LiveView):
    exposure_policy = "explicit"
    saved = state(0, persist="client", client=True)


@pytest.mark.parametrize("index", range(6))
def test_non_primitive_snapshot_values_are_rejected_not_stringified(staged, index):
    from djust._exposure_snapshots import snapshot_codec

    request = make_request()
    view = object.__new__(SnapshotCodecView)
    codec = snapshot_codec(view, request)
    view.saved = 3
    assert codec.restore(codec.capture(view)) == {"saved": 3}  # control
    view.saved = _values()[index]
    with pytest.raises(ExposureError):
        codec.capture(view)


def test_codec_identifier_is_json_primitives_v1_only():
    from djust import _exposure

    assert _exposure._CODEC_VERSION == "json-primitives-v1"
