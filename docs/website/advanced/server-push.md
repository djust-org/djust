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
push_to_view("myapp.views.ChatView", handler="handle_new_message",
              payload={"text": "Hello from the server!"})
```

A pushed `handler` must start with `handle_` or be decorated with `@event_handler`. Any other name is blocked (logged as `server_push: blocked handler`) and never called.

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
            handler="handle_alert",
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

When multiple clients edit shared state (collaborative editing, shared dashboards), push each change to the view so every peer receives it. The session that made the change does not need special handling: when a handler pushes to its own view, the originating session automatically skips its own broadcast (#1677), because its direct event response already reflects the new state.

```python
from djust import LiveView, push_to_view
from djust.decorators import event_handler

class SharedNoteView(LiveView):
    template_name = "note.html"

    def mount(self, request, **kwargs):
        self.content = Note.objects.get(pk=kwargs["pk"]).content

    @event_handler
    def update_content(self, value: str = "", **kwargs):
        self.content = value
        Note.objects.filter(pk=self.kwargs["pk"]).update(content=value)
        push_to_view(
            "notes.views.SharedNoteView",
            handler="handle_broadcast",
            payload={"content": value},
        )

    def handle_broadcast(self, content: str = "", **kwargs):
        """Receive broadcast from a peer."""
        self.content = content
```

The receiver is named `handle_broadcast` because pushed handlers must start with `handle_` (or be `@event_handler`-decorated). The sender skips its own broadcast, and peers receive it and update their state normally.

## Event Sequencing

Server pushes, ticks, and async completions are all treated as *background* updates. If a user event (click, submit, etc.) is in flight when a server push arrives, the push is buffered on the client and applied after the user event round-trip completes. This prevents version interleaving where a background update would silently discard the user's action.

On the server side, `server_push` acquires a render lock and yields to in-progress user events. A push that arrives while the session is busy (a user event or background result in progress, or the render lock held) is queued. As soon as the lock frees, every queued push is applied in order and the view renders once. So the last push of a change always reaches every viewer.

This is automatic and requires no developer action.

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
| `handler`   | `str`  | Name of a method to call on the view — must start with `handle_` or be decorated with `@event_handler`; other names are blocked |
| `payload`   | `dict` | Keyword arguments passed to the handler       |

### `apush_to_view(view_path, *, state=None, handler=None, payload=None)`

Async version of `push_to_view`. Same parameters.

### `LiveView.tick_interval`

Class attribute. Set to an integer (milliseconds) to enable periodic ticking.

### `LiveView.handle_tick()`

Override to update state on each tick. Called every `tick_interval` ms.
