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

A pushed `handler` must start with `handle_`, be decorated with `@event_handler`, or be marked `@push_handler`. Any other name is blocked (logged as `server_push: blocked handler`) and never called.

### Handlers only the server may call: `@push_handler`

A browser can send an event naming any method of your view. Under the default `event_security = "strict"` it is refused for a method without `@event_handler`, which is why an undecorated `handle_*` method works as a push target. Under `"warn"` and `"open"` the browser is not refused, so that method is also a browser event target.

`@push_handler` marks a method that only server push may call, in every `event_security` mode:

```python
from djust.decorators import push_handler

class RoomView(LiveView):
    @push_handler
    def refresh_room(self, room: str = "", **kwargs):
        self.rooms = load_rooms(room)
```

```python
push_to_view("games.views.RoomView", handler="refresh_room", payload={"room": "a"})
```

- The name does not need the `handle_` prefix.
- A browser event naming it is refused on the WebSocket, SSE and HTTP POST transports with the same response as a method that does not exist. It is not suggested in debug-mode "did you mean" hints, and the HTTP API answers `unknown_handler`.
- It is not an event handler: it does not appear in the view schema, `djust_audit` or the HTTP API, and `djust.V004` does not suggest decorating it. A template binding to it is reported by `djust.T019` in every mode.
- `@event_handler` or `@server_function` on the same method is a `TypeError` at decoration time. So are `@permission_required`, `@rate_limit`, `@debounce`, `@throttle`, `@cache`, `@optimistic` and `@client_state`: server push enforces none of them, so they would be inert and look like protection. Check authorization inside the handler. `@background` is allowed.
- **The marker is inherited by overrides.** If any class in the MRO (a base, a mixin) defines the name with `@push_handler`, an override without the marker is still push-only, and server push still reaches it. `djust.V021` reports the override; re-apply the marker to silence it. Adding `@event_handler` to the override does not make it browser-callable (V021 warns).
- It may sit above or below `@staticmethod` / `@classmethod`. Put it topmost among ordinary decorators.
- To test one, use `LiveViewTestClient.send_push("name", payload={...})`. `send_event` refuses it, as a browser is refused.
- Existing `handle_*` handlers keep working exactly as before; the marker is opt-in.

## Push from Celery Tasks

The most common use case is pushing updates from background workers. Install
Celery in your project (`pip install celery`) before using this example. Since
`push_to_view` is synchronous, it works directly inside any Celery task.

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

The receiver is named `handle_broadcast` because pushed handlers must start with `handle_` (or be `@event_handler`-decorated or `@push_handler`-marked). The sender skips its own broadcast, and peers receive it and update their state normally.

The skip covers pushes sent while the handler's turn runs. Work the handler starts that pushes later, after the turn is over (a `start_async` task, a pump or follower that a service it awaited launched), is news to the originating page too, so its pushes reach that page like anyone else's. (An asyncio task copies its context when it is created, so such a task used to keep the handler's tag, and its later pushes were skipped on the page that started it.)

### Async push handlers

A push handler may be `async def`. djust awaits it on the event loop, so it can use the async ORM or an async service directly; a sync handler runs off the loop through `sync_to_async`, as before. The same holds for `handle_info()`.

```python
class RunView(LiveView):
    async def handle_run_changed(self, run_id: str = "", **kwargs):
        self.run = await Run.objects.aget(pk=run_id)
```

## Scoped Push: One Room, Not Every Room

`push_to_view(view_path, ...)` reaches **every** session of the view class. For a view that serves many independent rooms (a game room, a document, a chat channel), a room's broadcast would then reach every session in every room. Each of those sessions runs the handler only to find the message is for another room, and sends a no-op frame back. The work grows with rooms × sessions.

Give each session a **push scope** instead, and push to one scope:

```python
from djust import LiveView, push_to_view

class RoomView(LiveView):
    template_name = "room.html"

    def mount(self, request, room="lobby", **kwargs):
        self.room = room
        self.push_scope = room  # this session receives pushes for its room

    def handle_refresh(self, **kwargs):
        self.players = load_players(self.room)

# Anywhere (a handler, a Celery task, a background loop):
push_to_view("games.views.RoomView", handler="handle_refresh", scope="room-42")
```

- `push_scope` is a `str` or an `int`, a list, tuple or set of them (a session can be in up to 64 scopes), or `None`, the default, which means view-wide pushes only.
- Set it in `mount()`. **Reassigning it moves the session.** djust joins the new scopes and leaves the dropped ones at the end of the turn that changed it: an event handler, a server-push hook, `handle_tick` or `handle_info`.
- `push_scope` is ordinary view state. It is restored with the session on reconnect, and a template can read it.
- `scope=` takes one `str` or `int`. `push_to_view` and `apush_to_view` both accept it.
- **A push without `scope` is unchanged**: it still reaches every session of the view, including sessions that set a `push_scope`.
- Scopes belong to one view path. `scope="room-42"` for `RoomView` does not reach another view class whose sessions use the same scope; push to each view path.
- **Scopes belong to one tenant.** A view that uses `TenantMixin` joins the scope groups of its own tenant (derived from `self.tenant.id`), so two tenants that both use `room-42` do not share a room. `push_to_view` and `apush_to_view` take `tenant=` (a tenant id or a `TenantInfo`) and default to the tenant bound to the running context: an event handler, a server-push hook and a `handle_info` turn of a tenant view each run with their own, so a push they send stays in that tenant. A Celery task or management command has none; pass `tenant=` from it. `tenant=None` reaches only views that are not tenant-scoped.
  - `tenant` only matters for a view that uses `TenantMixin`. A push to a view class that does not is sent unqualified whichever tenant is bound, so a tenant's handler, or an HTTP view under `TenantMiddleware`, still reaches the plain view's sessions. That check reads the class from the publishing process: from a process that has not imported the view (a Celery worker), pass `tenant=None` for a view that is not tenant-scoped, or `tenant=` for one that is. A scoped push from such a process that names no tenant and has none bound logs one warning per view path.
  - A tenant view whose tenant did not resolve receives no scoped push, and a scoped push to a tenant-scoped view with no tenant reaches no session. Each logs one warning (per consumer, per view path) that names no value.
  - A push without `scope` goes to the view-wide group, which has no tenant in its name, but every push is also stamped with the tenant it is for (`tenant=`, defaulting to the bound tenant), and a tenant view applies only a push stamped for its own tenant or for none. A tenant's handler that pushes view-wide therefore reaches that tenant's sessions only; a task with no tenant, or `tenant=None`, is a broadcast to every tenant. A view that is not tenant-scoped applies every push, and a message without a stamp (from a process on an older djust) is applied as before. A database NOTIFY (`handle_info`) carries no stamp: qualify the channel name with the tenant if tenants must not see each other's notifications.
- Scoped groups are ordinary channel-layer groups. They work across processes with the Redis channel layer, like view-wide push.
- WebSocket sessions only. SSE and HTTP-only sessions receive no server push, scoped or not.
- **Presence follows.** In a view that also uses `PresenceMixin`, a join or leave then wakes only the sessions that share the presence key, not every room (v1.3+; see [Presence](../guides/presence.md#multi-room-views-who-a-join-wakes)).

## Event Sequencing

Server pushes, ticks, and async completions are all treated as *background* updates. If a user event (click, submit, etc.) is in flight when a server push arrives, the push is buffered on the client and applied after the user event round-trip completes. This prevents version interleaving where a background update would silently discard the user's action.

On the server side, `server_push` acquires a render lock and yields to in-progress user events. A push that arrives while the session is busy (a user event or background result in progress, or the render lock held) is queued. As soon as the lock frees, every queued push is applied in order and the view renders once. So the last push of a change always reaches every viewer.

This is automatic and requires no developer action.

### A push is a message, not a call

`push_to_view()` and `apush_to_view()` return once the message is handed to the channel layer. The handler runs later, in each receiving session, possibly in another process, and later still in a session that was busy when the push arrived (it is queued, as above). So code after `await apush_to_view(...)` cannot read back state the pushed handler sets, even in the same process. Decide in the code that pushes, and give the handler finished values to copy into public state:

```python
from djust import apush_to_view


async def finish(run):
    """A background task: decide here, then push the result."""
    outcome = await decide(run)
    await apush_to_view(
        "runs.views.RunView",
        handler="handle_outcome",
        payload={"outcome": outcome},
        scope=run.owner_id,
    )
    if outcome == "send":
        await send(run)  # not: read back a flag the pushed handler sets
```

There is no awaitable for "the handler ran": a push reaches any number of sessions, or none.

### Reacting to a pushed render on the client

The client dispatches `djust:patched` on `window` after every frame that changed the page, whatever caused it: a server push, a tick, an async result or the page's own event. `event.detail` has `view` (the view path), `target_id` (the embedded view the frame updated, or `null`) and `source` (`"broadcast"` for a push, `"tick"`, `"async"`, or `"event"`). The DOM is already updated when it fires, so client code that re-syncs state it owns (a character counter, an `aria-describedby`, a control that a pushed render enables) listens to it instead of watching the DOM:

```javascript
window.addEventListener('djust:patched', () => syncComposer());
```

`djust:rendered` is different: it confirms that a view's own state rendered, and is not dispatched for a push (see [Error overlay](../guides/error-overlay.md#deferred-state-saves)).

## How It Works

1. When a client connects via WebSocket, the consumer joins a channel-layer group named `djust_view_<view_path>` (dots replaced with underscores). After mount it also joins one group per `push_scope` value, `djust_scope_<digest of view path and scope>` (`djust_tscope_<digest of view path, tenant and scope>` for a tenant view).
2. `push_to_view()` sends a message to the view's group, or to the scope's group when `scope=` is given, via Django Channels.
3. Each connected consumer receives the message, applies state updates and/or calls the handler, re-renders, and sends DOM patches to the client.

## Requirements

- Django Channels with a channel layer backend (Redis recommended for production).
- The `CHANNEL_LAYERS` setting must be configured in `settings.py`:

```python
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [{"address": "redis://127.0.0.1:6379", "socket_timeout": 20}],
        },
    },
}
```

Keep `socket_timeout` above 5 s; 20 is recommended. redis-py 8 lowered its default `socket_timeout` to 5 s, the same as channels_redis' blocking read, so without a longer timeout idle WebSockets drop every few seconds (django/channels_redis#422). Don't use `None`: a half-open connection then hangs until TCP keepalive notices. See [Deployment: Channel Layer](../guides/deployment.md#channel-layer-for-cross-process-push).

## API Reference

### `push_to_view(view_path, *, state=None, handler=None, payload=None, scope=None)`

Synchronous. Sends an update to all clients connected to `view_path`, or with `scope`, only to those whose view's `push_scope` includes it.

| Parameter   | Type   | Description                                   |
| ----------- | ------ | --------------------------------------------- |
| `view_path` | `str`  | Dotted import path of the LiveView class      |
| `state`     | `dict` | Attribute names and values to set on the view |
| `handler`   | `str`  | Name of a method to call on the view — must start with `handle_`, be decorated with `@event_handler`, or be marked `@push_handler`; other names are blocked |
| `payload`   | `dict` | Keyword arguments passed to the handler       |
| `scope`     | `str` or `int` | Only the sessions in this scope (see [Scoped Push](#scoped-push-one-room-not-every-room)); `None` reaches every session |
| `tenant`    | tenant id or `TenantInfo` | The tenant the push is for: with `scope`, whose scope group it goes to; without, which tenant's views apply it (`None` broadcasts). Defaults to the tenant bound to the running context (see [Scoped Push](#scoped-push-one-room-not-every-room)) |

### `apush_to_view(view_path, *, state=None, handler=None, payload=None, scope=None)`

Async version of `push_to_view`. Same parameters.

### `LiveView.push_scope`

The scope or scopes this session receives scoped pushes for: a `str`, an `int`, a list, tuple or set of them (at most 64), or `None` (the default). Usually set in `mount()`; reassigning it in a handler, push hook, `handle_tick` or `handle_info` moves the session.

### `LiveView.tick_interval`

Class attribute. Set to an integer (milliseconds) to enable periodic ticking.

### `LiveView.handle_tick()`

Override to update state on each tick. Called every `tick_interval` ms.

## Shared clocks

For one step per room rather than one tick per session, use the experimental
[shared room clock](../guides/shared-room-clock.md). It is process-local, tenant-scoped and
presence-bound, with a separate shared worker pool. Across event loops use
`MultiLoopInMemoryChannelLayer`; across processes route rooms to one owner.
Redis transport alone does not provide clock ownership.
