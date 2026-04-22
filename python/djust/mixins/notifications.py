"""
``NotificationMixin`` — hooks ``self.listen(channel)`` + ``handle_info(message)``
into any LiveView. Wired up on the WebSocket side by ``LiveViewConsumer``,
which calls ``group_add(f"djust_db_notify_{channel}", self.channel_name)``
for every channel the view subscribed to during ``mount()``.
"""

import logging
from typing import Any, Dict, Iterable, Set

from asgiref.sync import async_to_sync

from ..db.decorators import _validate_channel
from ..db.exceptions import DatabaseNotificationNotSupported

logger = logging.getLogger(__name__)


class NotificationMixin:
    """Subscribe a LiveView to PostgreSQL ``pg_notify`` channels.

    Typical usage::

        from djust import LiveView
        from djust.db import notify_on_save

        @notify_on_save(channel="orders")
        class Order(models.Model):
            ...

        class OrderDashboard(LiveView):
            def mount(self, request, **kwargs):
                self.orders = list(Order.objects.filter(status="pending"))
                self.listen("orders")

            def handle_info(self, message):
                if message["type"] == "db_notify":
                    self.orders = list(Order.objects.filter(status="pending"))
    """

    # Populated lazily on first call to self.listen().
    # Mypy / IDE friendliness:
    _listen_channels: Set[str]

    def listen(self, channel: str) -> None:
        """Subscribe this view to pg_notify on ``channel``.

        Call from ``mount()`` (or any later event handler). Safe to call
        multiple times with the same channel — subsequent calls are no-ops.

        Raises:
            ValueError: if the channel name doesn't match the safe
                pattern (``^[a-z_][a-z0-9_]{0,62}$``).
            DatabaseNotificationNotSupported: if psycopg isn't installed
                or the backend isn't postgresql.
        """
        _validate_channel(channel)

        channels = getattr(self, "_listen_channels", None)
        if channels is None:
            channels = set()
            self._listen_channels = channels

        if channel in channels:
            return
        channels.add(channel)

        # Lazily kick the process-wide listener so NOTIFYs start flowing
        # even before the WS consumer wires up the group. The consumer
        # still handles group_add separately when it detects this view.
        try:
            from ..db.notifications import PostgresNotifyListener

            async_to_sync(PostgresNotifyListener.instance().ensure_listening)(channel)
        except DatabaseNotificationNotSupported:
            # Re-raise so callers know to guard with a feature flag or
            # skip listen() on non-postgres test environments.
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("listen(%s): failed to start pg listener — %s", channel, exc)

    def _restore_listen_channels(self) -> None:
        """Re-issue ``LISTEN`` on every subscribed channel after state restoration.

        Called by the WebSocket consumer's state-restoration path (issue
        #894). When ``mount()`` is skipped because pre-rendered session
        state exists, the ``self._listen_channels`` set survives the
        JSON round-trip intact, but the side-effect registration with
        :class:`PostgresNotifyListener` — the actual Postgres ``LISTEN
        channel`` SQL statement — does NOT survive a process boundary.

        Without this replay, a sticky-session load balancer or a
        rolling-deploy restart that lands the HTTP mount and the WS
        connection on different processes will leave the WS-side
        process's listener with no subscriptions, and NOTIFYs will
        never flow to ``handle_info``.

        ``ensure_listening`` is already idempotent (it early-returns
        on known channels) so calling it on the same process that
        initially registered is harmless. Exceptions are logged and
        swallowed — restoration must not break the WS.
        """
        channels = getattr(self, "_listen_channels", None)
        if not channels:
            return
        try:
            from ..db.notifications import PostgresNotifyListener
        except ImportError:
            return
        from asgiref.sync import async_to_sync

        for channel in list(channels):
            try:
                async_to_sync(PostgresNotifyListener.instance().ensure_listening)(channel)
            except DatabaseNotificationNotSupported:
                # Non-postgres backend — ``listen()`` would have raised
                # at the original call site, so seeing this here means
                # the backend changed between save and restore. Log and
                # continue; other channels / mixins may still work.
                logger.warning(
                    "NotificationMixin._restore_listen_channels: postgres no longer "
                    "available; channel %r will not receive NOTIFYs (issue #894).",
                    channel,
                )
            except Exception as exc:  # noqa: BLE001 — restoration must never kill the WS
                logger.warning(
                    "NotificationMixin._restore_listen_channels: failed to re-issue "
                    "LISTEN %s (issue #894): %s",
                    channel,
                    exc,
                )

    def handle_info(self, message: Dict[str, Any]) -> None:
        """Receive out-of-band messages (e.g. pg_notify events).

        Default implementation is a no-op. Override to react to database
        change notifications::

            def handle_info(self, message):
                if message["type"] == "db_notify":
                    self.refresh()
        """
        # Intentional no-op. Public hook for subclasses.

    def _listen_channels_set(self) -> Iterable[str]:
        """Internal accessor used by the WS consumer. Always returns a set."""
        return getattr(self, "_listen_channels", set())
