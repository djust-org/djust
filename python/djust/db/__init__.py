"""
``djust.db`` — PostgreSQL LISTEN/NOTIFY bridge for LiveView.

Public API:

* :class:`notify_on_save` — model decorator that emits ``pg_notify`` on save/delete.
* :func:`untrack` — disconnect the signal receivers wired by ``notify_on_save``
  (mostly for test teardowns).
* :func:`send_pg_notify` — low-level helper to send a NOTIFY from any code path.
* :class:`PostgresNotifyListener` — singleton async listener (usually not
  invoked directly; views subscribe via ``self.listen(channel)``).
* :class:`DatabaseNotificationNotSupported` — raised when psycopg/postgres
  is unavailable and the listener is explicitly requested.
"""

from .decorators import notify_on_save, send_pg_notify, untrack
from .exceptions import DatabaseNotificationNotSupported
from .notifications import PostgresNotifyListener

__all__ = [
    "notify_on_save",
    "untrack",
    "send_pg_notify",
    "PostgresNotifyListener",
    "DatabaseNotificationNotSupported",
]
