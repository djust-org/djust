"""ADR-038 E1: presence hooks are application code.

``track_presence`` and ``untrack_presence`` call ``handle_presence_join`` /
``handle_presence_leave`` and logged their failures with ``logger.exception``
for any policy. Driven directly on a view: the hooks run in the presence mixin,
outside any runtime turn, so the view is the only owner.
"""

import logging

import pytest
from django.contrib.auth.models import AnonymousUser

from djust import LiveView
from djust.presence import PresenceMixin

CALLS = []


class PresenceHookView(PresenceMixin, LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root></div>"
    presence_key = "exposure-presence-room"

    def handle_presence_join(self, data):
        CALLS.append("join")
        raise ValueError("PRESENCE_JOIN_SENTINEL")

    def handle_presence_leave(self, data):
        CALLS.append("leave")
        raise ValueError("PRESENCE_LEAVE_SENTINEL")


@pytest.mark.django_db
@pytest.mark.parametrize("policy", ["legacy", "explicit"])
def test_presence_hook_failures_are_value_free_for_explicit_views(monkeypatch, rf, caplog, policy):
    CALLS.clear()
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    monkeypatch.setattr(PresenceHookView, "exposure_policy", policy)
    view = PresenceHookView()
    view._websocket_session_id = "exposure-presence-session"
    view.request = rf.get("/")
    view.request.user = AnonymousUser()
    with caplog.at_level(logging.DEBUG):
        view.track_presence()
        view.untrack_presence()
    assert CALLS == ["join", "leave"], "a presence hook never ran; the test would be vacuous"
    if policy == "legacy":
        assert "Error in handle_presence_join: PRESENCE_JOIN_SENTINEL" in caplog.text
        assert "Error in handle_presence_leave: PRESENCE_LEAVE_SENTINEL" in caplog.text
    else:
        assert "PRESENCE_JOIN_SENTINEL" not in caplog.text
        assert "PRESENCE_LEAVE_SENTINEL" not in caplog.text
        assert caplog.text.count("Protected view operation failed") == 2
