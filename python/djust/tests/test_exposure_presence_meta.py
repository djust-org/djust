"""ADR-038 D-c: presence metadata is application output.

``track_presence(meta)`` stores ``meta`` in the presence backend, where peers
read it through ``list_presences()``, and ``update_cursor_position`` rebroadcasts
it to the presence group on every ``cursor_move``. The meta the application
passes is its own output (D6). What the framework added on its own was the
authenticated user's ``username`` (as ``name``) and ``user_id``. For nonlegacy
views it no longer adds them; legacy views are unchanged.

Asserted at the destinations peers read: the backend's presence records and the
message a subscribed peer channel receives from a real in-memory channel layer.
"""

from types import SimpleNamespace

import pytest
from asgiref.sync import async_to_sync
from channels.layers import InMemoryChannelLayer
from django.test import RequestFactory

from djust import LiveView, presence
from djust.presence import LiveCursorMixin, PresenceManager

USERNAME = "PRESENCE_USERNAME_SENTINEL"
USER_PK = 918273645


class CursorView(LiveCursorMixin, LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root></div>"


@pytest.mark.parametrize("policy", ["legacy", "explicit"])
def test_presence_meta_carries_only_application_meta_for_explicit_views(monkeypatch, policy):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    monkeypatch.setattr(CursorView, "exposure_policy", policy)
    key = "adr038-presence-meta-" + policy
    monkeypatch.setattr(CursorView, "presence_key", key)
    layer = InMemoryChannelLayer()
    monkeypatch.setattr(presence, "get_channel_layer", lambda: layer)
    group = PresenceManager.presence_group_name(key)
    peer = "peer.adr038!" + policy
    async_to_sync(layer.group_add)(group, peer)

    view = CursorView()
    view._websocket_session_id = "adr038-presence-session"
    view.request = RequestFactory().get("/")
    view.request.user = SimpleNamespace(is_authenticated=True, id=USER_PK, username=USERNAME)
    view.request.session = SimpleNamespace(session_key="s")
    try:
        view.track_presence(meta={"color": "APP_COLOR"})
        records = PresenceManager.list_presences(key)
        view.update_cursor_position(3, 4)
        messages = []
        while True:
            try:
                messages.append(async_to_sync(layer.receive)(peer))
            except Exception:
                break
            if not layer.channels.get(peer):
                break
    finally:
        view.untrack_presence()

    cursor = [m for m in messages if m.get("event") == "cursor_move"]
    assert len(records) == 1 and cursor, "presence never tracked or broadcast; vacuous"
    record_meta = records[0]["meta"]
    cursor_meta = cursor[0]["payload"]["meta"]
    # Application-passed meta always reaches peers (D6 application output).
    assert record_meta["color"] == cursor_meta["color"] == "APP_COLOR"
    if policy == "legacy":
        assert record_meta["name"] == cursor_meta["name"] == USERNAME
        assert record_meta["user_id"] == cursor_meta["user_id"] == str(USER_PK)
    else:
        assert record_meta == cursor_meta == {"color": "APP_COLOR"}
        assert USERNAME not in repr(records) + repr(messages)
