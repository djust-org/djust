"""Signed explicit snapshots grant only declared client-persistence fields."""

from dataclasses import replace

import pytest

from djust import LiveView
from djust.decorators import state
from djust._exposure import ExposureContract, ExposureError
from djust._exposure_sessions import StateBinding
from djust._exposure_snapshots import ClientSnapshot


class SnapshotView(LiveView):
    exposure_policy = "explicit"
    server = state("SERVER_SENTINEL", persist="server")
    visible = state("VISIBLE_NOT_PERSISTED", client=True)
    saved = state(default_factory=lambda: [1], persist="client", client=True)
    transient = state("TRANSIENT_SENTINEL")


@pytest.fixture
def codec():
    return ClientSnapshot(
        ExposureContract.from_view_class(SnapshotView),
        StateBinding("session", "user", "tenant", "/route/"),
        max_age=60,
    )


def test_only_snapshot_fields_are_signed_and_restored(codec):
    view = object.__new__(SnapshotView)
    view.public_note = "PUBLIC_SENTINEL"
    token = codec.capture(view)
    assert "SENTINEL" not in token
    assert "VISIBLE_NOT_PERSISTED" not in token
    view.saved.append(2)
    assert codec.restore(token) == {"saved": [1]}
    assert "_state_server" not in view.__dict__
    assert "_state_visible" not in view.__dict__


@pytest.mark.parametrize("part", ["session", "user", "tenant", "view"])
def test_identity_changes_reject_replay(codec, part):
    token = codec.capture(object.__new__(SnapshotView))
    other = ClientSnapshot(codec.contract, replace(codec.binding, **{part: "different"}))
    assert other.restore(token) is None


@pytest.mark.parametrize("elapsed", [-1, 60, 61])
def test_future_or_expired_envelopes_are_rejected(codec, monkeypatch, elapsed):
    monkeypatch.setattr("djust._exposure_snapshots.time.time", lambda: 1000)
    token = codec.capture(object.__new__(SnapshotView))
    monkeypatch.setattr("djust._exposure_snapshots.time.time", lambda: 1000 + elapsed)
    assert codec.restore(token) is None


@pytest.mark.parametrize("token", [None, {}, "unsigned", "x" * 65537])
def test_invalid_wire_input_is_rejected(codec, token):
    assert codec.restore(token) is None


def test_schema_change_and_tampering_are_rejected(codec):
    token = codec.capture(object.__new__(SnapshotView))
    changed = ClientSnapshot(
        ExposureContract.from_view_class(SnapshotView, version=2), codec.binding
    )
    assert changed.restore(token) is None
    assert codec.restore(token + "x") is None


def test_permitted_value_failure_never_uses_repr(codec):
    class Secret:
        def __repr__(self):
            raise AssertionError("repr must not run")

    view = object.__new__(SnapshotView)
    view.saved = Secret()
    with pytest.raises(ExposureError):
        codec.capture(view)


@pytest.mark.parametrize(
    "mutation", ["extra", "destination", "version", "timestamp", "binding", "nan"]
)
def test_signed_but_invalid_envelope_is_rejected(codec, mutation):
    import json
    from django.core.signing import TimestampSigner
    from djust._exposure_snapshots import _SALT

    signer = TimestampSigner(salt=_SALT)
    raw = json.loads(signer.unsign(codec.capture(object.__new__(SnapshotView))))
    if mutation == "extra":
        raw["state"]["values"]["server"] = "SERVER_SENTINEL"
    elif mutation == "destination":
        raw["state"]["destination"] = "server"
    elif mutation == "version":
        raw["version"] = True
    elif mutation == "timestamp":
        raw["created"] = True
    elif mutation == "binding":
        raw["binding"] = "different"
    else:
        raw["state"]["values"]["saved"] = float("nan")
    assert codec.restore(signer.sign(json.dumps(raw))) is None


def test_signature_overhead_counts_toward_wire_limit(codec):
    from djust._exposure import StateLimits
    from django.core.signing import TimestampSigner
    from djust._exposure_snapshots import _SALT

    token = codec.capture(object.__new__(SnapshotView))
    payload_bytes = len(TimestampSigner(salt=_SALT).unsign(token).encode("utf-8"))
    assert len(token.encode("utf-8")) > payload_bytes
    limited = ClientSnapshot(
        replace(codec.contract, limits=StateLimits(max_bytes=payload_bytes)), codec.binding
    )
    with pytest.raises(ExposureError):
        limited.capture(object.__new__(SnapshotView))


def test_legacy_signature_cannot_be_replayed_as_explicit(codec):
    from djust.security import sign_snapshot

    assert (
        codec.restore(sign_snapshot('{"saved":[90]}', codec.contract.owner, codec.binding.session))
        is None
    )
