"""Time-travel debugging was removed in 2.0 (#4184): what a project still using it sees.

Owner decision (2026-11-15): time travel is dropped, not ported. The recording,
the jump / component-jump / forward-replay frames, the debug panel's Time Travel
tab (with the bug-capture Share button that read its buffer), the
``reset_view_state`` / ``eval_handler`` debug endpoints and their MCP tools are
gone. Nothing that turned it on may be ignored without a word:

* the view attribute is refused when the class is created, naming the removal and
  the replacement (the debug panel's State tab);
* the ``LIVEVIEW_CONFIG`` keys are refused at startup;
* the old imports name the removal instead of failing with a bare
  "cannot import name";
* an old client that still sends a removed frame gets the standard
  unknown-message refusal, and the connection keeps working.

The replacement is pinned too: under ``DEBUG`` every event's response carries the
``_debug`` payload (the event name and the view's debug projection after it),
which is what the debug panel's State tab lists, event by event.
"""

from __future__ import annotations

import pytest
from asgiref.sync import sync_to_async
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from djust import LiveView, event_handler
from djust.decorators import state

from ._ws_frames import drain_extra, has_type, receive_until
from .test_exposure_runtime import make_request

REMOVED_FRAMES = (
    {"type": "time_travel_jump", "index": 0, "which": "before"},
    {"type": "time_travel_component_jump", "index": 0, "component_id": "x", "which": "before"},
    {"type": "forward_replay", "from_index": 0},
    {"type": "bug_capture_share"},
)


class CounterView(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>count={{ count }}</div>"
    count = state(0, persist="server")

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)

    @event_handler()
    def bump(self) -> None:
        self.count += 1


# --------------------------------------------------------------------------
# The view attributes
# --------------------------------------------------------------------------


def test_time_travel_enabled_is_refused_when_the_class_is_created():
    with pytest.raises(ImproperlyConfigured) as refused:

        class StillTimeTravelling(LiveView):
            exposure_policy = "explicit"
            template = "<div dj-root></div>"
            time_travel_enabled = True

    message = str(refused.value)
    assert "StillTimeTravelling" in message
    assert "time_travel_enabled" in message
    assert "removed in djust 2.0" in message
    assert "delete the attribute" in message
    # The replacement, not just the refusal.
    assert "State tab" in message


def test_time_travel_excluded_fields_is_refused_when_the_class_is_created():
    with pytest.raises(ImproperlyConfigured) as refused:

        class StillScrubbing(LiveView):
            exposure_policy = "explicit"
            template = "<div dj-root></div>"
            time_travel_excluded_fields = ["password"]

    message = str(refused.value)
    assert "time_travel_excluded_fields" in message
    assert "bug_capture_default_scrub" in message


def test_the_attributes_turned_off_are_accepted_and_the_base_class_has_neither():
    class Off(LiveView):
        exposure_policy = "explicit"
        template = "<div dj-root></div>"
        time_travel_enabled = False
        time_travel_excluded_fields: list = []

    assert Off() is not None
    for name in ("time_travel_enabled", "time_travel_excluded_fields"):
        assert not hasattr(LiveView, name), name
    view = CounterView()
    for name in ("_time_travel_buffer", "_time_travel_branch_id", "_capture_snapshot_state"):
        assert not hasattr(view, name), name


# --------------------------------------------------------------------------
# The configuration keys
# --------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["time_travel_enabled", "time_travel_max_events"])
@pytest.mark.parametrize("setting", ["LIVEVIEW_CONFIG", "DJUST_CONFIG"])
def test_a_removed_config_key_is_refused_at_startup(key, setting):
    from djust._removed_settings import reject_removed_settings

    with override_settings(**{setting: {key: True}}):
        with pytest.raises(ImproperlyConfigured) as refused:
            reject_removed_settings()
    message = str(refused.value)
    assert "%s[%r] was removed in djust 2.0" % (setting, key) in message
    assert "delete the key" in message


def test_the_config_defaults_no_longer_carry_the_keys():
    from djust.config import config

    for key in ("time_travel_enabled", "time_travel_max_events"):
        assert config.get(key) is None, key
    # Kept: the State tab's Share applies it (#4184).
    assert config.get("bug_capture_default_scrub") == []


# --------------------------------------------------------------------------
# Imports, checks, endpoints
# --------------------------------------------------------------------------


def test_the_module_is_gone_and_the_package_import_names_the_replacement():
    with pytest.raises(ModuleNotFoundError):
        import djust.time_travel  # noqa: F401

    with pytest.raises(ImportError) as refused:
        from djust import time_travel  # noqa: F401
    message = str(refused.value)
    assert "removed in djust 2.0" in message
    assert "State tab" in message


def test_the_time_travel_checks_are_gone():
    import djust.checks

    for name in ("check_time_travel_debugging", "check_time_travel_pii_fields"):
        assert not hasattr(djust.checks, name), name


def test_the_debug_state_endpoints_and_their_mcp_tools_are_gone():
    import inspect

    from djust.mcp import server
    from djust.observability import urls, views

    names = {pattern.name for pattern in urls.urlpatterns}
    assert "view_assigns" in names  # the read-only endpoints stay
    assert not names & {"reset_view_state", "eval_handler"}
    assert not hasattr(views, "reset_view_state")
    assert not hasattr(views, "eval_handler")
    source = inspect.getsource(server)
    assert "def get_view_assigns(" in source
    assert "def eval_handler(" not in source
    assert "def reset_view_state(" not in source


# --------------------------------------------------------------------------
# An old client over a real WebSocket
# --------------------------------------------------------------------------


async def _connect_and_mount():
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    request = await sync_to_async(make_request)()
    socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    socket.scope.update(session=request.session, user=request.user, tenant=None)
    assert (await socket.connect())[0]
    await socket.receive_json_from()  # the connect frame
    await socket.send_json_to(
        {"type": "mount", "view": __name__ + ".CounterView", "url": "/counter/"}
    )
    frames = await receive_until(socket, has_type("mount", "error"), what="the mount frame")
    assert frames[-1]["type"] == "mount", frames
    return socket


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("frame", REMOVED_FRAMES, ids=lambda f: f["type"])
async def test_a_removed_frame_gets_the_unknown_message_refusal_and_the_socket_lives(frame):
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=True, DJUST_TENANTS=None, DJUST_CONFIG={}
    ):
        socket = await _connect_and_mount()
        try:
            await socket.send_json_to(frame)
            refusal = await socket.receive_json_from()
            assert refusal["type"] == "error", refusal
            assert refusal["error"] == "Unknown message type: %s" % frame["type"], refusal
            # Not a crash: the same connection still runs an event.
            await socket.send_json_to({"type": "event", "event": "bump", "params": {}, "ref": 1})
            frames = await receive_until(socket, has_type("patch", "html_update"), what="render")
            assert "count=1" in str(frames) or any(f.get("patches") for f in frames), frames
        finally:
            await socket.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_event_under_debug_carries_the_state_tab_payload_and_no_time_travel_frame():
    """The replacement: the ``_debug`` payload the State tab lists, per event."""
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=True, DJUST_TENANTS=None, DJUST_CONFIG={}
    ):
        socket = await _connect_and_mount()
        try:
            await socket.send_json_to({"type": "event", "event": "bump", "params": {}, "ref": 1})
            frames = await receive_until(socket, has_type("patch", "html_update"), what="render")
            frames += await drain_extra(socket)
        finally:
            await socket.disconnect()
    assert not [f for f in frames if str(f.get("type", "")).startswith("time_travel")], frames
    render = next(f for f in frames if f.get("type") in ("patch", "html_update"))
    debug = render.get("_debug")
    assert debug is not None, render
    assert debug["_eventName"] == "bump"
    assert "count" in debug["variables"], debug
