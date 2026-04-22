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
    # Issue #810: PostgreSQL NOTIFY caps payload at 8000 bytes (documented
    # limit; some versions raise at 8191). Warn at ~4KB (half of that) so
    # callers can redesign before hitting the hard cap; drop above 7500B to
    # avoid a psycopg-level exception breaking the request path.
    size = len(body.encode("utf-8"))
    _NOTIFY_SOFT_LIMIT = 4_096
    _NOTIFY_HARD_LIMIT = 7_500
    if size > _NOTIFY_HARD_LIMIT:
        logger.error(
            "send_pg_notify(%s) payload %d bytes exceeds hard limit %d — "
            "DROPPING notification. Postgres NOTIFY caps payload at 8000 bytes. "
            "Redesign to send a lookup key the listener resolves lazily.",
            channel,
            size,
            _NOTIFY_HARD_LIMIT,
        )
        return
    if size > _NOTIFY_SOFT_LIMIT:
        logger.warning(
            "send_pg_notify(%s) payload %d bytes exceeds soft limit %d — "
            "nearing Postgres's 8000-byte NOTIFY cap. Consider sending an ID "
            "the listener resolves from the database instead of inlining the body.",
            channel,
            size,
            _NOTIFY_SOFT_LIMIT,
        )
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


def untrack(model: type) -> bool:
    """Disconnect the signal receivers previously wired by ``@notify_on_save``.

    Primarily for test teardowns (issue #809) — decorating a model with
    ``@notify_on_save`` registers two ``weak=False`` signal handlers
    against ``post_save`` / ``post_delete``, which would otherwise live
    for the process lifetime and fire during unrelated tests. Returns
    ``True`` if receivers were disconnected, ``False`` if the model was
    never decorated (idempotent / safe to call twice).

    After ``untrack``, the model loses its ``_djust_notify_channel`` /
    ``_djust_notify_receivers`` attributes so re-decorating works as if
    the model had never been touched.

    Example::

        @notify_on_save
        class Order(models.Model): ...

        # Later — in a pytest teardown or cleanup fixture:
        from djust.db import untrack
        untrack(Order)
    """
    receivers = getattr(model, "_djust_notify_receivers", None)
    if not receivers:
        return False
    on_save, on_delete = receivers
    post_save.disconnect(on_save, sender=model)
    post_delete.disconnect(on_delete, sender=model)
    # Wipe the introspection metadata so a fresh @notify_on_save round-
    # trips cleanly (re-decoration wires new receivers with a new
    # channel; leaving stale attributes would mask the re-wire).
    try:
        del model._djust_notify_channel
    except AttributeError:
        pass
    try:
        del model._djust_notify_receivers
    except AttributeError:
        pass
    return True
