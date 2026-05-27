"""
Reproducers for PresenceMixin DX issues #1611, #1612, #1613, #1614.

Each test fails on main and passes once the corresponding fix lands.
Mirrors the FakeView pattern from tests/test_presence.py and
tests/test_realtime_multiuser.py.
"""

from unittest.mock import patch

import pytest

from djust.backends.memory import InMemoryPresenceBackend
from djust.backends.registry import reset_presence_backend, set_presence_backend
from djust.presence import PRESENCE_TIMEOUT, PresenceMixin


class FakeUser:
    def __init__(self, username="alice", user_id=1):
        self.username = username
        self.id = user_id
        self.is_authenticated = True


class FakeAnonymousUser:
    is_authenticated = False


class FakeSession:
    def __init__(self, key="sess"):
        self.session_key = key


class FakeRequest:
    def __init__(self, user=None, session_key="sess"):
        self.user = user or FakeUser()
        self.session = FakeSession(session_key)


class FakeView:
    def __init__(self, user=None, session_key="sess", ws_session_id=None):
        if hasattr(super(), "__init__"):
            super().__init__()
        self.request = FakeRequest(user=user, session_key=session_key)
        if ws_session_id is not None:
            self._websocket_session_id = ws_session_id


@pytest.fixture(autouse=True)
def fresh_presence_backend():
    backend = InMemoryPresenceBackend(timeout=PRESENCE_TIMEOUT)
    set_presence_backend(backend)
    yield backend
    reset_presence_backend()


# ---------------------------------------------------------------------------
# #1611 — online_count is auto-set as an instance attribute
# ---------------------------------------------------------------------------
class TestIssue1611OnlineCountAttribute:
    def test_track_presence_sets_online_count_instance_attribute(self):
        """track_presence() must set self.online_count = 1 (instance attr)."""

        class V(FakeView, PresenceMixin):
            presence_key = "demo_1611"

        v = V(ws_session_id="wsA")
        v.track_presence()

        assert hasattr(v, "online_count"), "online_count not set as instance attribute"
        assert v.online_count == 1

    def test_untrack_presence_refreshes_online_count(self):
        """untrack_presence() must recompute self.online_count after leave."""

        class V(FakeView, PresenceMixin):
            presence_key = "demo_1611b"

        v = V(ws_session_id="wsB")
        v.track_presence()
        assert v.online_count == 1
        v.untrack_presence()
        assert v.online_count == 0


# ---------------------------------------------------------------------------
# #1612 — track_presence() no-ops when _websocket_session_id is absent
# ---------------------------------------------------------------------------
class TestIssue1612HttpMountNoOp:
    def test_track_presence_skipped_in_http_mount_context(self):
        """No _websocket_session_id => track_presence does NOT call the backend."""

        class V(FakeView, PresenceMixin):
            presence_key = "demo_1612"

        v = V()  # no ws_session_id
        with patch("djust.presence.PresenceManager.join_presence") as mock_join:
            v.track_presence()
            mock_join.assert_not_called()
        assert v._presence_tracked is False

    def test_track_presence_proceeds_when_ws_session_present(self):
        """WS-mount context => track_presence proceeds normally."""

        class V(FakeView, PresenceMixin):
            presence_key = "demo_1612b"

        v = V(ws_session_id="wsC")
        v.track_presence()
        assert v._presence_tracked is True
        assert v.presence_count() == 1


# ---------------------------------------------------------------------------
# #1613 — presence_unique_per_connection flag for anonymous tabs
# ---------------------------------------------------------------------------
class TestIssue1613PerConnectionUniqueness:
    def test_anonymous_tabs_collide_by_default(self):
        """Default behavior: two anonymous tabs of same session collapse to 1 id."""

        class V(FakeView, PresenceMixin):
            presence_key = "demo_1613a"

        anon = FakeAnonymousUser()
        v1 = V(user=anon, session_key="anon_session", ws_session_id="wsA")
        v2 = V(user=anon, session_key="anon_session", ws_session_id="wsB")
        assert v1.get_presence_user_id() == v2.get_presence_user_id()

    def test_anonymous_tabs_distinct_when_flag_set(self):
        """presence_unique_per_connection=True => anonymous tabs get distinct ids."""

        class V(FakeView, PresenceMixin):
            presence_key = "demo_1613b"
            presence_unique_per_connection = True

        anon = FakeAnonymousUser()
        v1 = V(user=anon, session_key="anon_session", ws_session_id="wsA")
        v2 = V(user=anon, session_key="anon_session", ws_session_id="wsB")
        id1 = v1.get_presence_user_id()
        id2 = v2.get_presence_user_id()
        assert id1 != id2
        assert id1.startswith("anon_conn_")
        assert id2.startswith("anon_conn_")

    def test_authenticated_user_unaffected_by_flag(self):
        """Flag=True with an authed user still returns user.id (cross-tab collapse)."""

        class V(FakeView, PresenceMixin):
            presence_key = "demo_1613c"
            presence_unique_per_connection = True

        user = FakeUser("alice", 42)
        v1 = V(user=user, ws_session_id="wsA")
        v2 = V(user=user, ws_session_id="wsB")
        assert v1.get_presence_user_id() == "42"
        assert v2.get_presence_user_id() == "42"


# ---------------------------------------------------------------------------
# #1614 — auto-broadcast on track/untrack to _on_presence_change
# ---------------------------------------------------------------------------
class TestIssue1614AutoBroadcast:
    @patch("djust.presence.push_to_view")
    def test_track_presence_broadcasts_on_presence_change(self, mock_push):
        """track_presence fires push_to_view(<view_path>, handler='_on_presence_change')."""

        class V(FakeView, PresenceMixin):
            presence_key = "demo_1614a"

        v = V(ws_session_id="wsA")
        v.track_presence()

        assert mock_push.called, "push_to_view was not called"
        args, kwargs = mock_push.call_args
        view_path = args[0] if args else kwargs.get("view_path")
        assert view_path.endswith(".V")
        assert kwargs.get("handler") == "_on_presence_change"

    @patch("djust.presence.push_to_view")
    def test_untrack_presence_broadcasts_on_presence_change(self, mock_push):
        class V(FakeView, PresenceMixin):
            presence_key = "demo_1614b"

        v = V(ws_session_id="wsB")
        v.track_presence()
        mock_push.reset_mock()
        v.untrack_presence()
        assert mock_push.called
        _, kwargs = mock_push.call_args
        assert kwargs.get("handler") == "_on_presence_change"

    def test_default_on_presence_change_handler_refreshes_count_only(self):
        """Default handler must call _refresh_online_count and NOT track_presence
        (would create an infinite broadcast loop)."""

        class V(FakeView, PresenceMixin):
            presence_key = "demo_1614c"

        v = V(ws_session_id="wsX")
        v._presence_tracked = True
        v._presence_user_id = "1"  # already joined logically
        from djust.presence import PresenceManager

        PresenceManager.join_presence("demo_1614c", "other_user", {})

        with patch.object(v, "track_presence") as mock_track:
            v._on_presence_change()
            mock_track.assert_not_called()
        assert v.online_count == 1

    def test_on_presence_change_is_event_handler(self):
        """The default _on_presence_change must be @event_handler-decorated so
        the WS consumer's server_push gate (websocket.py:4954) accepts it."""
        from djust.decorators import is_event_handler

        handler = PresenceMixin._on_presence_change
        assert is_event_handler(handler), (
            "_on_presence_change must be @event_handler decorated; otherwise "
            "websocket.server_push will reject it as not handle_*/event_handler."
        )


# ---------------------------------------------------------------------------
# Cross-cutting end-to-end: zero-config "{{ online_count }}" pattern
# ---------------------------------------------------------------------------
class TestZeroConfigPresence:
    @patch("djust.presence.push_to_view")
    def test_two_anonymous_sessions_see_count_2(self, mock_push):
        """Two anonymous tabs with presence_unique_per_connection=True both end
        up with online_count == 2 after each tracks and the broadcast fans out."""

        class V(FakeView, PresenceMixin):
            presence_key = "zeroconfig"
            presence_unique_per_connection = True

        anon = FakeAnonymousUser()
        v1 = V(user=anon, session_key="anon_session", ws_session_id="ws1")
        v2 = V(user=anon, session_key="anon_session", ws_session_id="ws2")

        v1.track_presence()
        v2.track_presence()

        # Simulate the broadcast handler firing on both sessions
        v1._on_presence_change()
        v2._on_presence_change()

        assert v1.online_count == 2
        assert v2.online_count == 2
        # push_to_view was called on every track (2 total)
        assert mock_push.call_count == 2
