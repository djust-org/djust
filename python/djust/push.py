"""
Server-push API for djust LiveView.

Allows background tasks (Celery, management commands, cron jobs) to push
state updates to connected LiveView clients.
"""

import re

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

_VIEW_PATH_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+$")


def view_group_name(view_path: str) -> str:
    """Return the channel-layer group name for a view path."""
    return f"djust_view_{view_path.replace('.', '_')}"


def push_to_view(view_path, *, state=None, handler=None, payload=None):
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
    }
    async_to_sync(channel_layer.group_send)(group, message)


def push_event_to_view(view_path, event, payload=None):
    """
    Push an event directly to all clients connected to a LiveView.

    Unlike push_to_view (which updates state/calls handlers), this sends
    a raw event that client-side JS hooks can receive via handleEvent().

    Args:
        view_path: Dotted path to the view class
        event: Event name (e.g. "notification", "chart_update")
        payload: Dict of data to send with the event

    Example::

        from djust import push_event_to_view

        push_event_to_view("myapp.views.DashboardView",
                           "new_alert", {"message": "Server restarted"})
    """
    if not _VIEW_PATH_RE.match(view_path):
        raise ValueError(
            f"Invalid view_path: {view_path!r}. Expected dotted Python path like 'myapp.views.MyView'"
        )
    channel_layer = get_channel_layer()
    group = view_group_name(view_path)
    message = {
        "type": "client_push_event",
        "event": event,
        "payload": payload or {},
    }
    async_to_sync(channel_layer.group_send)(group, message)


async def apush_event_to_view(view_path, event, payload=None):
    """Async version of :func:`push_event_to_view`."""
    if not _VIEW_PATH_RE.match(view_path):
        raise ValueError(
            f"Invalid view_path: {view_path!r}. Expected dotted Python path like 'myapp.views.MyView'"
        )
    channel_layer = get_channel_layer()
    group = view_group_name(view_path)
    message = {
        "type": "client_push_event",
        "event": event,
        "payload": payload or {},
    }
    await channel_layer.group_send(group, message)


async def apush_to_view(view_path, *, state=None, handler=None, payload=None):
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
    }
    await channel_layer.group_send(group, message)
