"""Anonymous presence ids are opaque: peers never see the Django session key.

A presence id is shared with everyone in the room (``list_presences()``,
``handle_presence_join``/``handle_presence_leave`` records and the
``LiveCursorMixin`` ``cursor_move`` broadcast). For an anonymous user it is an
HMAC of the session key under ``SECRET_KEY``: stable for the session, distinct
between sessions, and not the key itself.
"""

import json
from unittest.mock import Mock, patch

import pytest
from django.core.cache import cache
from django.test import override_settings

from djust.backends.memory import InMemoryPresenceBackend
from djust.backends.registry import reset_presence_backend, set_presence_backend
from djust.presence import (
    PRESENCE_TIMEOUT,
    LiveCursorMixin,
    anonymous_connection_presence_id,
    anonymous_presence_id,
)

SESSION_A = "q3v9xk2m7p1zr8t4w6y0n5b2c7d9f1g3"
SESSION_B = "h8j2k4l6m1n3p5r7s9t0v2w4x6y8z0a1"


class _AnonymousUser:
    is_authenticated = False


class _Session:
    def __init__(self, session_key):
        self.session_key = session_key


class _Request:
    def __init__(self, session_key):
        self.user = _AnonymousUser()
        self.session = _Session(session_key)


class _View:
    exposure_policy = "explicit"

    def __init__(self, session_key, ws_session_id="ws-1"):
        super().__init__()
        self.request = _Request(session_key)
        self._websocket_session_id = ws_session_id


class CursorView(_View, LiveCursorMixin):
    presence_key = "doc:anon-id"


@pytest.fixture(autouse=True)
def _fresh_presence():
    cache.clear()
    set_presence_backend(InMemoryPresenceBackend(timeout=PRESENCE_TIMEOUT))
    yield
    reset_presence_backend()
    cache.clear()


def test_id_is_not_the_session_key():
    presence_id = anonymous_presence_id(SESSION_A)
    assert SESSION_A not in presence_id
    assert presence_id.startswith("anon_")
    # 128 bits of hex after the prefix.
    assert len(presence_id) == len("anon_") + 32


def test_same_session_gets_same_id():
    v1 = CursorView(SESSION_A, ws_session_id="ws-1")
    v2 = CursorView(SESSION_A, ws_session_id="ws-2")
    assert v1.get_presence_user_id() == v2.get_presence_user_id()
    assert v1.get_presence_user_id() == anonymous_presence_id(SESSION_A)


def test_different_sessions_get_different_ids():
    assert CursorView(SESSION_A).get_presence_user_id() != (
        CursorView(SESSION_B).get_presence_user_id()
    )


def test_id_is_keyed_by_secret_key():
    with override_settings(SECRET_KEY="one-secret-key-for-this-test-xxxxxxxxxxxxxxxxxxxx"):
        first = anonymous_presence_id(SESSION_A)
    with override_settings(SECRET_KEY="another-secret-key-for-this-test-yyyyyyyyyyyyyyyy"):
        second = anonymous_presence_id(SESSION_A)
    assert first != second


def test_two_tabs_of_one_session_are_one_presence():
    v1 = CursorView(SESSION_A, ws_session_id="ws-1")
    v2 = CursorView(SESSION_A, ws_session_id="ws-2")
    v1.track_presence()
    v2.track_presence()
    assert v1.presence_count() == 1


def test_presence_list_and_join_record_omit_session_key():
    joined = []
    viewer = CursorView(SESSION_B, ws_session_id="ws-b")
    viewer.track_presence(meta={"name": "b"})

    view = CursorView(SESSION_A, ws_session_id="ws-a")
    view.handle_presence_join = joined.append
    view.track_presence(meta={"name": "a"})

    listed = json.dumps(viewer.list_presences())
    assert SESSION_A not in listed
    assert SESSION_B not in listed
    assert anonymous_presence_id(SESSION_A) in listed
    assert joined and SESSION_A not in json.dumps(joined)


@patch("djust.presence.get_channel_layer")
@patch("djust.presence.async_to_sync")
def test_cursor_broadcast_omits_session_key(mock_async_to_sync, mock_get_channel_layer):
    mock_get_channel_layer.return_value = Mock()
    group_send = Mock()
    mock_async_to_sync.return_value = group_send

    view = CursorView(SESSION_A)
    view.track_presence(meta={"color": "#123456"})
    view.update_cursor_position(10, 20)

    group_send.assert_called_once()
    _group, message = group_send.call_args[0]
    assert message["event"] == "cursor_move"
    assert message["payload"]["user_id"] == anonymous_presence_id(SESSION_A)
    assert SESSION_A not in json.dumps(message)

    # The cursor map other participants read back is keyed by the same id.
    cursors = view.get_cursors()
    assert list(cursors) == [anonymous_presence_id(SESSION_A)]
    assert SESSION_A not in json.dumps(cursors)


def test_per_connection_id_omits_transport_session_id():
    class PerConnectionView(CursorView):
        presence_unique_per_connection = True

    ws_session_id = "0f3c2b7e-5d1a-4e9b-8c6f-2a7d9e1b4c3f"
    v = PerConnectionView(SESSION_A, ws_session_id=ws_session_id)
    presence_id = v.get_presence_user_id()
    assert presence_id == anonymous_connection_presence_id(ws_session_id)
    assert presence_id.startswith("anon_conn_")
    assert ws_session_id not in presence_id
    assert SESSION_A not in presence_id
