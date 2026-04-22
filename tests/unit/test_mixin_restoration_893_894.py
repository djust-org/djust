"""Regression tests for issues #893 and #894.

Sibling bugs to #889 (fixed in PR #891):

- #893: ``PresenceMixin.track_presence()`` calls
  ``PresenceManager.join_presence(...)`` as a process-wide side effect.
  On WS state-restore (when ``mount()`` is skipped), the flag attrs
  (``_presence_tracked`` etc.) survive the session round-trip, but the
  PresenceManager registration does not — the user's own presence is
  lost.

- #894: ``NotificationMixin.listen(channel)`` calls
  ``PostgresNotifyListener.instance().ensure_listening(channel)`` as a
  process-wide side effect. On a cross-process state-restore (server
  restart between HTTP mount and WS connect, or sticky-session LB
  sending WS to a different worker), the Postgres ``LISTEN channel``
  SQL statement isn't re-issued, so NOTIFYs never flow.

Both fixes add a ``_restore_*`` method that the WS consumer's
state-restoration path calls right after ``_restore_private_state``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


from djust.mixins.notifications import NotificationMixin
from djust.presence import PresenceMixin


# ---------------------------------------------------------------------------
# Issue #893 — PresenceMixin._restore_presence
# ---------------------------------------------------------------------------


class _PresenceView(PresenceMixin):
    """Concrete mixin host for presence testing."""

    presence_key = "doc:{doc_id}"


class TestPresenceRestoration:
    def test_restore_with_tracked_true_calls_join_presence(self):
        v = _PresenceView()
        v.doc_id = 42
        v._presence_tracked = True
        v._presence_user_id = "user-7"
        v._presence_meta = {"name": "Ada", "color": "#f00"}

        with patch("djust.presence.PresenceManager.join_presence") as mock_join:
            v._restore_presence()

        mock_join.assert_called_once_with("doc:42", "user-7", {"name": "Ada", "color": "#f00"})

    def test_restore_when_not_tracked_is_noop(self):
        v = _PresenceView()
        v.doc_id = 1
        v._presence_tracked = False
        v._presence_user_id = "user-1"
        v._presence_meta = {}

        with patch("djust.presence.PresenceManager.join_presence") as mock_join:
            v._restore_presence()

        mock_join.assert_not_called()

    def test_restore_with_missing_user_id_is_noop(self):
        """Defensive: if the session round-trip somehow dropped the
        user_id but kept the ``_presence_tracked=True`` flag, restoring
        without a user_id would call ``join_presence(key, None, meta)``
        which is nonsense. Guard against it.
        """
        v = _PresenceView()
        v.doc_id = 1
        v._presence_tracked = True
        v._presence_user_id = None  # corrupted / partial state
        v._presence_meta = {"name": "Ada"}

        with patch("djust.presence.PresenceManager.join_presence") as mock_join:
            v._restore_presence()

        mock_join.assert_not_called()

    def test_restore_with_missing_meta_uses_empty_dict(self):
        v = _PresenceView()
        v.doc_id = 1
        v._presence_tracked = True
        v._presence_user_id = "user-1"
        v._presence_meta = None  # defensive — treat as empty

        with patch("djust.presence.PresenceManager.join_presence") as mock_join:
            v._restore_presence()

        mock_join.assert_called_once_with("doc:1", "user-1", {})

    def test_restore_swallows_backend_exceptions(self, caplog):
        """The WS must not die if the presence backend is temporarily
        unavailable. Log a warning and move on.
        """
        import logging

        v = _PresenceView()
        v.doc_id = 1
        v._presence_tracked = True
        v._presence_user_id = "user-1"
        v._presence_meta = {}

        with patch(
            "djust.presence.PresenceManager.join_presence",
            side_effect=RuntimeError("backend down"),
        ):
            with caplog.at_level(logging.WARNING, logger="djust.presence"):
                v._restore_presence()  # must not raise

        assert any("failed to re-register presence" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Issue #894 — NotificationMixin._restore_listen_channels
# ---------------------------------------------------------------------------


class _NotifyView(NotificationMixin):
    """Concrete mixin host for notification testing."""


class TestNotificationRestoration:
    def test_restore_with_listen_channels_calls_ensure_listening_per_channel(self):
        v = _NotifyView()
        v._listen_channels = {"orders", "claims"}

        mock_listener = MagicMock()
        # async_to_sync wraps the coroutine; we want to assert on the
        # UNWRAPPED method, so patch ensure_listening directly on the
        # singleton.
        with patch("djust.db.notifications.PostgresNotifyListener.instance") as mock_instance:
            mock_instance.return_value = mock_listener

            # ensure_listening is used as an async coroutine via async_to_sync
            async def _noop(ch):
                return None

            mock_listener.ensure_listening = _noop
            v._restore_listen_channels()

        # Can't easily assert call args through async_to_sync — verify via
        # a side-effecting spy instead:
        called = []

        async def _spy(ch):
            called.append(ch)

        mock_listener.ensure_listening = _spy
        v._listen_channels = {"orders", "claims"}
        with patch("djust.db.notifications.PostgresNotifyListener.instance") as mock_instance:
            mock_instance.return_value = mock_listener
            v._restore_listen_channels()
        assert set(called) == {"orders", "claims"}

    def test_restore_with_empty_set_is_noop(self):
        v = _NotifyView()
        v._listen_channels = set()

        with patch("djust.db.notifications.PostgresNotifyListener.instance") as mock_instance:
            v._restore_listen_channels()

        mock_instance.assert_not_called()

    def test_restore_with_missing_attribute_is_noop(self):
        """Views that never called ``listen()`` don't have
        ``_listen_channels`` set at all — the restore method must
        tolerate that cleanly (no ``AttributeError``)."""
        v = _NotifyView()
        assert not hasattr(v, "_listen_channels")

        with patch("djust.db.notifications.PostgresNotifyListener.instance") as mock_instance:
            v._restore_listen_channels()  # must not raise

        mock_instance.assert_not_called()

    def test_restore_swallows_per_channel_exceptions(self, caplog):
        """One failed LISTEN shouldn't block the others."""
        import logging

        v = _NotifyView()
        v._listen_channels = {"orders", "claims"}

        call_log = []

        async def _flaky(ch):
            call_log.append(ch)
            if ch == "orders":
                raise RuntimeError("pg hiccup")

        mock_listener = MagicMock()
        mock_listener.ensure_listening = _flaky

        with patch("djust.db.notifications.PostgresNotifyListener.instance") as mock_instance:
            mock_instance.return_value = mock_listener
            with caplog.at_level(logging.WARNING, logger="djust.mixins.notifications"):
                v._restore_listen_channels()  # must not raise

        # Both channels attempted even though one failed
        assert set(call_log) == {"orders", "claims"}
        # The failure logged a warning
        assert any("failed to re-issue LISTEN orders" in r.message for r in caplog.records)

    def test_restore_swallows_postgres_unavailable(self, caplog):
        """If the postgres backend is unavailable at restore time,
        don't crash — log and continue."""
        import logging

        from djust.db.exceptions import DatabaseNotificationNotSupported

        v = _NotifyView()
        v._listen_channels = {"orders"}

        async def _raise_unsupported(ch):
            raise DatabaseNotificationNotSupported("no pg")

        mock_listener = MagicMock()
        mock_listener.ensure_listening = _raise_unsupported

        with patch("djust.db.notifications.PostgresNotifyListener.instance") as mock_instance:
            mock_instance.return_value = mock_listener
            with caplog.at_level(logging.WARNING, logger="djust.mixins.notifications"):
                v._restore_listen_channels()  # must not raise

        assert any("postgres no longer available" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# End-to-end: session round-trip preserves the call and both replays fire
# ---------------------------------------------------------------------------


class TestEndToEndSessionRoundTrip:
    def test_http_mount_then_restore_replays_both_side_effects(self):
        """Drives the full flow in Python:
        1. Instantiate a view with PresenceMixin + NotificationMixin.
        2. Set the attrs that ``track_presence`` / ``listen`` would set.
        3. Simulate the session round-trip (JSON serialize + deserialize).
        4. On a FRESH instance, call ``_restore_presence`` and
           ``_restore_listen_channels``.
        5. Assert both side effects were re-issued.
        """
        import json

        class MyView(PresenceMixin, NotificationMixin):
            presence_key = "doc:{doc_id}"

        # Simulate what mount() + track_presence() + listen() would have
        # written to instance state — these are all session-serializable.
        v = MyView()
        v.doc_id = 7
        v._presence_tracked = True
        v._presence_user_id = "u-7"
        v._presence_meta = {"name": "Grace"}
        v._listen_channels = {"doc:7"}

        # Session round-trip only preserves JSON-serializable values.
        state = {
            "doc_id": v.doc_id,
            "_presence_tracked": v._presence_tracked,
            "_presence_user_id": v._presence_user_id,
            "_presence_meta": v._presence_meta,
            "_listen_channels": list(v._listen_channels),  # sets → lists for JSON
        }
        blob = json.dumps(state)  # must not raise
        restored = json.loads(blob)

        # WS-side: fresh instance, apply restored state.
        ws_view = MyView()
        for k, val in restored.items():
            setattr(ws_view, k, val)
        ws_view._listen_channels = set(ws_view._listen_channels)  # back to set

        # Neither side effect has been replayed yet — PresenceManager /
        # PostgresNotifyListener don't know about this restored view.
        presence_calls = []
        listen_calls = []

        async def _fake_listen(ch):
            listen_calls.append(ch)

        mock_listener = MagicMock()
        mock_listener.ensure_listening = _fake_listen

        with patch("djust.presence.PresenceManager.join_presence") as mock_join:
            with patch("djust.db.notifications.PostgresNotifyListener.instance") as mock_instance:
                mock_instance.return_value = mock_listener
                mock_join.side_effect = lambda *a, **kw: presence_calls.append(a)
                ws_view._restore_presence()
                ws_view._restore_listen_channels()

        assert presence_calls == [("doc:7", "u-7", {"name": "Grace"})]
        assert listen_calls == ["doc:7"]
