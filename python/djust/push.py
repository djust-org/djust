"""
Server-push API for djust LiveView.

Allows background tasks (Celery, management commands, cron jobs) to push
state updates to connected LiveView clients.
"""

import contextvars
import re
from typing import Any, Optional

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

_VIEW_PATH_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+$")

# Set by ``LiveViewConsumer.handle_event`` to the originating session's channel
# name while a user event is being handled (and reset afterward). When a handler
# calls ``push_to_view`` for its OWN view, the broadcast is tagged with this
# origin so the originating session can skip its own self-broadcast (#1677):
# that session's direct event response already reflects the state, and
# re-applying the redundant self-broadcast churns the single client-side VDOM
# version counter — under rapid event bursts that reads as non-sequential
# versions → a full-HTML ``request_html`` recovery storm + intermittent WS
# reconnect. The ContextVar is ``None`` outside event handling (Celery,
# management commands, cron, cross-view pushes), so those broadcasts are never
# suppressed. Worst case if the context doesn't propagate: the tag is ``None``
# and nothing is suppressed (no regression).
origin_channel: "contextvars.ContextVar[str | None]" = contextvars.ContextVar(
    "djust_push_origin_channel", default=None
)


def view_group_name(view_path: str) -> str:
    """Return the channel-layer group name for a view path."""
    return f"djust_view_{view_path.replace('.', '_')}"


def push_to_view(
    view_path: str,
    *,
    state: Optional[dict[str, Any]] = None,
    handler: Optional[str] = None,
    payload: Optional[dict[str, Any]] = None,
) -> None:
    """
    Push an update to all clients connected to a LiveView.

    Works from any synchronous context: Celery tasks, management commands,
    Django signals, cron jobs, etc.

    Args:
        view_path: Dotted path to the view class (e.g. "myapp.views.DashboardView")
        state: Dict of attribute names → values to set on the view instance
        handler: Name of a handler method to call on the view instance
        payload: Dict passed as kwargs to the handler method

    Raises:
        ValueError: If view_path is not a valid dotted Python path.

    Example::

        from djust import push_to_view

        # From a Celery task
        @shared_task
        def refresh_dashboard(new_count):
            push_to_view("myapp.views.DashboardView", state={"count": new_count})

        # Call a handler
        push_to_view("myapp.views.ChatView", handler="on_new_message",
                      payload={"text": "hello"})
    """
    if not _VIEW_PATH_RE.match(view_path):
        raise ValueError(
            f"Invalid view_path: {view_path!r}. Expected dotted Python path like 'myapp.views.MyView'"
        )
    channel_layer = get_channel_layer()
    group = view_group_name(view_path)
    message = {
        "type": "server_push",
        "state": state,
        "handler": handler,
        "payload": payload,
        # Originating session's channel (#1677), if pushed from within an event
        # handler — lets that session skip its redundant self-broadcast.
        "sender_channel": origin_channel.get(),
    }
    async_to_sync(channel_layer.group_send)(group, message)


async def apush_to_view(
    view_path: str,
    *,
    state: Optional[dict[str, Any]] = None,
    handler: Optional[str] = None,
    payload: Optional[dict[str, Any]] = None,
) -> None:
    """
    Async version of :func:`push_to_view`.

    Use from async contexts (async views, async Celery tasks, etc.).

    Raises:
        ValueError: If view_path is not a valid dotted Python path.
    """
    if not _VIEW_PATH_RE.match(view_path):
        raise ValueError(
            f"Invalid view_path: {view_path!r}. Expected dotted Python path like 'myapp.views.MyView'"
        )
    channel_layer = get_channel_layer()
    group = view_group_name(view_path)
    message = {
        "type": "server_push",
        "state": state,
        "handler": handler,
        "payload": payload,
        # Originating session's channel (#1677), if pushed from within an event
        # handler — lets that session skip its redundant self-broadcast.
        "sender_channel": origin_channel.get(),
    }
    await channel_layer.group_send(group, message)
