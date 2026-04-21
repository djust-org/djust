"""
``@notify_on_save`` and ``send_pg_notify`` — emit PostgreSQL ``NOTIFY``
statements from Django signals or arbitrary code paths.

The decorator is a zero-config wrapper: slap it on a model and every
``save()`` / ``delete()`` sends a minimal JSON payload to a channel. Any
``LiveView`` that called ``self.listen(<channel>)`` in ``mount()`` will
receive a ``db_notify`` message and its ``handle_info()`` will fire.

Design decisions (see pipeline plan for rationale):

* Payload is minimal: ``{"pk": ..., "event": "save"|"delete", "model": ...}``.
  Receivers re-fetch if they need more state.
* Channel name defaults to ``{app_label}_{model_name}`` but can be
  overridden via ``@notify_on_save(channel="orders")``.
* Channel names are strictly validated against ``^[a-z_][a-z0-9_]{0,62}$``.
  This is security-critical: Postgres ``NOTIFY`` does NOT accept bind
  parameters for the channel name, so the regex is the only defense
  against SQL injection.
* Non-postgres backends no-op with a debug log — the same ``@notify_on_save``
  decorated model works in sqlite test suites.
"""

import json
import logging
import re
from typing import Any, Callable, Dict, Optional, Union

from django.db import connection
from django.db.models.signals import post_delete, post_save

logger = logging.getLogger(__name__)

# Channel names follow Postgres identifier rules (unquoted lowercase). The
# ceiling of 63 characters matches Postgres' NAMEDATALEN default (64 minus
# the trailing NUL). Starting with a digit is disallowed.
_CHANNEL_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _validate_channel(name: Any) -> str:
    """Return ``name`` if it's a safe pg channel identifier; else raise.

    This is security-critical — the channel name is interpolated directly
    into the ``NOTIFY`` SQL statement because Postgres does not accept
    bind parameters for identifiers.
    """
    if not isinstance(name, str) or not _CHANNEL_RE.match(name):
        raise ValueError(
            f"Invalid pg_notify channel name: {name!r} (must match {_CHANNEL_RE.pattern})"
        )
    return name


def send_pg_notify(channel: str, payload: Dict[str, Any]) -> None:
    """Emit a PostgreSQL ``NOTIFY`` on ``channel`` with a JSON-encoded payload.

    On non-postgres backends this is a no-op (debug-logged). Call this from
    Celery tasks, management commands, or any code path that needs to
    broadcast to connected LiveViews.

    Args:
        channel: Must match ``^[a-z_][a-z0-9_]{0,62}$``. Validated strictly
            because it's interpolated into SQL.
        payload: Any JSON-serializable mapping. Postgres caps NOTIFY
            payloads at 8000 bytes — keep it small.
    """
    _validate_channel(channel)
    if connection.vendor != "postgresql":
        logger.debug(
            "send_pg_notify(%s) skipped — backend is %s, not postgresql",
            channel,
            connection.vendor,
        )
        return
    body = json.dumps(payload, separators=(",", ":"), default=str)
    with connection.cursor() as cur:
        # channel is regex-validated above; Postgres NOTIFY takes no bind
        # parameters for the channel identifier.
        cur.execute(f"NOTIFY {channel}, %s", [body])  # nosec B608 — regex-validated identifier


def notify_on_save(
    model_or_channel: Optional[Union[type, str]] = None,
    *,
    channel: Optional[str] = None,
) -> Callable:
    """Decorator that wires Django post_save/post_delete signals to pg_notify.

    Three supported invocation forms::

        @notify_on_save                    # default channel "{app}_{model}"
        class Order(models.Model): ...

        @notify_on_save(channel="orders")  # explicit keyword channel
        class Order(models.Model): ...

        @notify_on_save("orders")          # positional channel shorthand
        class Order(models.Model): ...
    """

    def decorate(model: type) -> type:
        effective = _validate_channel(
            channel or f"{model._meta.app_label}_{model._meta.model_name}"
        )
        label = model._meta.label

        def _on_save(sender, instance, **_kw):
            try:
                send_pg_notify(
                    effective,
                    {"pk": instance.pk, "event": "save", "model": label},
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "notify_on_save: failed to emit NOTIFY for %s pk=%s: %s",
                    label,
                    getattr(instance, "pk", None),
                    exc,
                )

        def _on_delete(sender, instance, **_kw):
            try:
                send_pg_notify(
                    effective,
                    {"pk": instance.pk, "event": "delete", "model": label},
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "notify_on_save: failed to emit NOTIFY for %s pk=%s: %s",
                    label,
                    getattr(instance, "pk", None),
                    exc,
                )

        post_save.connect(_on_save, sender=model, weak=False)
        post_delete.connect(_on_delete, sender=model, weak=False)
        # Stash for introspection / test teardown.
        model._djust_notify_channel = effective
        model._djust_notify_receivers = (_on_save, _on_delete)
        return model

    # Bare @notify_on_save — called with the class directly.
    if isinstance(model_or_channel, type):
        return decorate(model_or_channel)

    # Positional channel string: @notify_on_save("orders")
    if isinstance(model_or_channel, str) and channel is None:
        channel = model_or_channel

    return decorate
