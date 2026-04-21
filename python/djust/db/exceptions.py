"""Exceptions raised by djust.db."""


class DatabaseNotificationNotSupported(RuntimeError):
    """Raised when a view calls ``self.listen()`` in an environment that
    cannot support PostgreSQL LISTEN/NOTIFY — typically because ``psycopg``
    is not installed or the configured database backend is not PostgreSQL.

    This is *not* raised at import time or at decorator-registration time:
    ``@notify_on_save`` and ``send_pg_notify()`` degrade gracefully on
    non-postgres backends so the same model code can ship to sqlite test
    suites. The exception only fires when a view actively attempts to
    subscribe via ``self.listen()``.
    """
