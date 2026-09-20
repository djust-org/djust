"""ADR-038 debug destinations; construction remains gated in production.

Fixtures deliberately bypass LiveView.__init__ to exercise the real debug sinks
before explicit mode is enabled. These are not end-to-end explicit mount tests.
"""

import json

import pytest

from djust import LiveView
from djust.decorators import state
from djust.observability.registry import register_view, unregister_view
from djust.observability.views import _lenient_assigns, view_assigns
from djust.time_travel import (
    TimeTravelBuffer,
    record_event_end,
    record_event_start,
    restore_snapshot,
)


class DebugView(LiveView):
    exposure_policy = "explicit"
    time_travel_enabled = True
    server_value = state("SERVER_SENTINEL", persist="server")
    client_value = state(default_factory=lambda: [1], client=True)
    transient_value = state("TRANSIENT_SENTINEL")

    @property
    def unrelated_property(self):
        raise AssertionError("Debug output must not evaluate unrelated properties")


@pytest.fixture
def view():
    result = object.__new__(DebugView)
    result.public_note = "UNDECLARED_PUBLIC_SENTINEL"
    result._private_note = "PRIVATE_SENTINEL"
    result._time_travel_buffer = TimeTravelBuffer()
    return result


def test_observability_endpoint_only_emits_the_debug_projection(view, rf, settings):
    settings.DEBUG = True
    register_view("exposure-debug-test", view)
    try:
        response = view_assigns(rf.get("/debug/", {"session_id": "exposure-debug-test"}))
    finally:
        unregister_view("exposure-debug-test")
    assert response.status_code == 200
    assert json.loads(response.content)["assigns"] == {
        "server_value": "[redacted]",
        "client_value": [1],
        "transient_value": "[redacted]",
    }
    assert b"SENTINEL" not in response.content
    assert "_state_server_value" not in view.__dict__
    assert "_state_transient_value" not in view.__dict__


def test_bug_capture_refuses_legacy_history_after_policy_change(view, settings):
    from djust._exposure import ExposureError
    from djust.bug_capture import encode_view_state
    from djust.time_travel import EventSnapshot

    settings.DEBUG = True
    view._time_travel_buffer.append(
        EventSnapshot(
            event_name="old",
            params={},
            ref=None,
            ts=0,
            state_before={"old_secret": "HISTORICAL_SENTINEL"},
            state_after={"old_secret": "HISTORICAL_SENTINEL"},
        )
    )
    with pytest.raises(ExposureError, match="historical"):
        encode_view_state(view, [])


def test_bug_capture_reprojects_history_without_reading_current_state(view, settings):
    from djust.bug_capture import BugCapture, encode_view_state

    settings.DEBUG = True
    snapshot = record_event_start(view, "change", {}, 1)
    view.client_value = [2]
    record_event_end(view, snapshot)
    snapshot.state_before["extra"] = "HISTORICAL_SENTINEL"
    snapshot.state_after["server_value"] = "SERVER_SENTINEL"
    view.client_value = [77]
    capture = BugCapture.decode(encode_view_state(view, []))
    assert capture.state_before["client_value"] == [1]
    assert capture.state_after["client_value"] == [2]
    assert capture.state_after["server_value"] == "[redacted]"
    assert "SENTINEL" not in json.dumps(capture.to_dict())
    assert "_state_server_value" not in view.__dict__
    assert "_state_transient_value" not in view.__dict__


def test_debug_codec_failure_does_not_fall_back_to_repr_or_raw_attrs(view, caplog):
    class Secret:
        def __repr__(self):
            raise AssertionError("repr must not run")

    view.client_value = Secret()
    result = _lenient_assigns(view)
    assert result == {"_djust_projection_error": "State unavailable"}
    assert "SENTINEL" not in caplog.text


def test_client_default_failure_is_not_logged_with_secret_exception_text(caplog):
    def fail():
        raise ValueError("SECRET_FACTORY_SENTINEL")

    class Broken(LiveView):
        exposure_policy = "explicit"
        visible = state(default_factory=fail, client=True)

    assert _lenient_assigns(object.__new__(Broken)) == {
        "_djust_projection_error": "State unavailable"
    }
    assert "SECRET_FACTORY_SENTINEL" not in caplog.text


def test_time_travel_wire_history_redacts_state_params_and_errors(view):
    snapshot = record_event_start(view, "change", {"value": "PARAM_SENTINEL"}, 1)
    assert snapshot is not None
    view.client_value.append(2)
    record_event_end(view, snapshot, "ERROR_SENTINEL")
    history = view._time_travel_buffer.history()
    assert history[0]["state_before"]["client_value"] == [1]
    assert history[0]["state_after"]["client_value"] == [1, 2]
    assert history[0]["state_before"]["server_value"] == "[redacted]"
    assert history[0]["params"] == {"_redacted": True}
    assert history[0]["error"] == "[redacted]"
    assert history[0]["restorable"] is False
    assert "SENTINEL" not in json.dumps(history)


def test_debug_projection_cannot_be_restored_or_delete_internal_attrs(view):
    snapshot = record_event_start(view, "change", {}, 1)
    view.client_value.append(2)
    assert restore_snapshot(view, snapshot) is False
    assert view.client_value == [1, 2]
    assert view.public_note == "UNDECLARED_PUBLIC_SENTINEL"
    assert view.server_value == "SERVER_SENTINEL"
    # A buffered debug projection cannot become restorable after policy changes.
    view.exposure_policy = "legacy"
    assert restore_snapshot(view, snapshot) is False


@pytest.mark.parametrize("policy", ["explict", None, True])
def test_unknown_policy_never_selects_legacy_debug_reflection(view, policy):
    view.exposure_policy = policy
    assert _lenient_assigns(view) == {"_djust_projection_error": "State unavailable"}


def test_legacy_debug_behavior_is_unchanged():
    view = LiveView()
    view.public_note = "legacy"
    assert _lenient_assigns(view)["public_note"] == "legacy"


def test_mount_and_event_debug_metadata_do_not_walk_attributes(view):
    for payload in (view.get_debug_info(), view.get_debug_update()):
        assert set(payload["variables"]) == {"server_value", "client_value", "transient_value"}
        assert payload["variables"]["server_value"]["type"] == "redacted"
        assert payload["state_sizes"]["server_value"] == {"memory": None, "serialized": None}
        assert "SENTINEL" not in json.dumps(payload)
    assert view._debug_state_sizes()["server_value"]["serialized"] is None


@pytest.mark.asyncio
async def test_actual_consumer_debug_and_time_travel_payloads(view, settings):
    from unittest.mock import AsyncMock

    from djust.websocket import LiveViewConsumer

    settings.DEBUG = True
    consumer = LiveViewConsumer()
    consumer.view_instance = view
    consumer.send_json = AsyncMock()
    response = {"type": "noop"}
    consumer._attach_debug_payload(response, "change")
    assert "SENTINEL" not in json.dumps(response)
    assert response["_debug"]["variables"]["server_value"]["type"] == "redacted"
    snapshot = record_event_start(view, "change", {"value": "PARAM_SENTINEL"}, 1)
    record_event_end(view, snapshot, "ERROR_SENTINEL")
    await consumer._maybe_push_tt_event(view, snapshot)
    frame = consumer.send_json.call_args.args[0]
    assert frame["type"] == "time_travel_event"
    assert frame["entry"]["restorable"] is False
    assert "SENTINEL" not in json.dumps(frame)


def test_replay_and_component_restore_refuse_observational_records(view):
    from unittest.mock import Mock

    from djust.decorators import event_handler
    from djust.time_travel import replay_event, restore_component_snapshot

    handler = Mock()

    @event_handler()
    def change(**kwargs):
        handler(**kwargs)

    view.change = change
    snapshot = record_event_start(view, "change", {}, 1)
    assert replay_event(view, snapshot, override_params={}) is None
    handler.assert_not_called()
    assert restore_component_snapshot(view, snapshot, "menu") is False


def test_policy_change_mid_event_cannot_retain_legacy_values():
    view = LiveView()
    view.time_travel_enabled = True
    view._time_travel_buffer = TimeTravelBuffer()
    view.public_note = "LEGACY_SENTINEL"
    snapshot = record_event_start(view, "change", {"value": "PARAM_SENTINEL"}, 1)
    view.exposure_policy = "explicit"
    record_event_end(view, snapshot, "ERROR_SENTINEL")
    assert "SENTINEL" not in json.dumps(view._time_travel_buffer.history())
    assert snapshot.restorable is False


def test_runtime_diagnostic_signal_uses_debug_projection_not_render_context(view, monkeypatch):
    from unittest.mock import Mock

    from djust.runtime import WSConsumerTransport
    from djust.websocket import LiveViewConsumer

    emit = Mock()
    monkeypatch.setattr("djust.websocket._emit_full_html_update", emit)
    transport = WSConsumerTransport(LiveViewConsumer())
    transport.on_render_emitted(
        view,
        reason="no_patches",
        version=1,
        event_name="change",
        context={"render_only": "RENDER_CONTEXT_SENTINEL"},
    )
    snapshot = emit.call_args.kwargs["context_snapshot"]
    assert snapshot["server_value"] == "[redacted]"
    assert "SENTINEL" not in json.dumps(snapshot)


@pytest.mark.parametrize("operation", ["reset", "eval"])
@pytest.mark.parametrize("policy", ["explicit", "misspelled", None])
def test_debug_mutations_refuse_nonlegacy_views_before_invocation(
    view, rf, settings, monkeypatch, operation, policy
):
    from djust import event_handler
    from djust.observability.views import eval_handler, reset_view_state

    settings.DEBUG = True
    monkeypatch.setattr(DebugView, "exposure_policy", policy)
    calls = []

    def mount(self, request, **kwargs):
        calls.append("mount")

    @event_handler()
    def mutate(self, **kwargs):
        calls.append("handler")
        return "RETURN_SECRET_SENTINEL"

    monkeypatch.setattr(DebugView, "mount", mount)
    monkeypatch.setattr(DebugView, "mutate", mutate, raising=False)
    view._djust_mount_request = object()
    view._djust_mount_kwargs = {}
    before = dict(view.__dict__)
    register_view("explicit-mutation", view)
    try:
        request = rf.post(
            "/debug/?session_id=explicit-mutation",
            {"handler_name": "mutate", "params": {"secret": "PARAM_SENTINEL"}},
            content_type="application/json",
        )
        response = (reset_view_state if operation == "reset" else eval_handler)(request)
    finally:
        unregister_view("explicit-mutation")
    assert response.status_code == 409
    assert calls == []
    assert view.__dict__ == before
    assert b"SENTINEL" not in response.content
