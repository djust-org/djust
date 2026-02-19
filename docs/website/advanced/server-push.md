---
title: "Server Push"
slug: server-push
section: advanced
order: 1
level: advanced
description: "Push real-time state updates to connected clients from Celery tasks, management commands, or any backend code."
---

# Server Push

Push state updates to connected LiveView clients from anywhere in your backend: Celery tasks, management commands, cron jobs, Django signals, and more.

## Quick Start

```python
from djust import push_to_view

# Update state on all connected clients
push_to_view("myapp.views.DashboardView", state={"visitors": 42})

# Call a handler method on all connected clients
push_to_view("myapp.views.ChatView", handler="on_new_message",
              payload={"text": "Hello from the server!"})
```

## Push from Celery Tasks

The most common use case is pushing updates from background workers. Since `push_to_view` is synchronous, it works directly inside any Celery task.

```python
from celery import shared_task
from djust import push_to_view

@shared_task
def refresh_metrics():
    count = Order.objects.filter(status="pending").count()
    push_to_view(
        "dashboard.views.MetricsView",
        state={"pending_orders": count},
    )
```

## Push from Management Commands

Useful for deployment notifications, maintenance windows, or system alerts.

```python
from django.core.management.base import BaseCommand
from djust import push_to_view

class Command(BaseCommand):
    def handle(self, *args, **options):
        push_to_view(
            "alerts.views.AlertView",
            handler="on_alert",
            payload={"level": "warning", "message": "Deploy starting"},
        )
```

## Async Push

For async contexts (async views, ASGI middleware, async Celery tasks), use `apush_to_view`:

```python
from djust import apush_to_view

async def notify_clients():
    await apush_to_view("myapp.views.FeedView", state={"new_items": True})
```

## Periodic Tick

For views that need to self-update on a schedule (dashboards, live feeds), set `tick_interval` and override `handle_tick()`:

```python
from djust import LiveView

class StockTickerView(LiveView):
    template_name = "ticker.html"
    tick_interval = 2000  # milliseconds

    def mount(self, request, **kwargs):
        self.price = get_current_price()

    def handle_tick(self):
        self.price = get_current_price()
```

The view re-renders and sends VDOM patches to all connected clients after each tick. No external task runner needed.

## The Broadcast Pattern

When multiple clients edit shared state (collaborative editing, shared dashboards), you need to broadcast changes to peers without triggering a render loop on the sender. The `_broadcast` / `_on_broadcast` pattern solves this.

```python
from djust import LiveView
from djust.decorators import event_handler

class SharedNoteView(LiveView):
    template_name = "note.html"

    def mount(self, request, **kwargs):
        self.content = Note.objects.get(pk=kwargs["pk"]).content
        self._skip_broadcast = False

    @event_handler
    def update_content(self, value: str = "", **kwargs):
        self.content = value
        Note.objects.filter(pk=self.kwargs["pk"]).update(content=value)
        self._broadcast({"content": value})

    def _broadcast(self, data):
        """Push to all peers. The sender skips re-render."""
        self._skip_broadcast = True
        push_to_view(
            "notes.views.SharedNoteView",
            handler="_on_broadcast",
            payload=data,
        )

    def _on_broadcast(self, content: str = "", **kwargs):
        """Receive broadcast from a peer."""
        if self._skip_broadcast:
            self._skip_broadcast = False
            self._skip_render = True
            return
        self.content = content
```

The `_skip_broadcast` flag prevents the sender from re-rendering its own update. Peers receive the broadcast and update their state normally.

## How It Works

1. When a client connects via WebSocket, the consumer joins a channel-layer group named `djust_view_<view_path>` (dots replaced with underscores).
2. `push_to_view()` sends a message to that group via Django Channels.
3. Each connected consumer receives the message, applies state updates and/or calls the handler, re-renders, and sends DOM patches to the client.

## Requirements

- Django Channels with a channel layer backend (Redis recommended for production).
- The `CHANNEL_LAYERS` setting must be configured in `settings.py`:

```python
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [("127.0.0.1", 6379)],
        },
    },
}
```

## API Reference

### `push_to_view(view_path, *, state=None, handler=None, payload=None)`

Synchronous. Sends an update to all clients connected to `view_path`.

| Parameter   | Type   | Description                                   |
| ----------- | ------ | --------------------------------------------- |
| `view_path` | `str`  | Dotted import path of the LiveView class      |
| `state`     | `dict` | Attribute names and values to set on the view |
| `handler`   | `str`  | Name of a method to call on the view          |
| `payload`   | `dict` | Keyword arguments passed to the handler       |

### `apush_to_view(view_path, *, state=None, handler=None, payload=None)`

Async version of `push_to_view`. Same parameters.

### `LiveView.tick_interval`

Class attribute. Set to an integer (milliseconds) to enable periodic ticking.

### `LiveView.handle_tick()`

Override to update state on each tick. Called every `tick_interval` ms.
