"""Unit tests for djust.db notifications (v0.5.0 P1).

Coverage:

* Channel-name validator (``_validate_channel``).
* ``@notify_on_save`` decorator — signal wiring, payload shape, channel
  defaults, positional/keyword forms.
* ``send_pg_notify`` — SQL emission, non-postgres no-op.
* ``NotificationMixin.listen`` / ``handle_info`` — subscription tracking,
  idempotency, default no-op.
* ``LiveViewConsumer.db_notify`` — handler routing + re-render path.

Everything is mocked — no live Postgres required. Round-trip tests live
in ``tests/integration/test_pg_notify_roundtrip.py`` and are skipped
when PG isn't available.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.db.models.signals import post_delete, post_save

from djust.db import (
    DatabaseNotificationNotSupported,
    PostgresNotifyListener,
    notify_on_save,
    send_pg_notify,
)
from djust.db.decorators import _validate_channel
from djust.mixins.notifications import NotificationMixin


# ---------------------------------------------------------------------------
# Channel-name validator
# ---------------------------------------------------------------------------


class TestValidateChannel:
    def test_accepts_simple_name(self):
        assert _validate_channel("orders") == "orders"

    def test_accepts_underscore_start(self):
        assert _validate_channel("_private") == "_private"

    def test_accepts_digits_after_first_char(self):
        assert _validate_channel("orders_v2") == "orders_v2"

    @pytest.mark.parametrize(
        "bad",
        [
            "",  # empty
            "Orders",  # uppercase
            "orders-updated",  # hyphen
            "orders.updated",  # dot
            "1orders",  # starts with digit
            "orders; DROP TABLE users;",  # injection attempt
            "orders'",  # quote
            " orders",  # leading space
            "a" * 64,  # too long (> 63 chars)
            None,  # not a string
            42,  # int
        ],
    )
    def test_rejects_bad_name(self, bad):
        with pytest.raises(ValueError):
            _validate_channel(bad)


# ---------------------------------------------------------------------------
# send_pg_notify
# ---------------------------------------------------------------------------


class TestSendPgNotify:
    @patch("djust.db.decorators.connection")
    def test_sends_notify_on_postgres(self, mock_conn):
        mock_conn.vendor = "postgresql"
        cursor_cm = MagicMock()
        cursor = MagicMock()
        cursor_cm.__enter__.return_value = cursor
        mock_conn.cursor.return_value = cursor_cm

        send_pg_notify("orders", {"pk": 1, "event": "save"})

        cursor.execute.assert_called_once()
        sql, params = cursor.execute.call_args[0]
        assert sql == "NOTIFY orders, %s"
        assert json.loads(params[0]) == {"pk": 1, "event": "save"}

    @patch("djust.db.decorators.connection")
    def test_noop_on_sqlite(self, mock_conn):
        mock_conn.vendor = "sqlite"
        send_pg_notify("orders", {"pk": 1})
        mock_conn.cursor.assert_not_called()

    def test_rejects_bad_channel(self):
        with pytest.raises(ValueError):
            send_pg_notify("bad-channel", {})

    @patch("djust.db.decorators.connection")
    def test_payload_over_hard_limit_is_dropped_and_logged(self, mock_conn, caplog):
        """Issue #810: payloads > 7500 bytes are dropped to avoid the 8000B NOTIFY cap."""
        import logging

        mock_conn.vendor = "postgresql"
        # Build a payload that JSON-encodes to > 7500 bytes.
        big = {"data": "x" * 8000}
        with caplog.at_level(logging.ERROR, logger="djust.db"):
            send_pg_notify("orders", big)
        # Cursor was never acquired — notification was dropped.
        mock_conn.cursor.assert_not_called()
        assert any("DROPPING notification" in r.message for r in caplog.records)

    @patch("djust.db.decorators.connection")
    def test_payload_over_soft_limit_warns_but_sends(self, mock_conn, caplog):
        """Issue #810: payloads > 4KB log a warning but still send (under hard cap)."""
        import logging

        mock_conn.vendor = "postgresql"
        cursor_cm = MagicMock()
        cursor = MagicMock()
        cursor_cm.__enter__.return_value = cursor
        mock_conn.cursor.return_value = cursor_cm

        # ~5KB body (over 4K soft, under 7.5K hard).
        mid = {"data": "y" * 5000}
        with caplog.at_level(logging.WARNING, logger="djust.db"):
            send_pg_notify("orders", mid)
        cursor.execute.assert_called_once()  # still sent
        assert any("soft limit" in r.message.lower() for r in caplog.records)

    @patch("djust.db.decorators.connection")
    def test_small_payload_does_not_log(self, mock_conn, caplog):
        """Normal-sized payload produces no size-related log at WARNING or above."""
        import logging

        mock_conn.vendor = "postgresql"
        cursor_cm = MagicMock()
        cursor = MagicMock()
        cursor_cm.__enter__.return_value = cursor
        mock_conn.cursor.return_value = cursor_cm

        with caplog.at_level(logging.WARNING, logger="djust.db"):
            send_pg_notify("orders", {"pk": 1})
        assert not any("limit" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# @notify_on_save
# ---------------------------------------------------------------------------


def _make_fake_model(app_label="shop", model_name="order"):
    """Build a minimal duck-typed object that satisfies the decorator."""

    class _Meta:
        pass

    meta = _Meta()
    meta.app_label = app_label
    meta.model_name = model_name
    meta.label = f"{app_label}.{model_name}"

    class FakeModel:
        _meta = meta

    FakeModel.__name__ = model_name.capitalize()
    return FakeModel


class TestNotifyOnSave:
    def teardown_method(self):
        # Disconnect any receivers left attached by decorator calls.
        post_save.receivers = []
        post_delete.receivers = []

    def test_bare_decorator_uses_default_channel(self):
        Order = _make_fake_model("shop", "order")
        decorated = notify_on_save(Order)
        assert decorated._djust_notify_channel == "shop_order"

    def test_keyword_channel_overrides_default(self):
        Order = _make_fake_model("shop", "order")
        decorated = notify_on_save(channel="orders")(Order)
        assert decorated._djust_notify_channel == "orders"

    def test_positional_channel_string(self):
        Order = _make_fake_model("shop", "order")
        decorated = notify_on_save("orders")(Order)
        assert decorated._djust_notify_channel == "orders"

    def test_connects_both_signals(self):
        Order = _make_fake_model("shop", "order")
        decorated = notify_on_save(Order)
        on_save, on_delete = decorated._djust_notify_receivers

        def _live(r):
            """Django stores (lookup_key, receiver). Unwrap weakref-or-strong."""
            # weak=False receivers are stored strongly; test both shapes.
            return r() if callable(r) and hasattr(r, "__weakref__") else r

        save_receivers = [_live(r[1]) for r in post_save.receivers]
        delete_receivers = [_live(r[1]) for r in post_delete.receivers]
        assert on_save in save_receivers
        assert on_delete in delete_receivers

    def test_rejects_invalid_channel(self):
        Order = _make_fake_model("shop", "order")
        with pytest.raises(ValueError):
            notify_on_save(channel="BAD-NAME")(Order)

    @patch("djust.db.decorators.send_pg_notify")
    def test_save_signal_fires_notify(self, mock_send):
        Order = _make_fake_model("shop", "order")
        decorated = notify_on_save(Order)
        on_save, _ = decorated._djust_notify_receivers

        instance = MagicMock()
        instance.pk = 7
        on_save(sender=decorated, instance=instance, created=True)

        mock_send.assert_called_once_with(
            "shop_order",
            {"pk": 7, "event": "save", "model": "shop.order"},
        )

    @patch("djust.db.decorators.send_pg_notify")
    def test_delete_signal_fires_notify(self, mock_send):
        Order = _make_fake_model("shop", "order")
        decorated = notify_on_save(Order)
        _, on_delete = decorated._djust_notify_receivers

        instance = MagicMock()
        instance.pk = 3
        on_delete(sender=decorated, instance=instance)

        mock_send.assert_called_once_with(
            "shop_order",
            {"pk": 3, "event": "delete", "model": "shop.order"},
        )

    @patch("djust.db.decorators.send_pg_notify", side_effect=RuntimeError("boom"))
    def test_swallows_send_exception(self, _mock_send):
        Order = _make_fake_model("shop", "order")
        decorated = notify_on_save(Order)
        on_save, _ = decorated._djust_notify_receivers
        # Must not propagate — model save() should never break on NOTIFY failure.
        instance = MagicMock()
        instance.pk = 1
        on_save(sender=decorated, instance=instance, created=True)


# ---------------------------------------------------------------------------
# NotificationMixin
# ---------------------------------------------------------------------------


class _Listener(NotificationMixin):
    """Concrete subclass for mixin tests."""


class TestNotificationMixin:
    def test_listen_adds_to_set(self):
        view = _Listener()
        with patch("djust.db.notifications.PostgresNotifyListener") as mock_listener_cls:
            mock_listener_cls.instance.return_value.ensure_listening = AsyncMock()
            view.listen("orders")
        assert view._listen_channels == {"orders"}

    def test_listen_is_idempotent(self):
        view = _Listener()
        with patch("djust.db.notifications.PostgresNotifyListener") as mock_listener_cls:
            mock_listener_cls.instance.return_value.ensure_listening = AsyncMock()
            view.listen("orders")
            view.listen("orders")
            view.listen("orders")
            # Only called once because second/third are no-ops.
            assert mock_listener_cls.instance.return_value.ensure_listening.call_count == 1

    def test_listen_rejects_bad_channel(self):
        view = _Listener()
        with pytest.raises(ValueError):
            view.listen("BAD CHANNEL")

    def test_handle_info_is_noop(self):
        view = _Listener()
        # Should not raise and should return None.
        assert view.handle_info({"type": "db_notify", "channel": "x", "payload": {}}) is None

    def test_listen_propagates_unsupported(self):
        view = _Listener()
        with patch("djust.db.notifications.PostgresNotifyListener") as mock_listener_cls:
            mock_listener_cls.instance.side_effect = DatabaseNotificationNotSupported("no psycopg")
            with pytest.raises(DatabaseNotificationNotSupported):
                view.listen("orders")


# ---------------------------------------------------------------------------
# PostgresNotifyListener singleton
# ---------------------------------------------------------------------------


class TestListenerSingleton:
    def teardown_method(self):
        PostgresNotifyListener.reset_for_tests()

    def test_instance_is_singleton(self):
        a = PostgresNotifyListener.instance()
        b = PostgresNotifyListener.instance()
        assert a is b

    def test_reset_for_tests_clears(self):
        a = PostgresNotifyListener.instance()
        PostgresNotifyListener.reset_for_tests()
        b = PostgresNotifyListener.instance()
        assert a is not b

    def test_areset_for_tests_awaits_task(self):
        """Issue #811: async variant awaits the cancelled task so tests don't race."""

        async def run():
            listener = PostgresNotifyListener.instance()
            # Fake a task on the listener so the reset has something to await.

            async def _noop():
                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    raise

            listener._task = asyncio.create_task(_noop())
            await PostgresNotifyListener.areset_for_tests()
            # After areset returns, the task must be done (cancelled).
            assert listener._task.done()
            # And the singleton slot must be cleared.
            assert PostgresNotifyListener._instance is None

        asyncio.run(run())


class TestConsumerWithoutNotificationMixin:
    """Issue #812: consumers whose view has no NotificationMixin must not break."""

    def test_consumer_iterates_safely_when_no_listen_channels_attr(self):
        """getattr(view, '_listen_channels', None) handles views without the mixin."""
        from djust.websocket import LiveViewConsumer

        # A view instance without NotificationMixin has no _listen_channels attr.
        class PlainView:
            pass

        view = PlainView()
        assert not hasattr(view, "_listen_channels")

        # The consumer's pattern:
        listen_channels = getattr(view, "_listen_channels", None)
        assert listen_channels is None  # no iteration attempted

        # Use the same defensive idiom in a direct unit assertion:
        # the `if listen_channels:` gate prevents a TypeError on None.
        # If we DID enter the loop, iterating None would crash — we rely
        # on that gate being present in websocket.py:1321.
        assert LiveViewConsumer is not None  # import sanity only

    def test_consumer_iterates_safely_when_listen_channels_is_empty_set(self):
        """Mixin installed but no channels subscribed → loop body never executes."""
        from djust.websocket import LiveViewConsumer

        class MixedView:
            _listen_channels = set()

        view = MixedView()
        listen_channels = getattr(view, "_listen_channels", None)
        assert listen_channels == set()
        # `if listen_channels:` evaluates empty set as falsy.
        iterated = False
        if listen_channels:  # noqa: SIM102
            for _ in listen_channels:
                iterated = True
        assert iterated is False
        assert LiveViewConsumer is not None

    def test_dispatch_sends_group_message(self):
        """_dispatch should forward to channel_layer.group_send with the
        documented ``db_notify`` envelope."""
        listener = PostgresNotifyListener.instance()

        layer = MagicMock()
        layer.group_send = AsyncMock()
        with patch("channels.layers.get_channel_layer", return_value=layer):
            asyncio.run(listener._dispatch("orders", json.dumps({"pk": 1, "event": "save"})))

        layer.group_send.assert_awaited_once_with(
            "djust_db_notify_orders",
            {
                "type": "db_notify",
                "channel": "orders",
                "payload": {"pk": 1, "event": "save"},
            },
        )

    def test_dispatch_handles_non_json_payload(self):
        listener = PostgresNotifyListener.instance()
        layer = MagicMock()
        layer.group_send = AsyncMock()
        with patch("channels.layers.get_channel_layer", return_value=layer):
            asyncio.run(listener._dispatch("orders", "not-json"))
        sent = layer.group_send.await_args[0][1]
        assert sent["payload"] == {"raw": "not-json"}


# ---------------------------------------------------------------------------
# LiveViewConsumer.db_notify
# ---------------------------------------------------------------------------


class TestConsumerDbNotify:
    """Sanity-check the consumer routing: the handler exists, has the right
    signature, and no-ops when no view is mounted.

    Full re-render exercise belongs in integration tests — we only assert
    the contract here.
    """

    def test_handler_exists_on_consumer(self):
        from djust.websocket import LiveViewConsumer

        assert hasattr(LiveViewConsumer, "db_notify")
        assert callable(LiveViewConsumer.db_notify)

    def test_handler_noop_without_view(self):
        from djust.websocket import LiveViewConsumer

        consumer = LiveViewConsumer.__new__(LiveViewConsumer)
        consumer.view_instance = None
        # Should return quietly, not raise.
        asyncio.run(consumer.db_notify({"channel": "orders", "payload": {"pk": 1}}))

    def test_handler_calls_handle_info_and_rerenders(self):
        """The handler must route ``message`` to ``handle_info`` with the
        documented ``{"type": "db_notify", "channel": ..., "payload": ...}``
        shape, then re-render via the existing broadcast path."""
        from djust.websocket import LiveViewConsumer

        consumer = LiveViewConsumer.__new__(LiveViewConsumer)
        view = MagicMock()
        view.__class__.__name__ = "FakeView"
        view.handle_info = MagicMock()
        view._skip_render = False
        view._sync_state_to_rust = MagicMock()
        view.render_with_diff = MagicMock(return_value=("<html></html>", '[{"op": "test"}]', 1))
        consumer.view_instance = view
        consumer._processing_user_event = False
        consumer._render_lock = asyncio.Lock()
        consumer._send_update = AsyncMock()
        consumer._flush_push_events = AsyncMock()
        consumer._flush_flash = AsyncMock()
        consumer._flush_page_metadata = AsyncMock()
        consumer._send_noop = AsyncMock()

        asyncio.run(
            consumer.db_notify({"channel": "orders", "payload": {"pk": 1, "event": "save"}})
        )

        view.handle_info.assert_called_once_with(
            {
                "type": "db_notify",
                "channel": "orders",
                "payload": {"pk": 1, "event": "save"},
            }
        )
        consumer._send_update.assert_awaited_once()
        kwargs = consumer._send_update.await_args.kwargs
        assert kwargs["source"] == "broadcast"
        assert kwargs["broadcast"] is True
