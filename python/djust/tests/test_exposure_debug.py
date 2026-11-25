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


class DebugView(LiveView):
    exposure_policy = "explicit"
    server_value = state("SERVER_SENTINEL", persist="server")
    client_value = state(default_factory=lambda: [1], client=True)
    transient_value = state("TRANSIENT_SENTINEL")

    @property
    def unrelated_property(self):
        raise AssertionError("Debug output must not evaluate unrelated properties")


@pytest.fixture
def rf():
    """Requests carry the observability token: these tests are about what the
    endpoints emit, not the access check (b5ed46f2a)."""
    from .conftest import observability_request_factory

    return observability_request_factory()


@pytest.fixture
def view():
    result = object.__new__(DebugView)
    result.public_note = "UNDECLARED_PUBLIC_SENTINEL"
    result._private_note = "PRIVATE_SENTINEL"
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


@pytest.mark.parametrize("policy", ["explict", None, True])
def test_unknown_policy_never_selects_legacy_debug_reflection(view, policy):
    view.exposure_policy = policy
    assert _lenient_assigns(view) == {"_djust_projection_error": "State unavailable"}


def test_mount_and_event_debug_metadata_do_not_walk_attributes(view):
    for payload in (view.get_debug_info(), view.get_debug_update()):
        assert set(payload["variables"]) == {"server_value", "client_value", "transient_value"}
        assert payload["variables"]["server_value"]["type"] == "redacted"
        assert payload["state_sizes"]["server_value"] == {"memory": None, "serialized": None}
        assert "SENTINEL" not in json.dumps(payload)
    assert view._debug_state_sizes()["server_value"]["serialized"] is None


@pytest.mark.asyncio
async def test_actual_consumer_debug_payload(view, settings):
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
