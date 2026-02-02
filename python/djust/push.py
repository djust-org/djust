"""
Server-push API for djust LiveView.

Allows background tasks (Celery, management commands, cron jobs) to push
state updates to connected LiveView clients.
"""

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


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
    channel_layer = get_channel_layer()
    group = f"djust_view_{view_path.replace('.', '_')}"
    message = {
        "type": "server_push",
        "state": state,
        "handler": handler,
        "payload": payload,
    }
    async_to_sync(channel_layer.group_send)(group, message)


async def apush_to_view(view_path, *, state=None, handler=None, payload=None):
    """
    Async version of :func:`push_to_view`.

    Use from async contexts (async views, async Celery tasks, etc.).
    """
    channel_layer = get_channel_layer()
    group = f"djust_view_{view_path.replace('.', '_')}"
    message = {
        "type": "server_push",
        "state": state,
        "handler": handler,
        "payload": payload,
    }
    await channel_layer.group_send(group, message)
