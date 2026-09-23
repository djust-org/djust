---
title: "Real-Time Presence Tracking"
slug: presence
section: guides
order: 2
level: intermediate
description: "Track online users and live cursors with PresenceMixin and LiveCursorMixin"
---

# Real-Time Presence Tracking

djust provides a presence system for tracking which users are currently viewing a page, with support for live cursors and collaborative features. Inspired by Phoenix LiveView's Presence.

## What You Get

- **PresenceMixin** -- Track user presence in any LiveView with join/leave callbacks
- **CursorTracker** -- Track and broadcast live cursor positions
- **LiveCursorMixin** -- Combined presence + cursor tracking in a single mixin
- **Stale-presence cleanup** -- Presences with no heartbeat for 60 seconds are pruned (see the known issue under [Best Practices](#best-practices): the client does not send heartbeats yet)

## Quick Start

### Minimal (v1.0.0rc12+): zero-config online count

For just an online-user counter, you don't need any custom context or
handlers — `PresenceMixin` auto-maintains `self.online_count` and
auto-broadcasts join/leave to all sessions of the same view:

```python
from djust import LiveView
from djust.presence import PresenceMixin

class DemoView(PresenceMixin, LiveView):
    template_name = 'demo.html'
    presence_key = "demo"
    # For anonymous-tab demos where two browser tabs of one user should
    # count as two presences (not collapse to one), opt in below:
    # presence_unique_per_connection = True

    def mount(self, request, **kwargs):
        self.track_presence()
```

```html
<span class="presence-chip">{{ online_count }} online</span>
```

That's the entire surface. `online_count` is set as an instance attribute
(so djust's diff dirty-tracking emits patches when it changes) and the
broadcast fans out to other sessions automatically. Open the page in two
browser tabs — both chips show `2 online`. Close one — the other drops to
`1 online` when the closed tab's WebSocket disconnects.

> **Note: HTTP vs WebSocket mounts.** `track_presence()` is a no-op
> during the HTTP-prerender phase of the page load — presence only
> registers when the WebSocket consumer mounts the view. This prevents
> orphan presence records on the throwaway HTTP view instance. No
> caller action is required; it's transparent.

### Full: presence with metadata and per-user avatars

```python
from djust import LiveView
from djust.presence import PresenceMixin

class DocumentView(PresenceMixin, LiveView):
    template_name = 'document.html'
    presence_key = "document:{doc_id}"

    def mount(self, request, **kwargs):
        self.doc_id = kwargs.get("doc_id")
        self.track_presence(meta={
            "name": request.user.username,
            "color": "#6c63ff",
        })

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["presences"] = self.list_presences()
        # `online_count` is already on `self` (v1.0.0rc12+); the
        # imperative `presence_count()` method is still available too.
        return ctx
```

### Display Presence in Templates

```html
<div class="presence-bar">
    {{ online_count }} users online
    {% for p in presences %}
        <span class="avatar" style="background: {{ p.meta.color }}">
            {{ p.meta.name.0 }}
        </span>
    {% endfor %}
</div>
```

### Anonymous tabs (`presence_unique_per_connection`)

By default, two browser tabs of the same anonymous user share a Django
session and therefore one presence id (`anon_<session_key>`) — the live
count stays at "1 online" no matter how many tabs you open. This is the
right semantics for an authenticated user collaborating with themselves
(same person, one identity), but wrong for a demo or a counter where
each tab should be counted independently.

Opt in to per-connection uniqueness for the anonymous path:

```python
class DemoView(PresenceMixin, LiveView):
    presence_key = "demo"
    presence_unique_per_connection = True   # anonymous tabs count distinctly
```

Authenticated users always use `request.user.id` regardless of the flag
— logged-in tabs still collapse to one identity (intentional).

Each presence record is `{"id": <user id>, "joined_at": <timestamp>, "meta": <the dict you passed to track_presence>}`, so your metadata lives under `meta`.

### 3. Handle Join/Leave Events

These callbacks run on the view that is itself joining or leaving, not on the
other users' sessions. Use them for per-session work such as a welcome message:

```python
class DocumentView(PresenceMixin, LiveView):
    def handle_presence_join(self, presence):
        self.push_event("flash", {
            "message": f"Welcome, {presence['meta']['name']}"
        })

    def handle_presence_leave(self, presence):
        self.push_event("flash", {
            "message": f"Goodbye, {presence['meta']['name']}"
        })
```

To react when *other* users join or leave (for example to flash "Alice
joined" to everyone else), override `_on_presence_change`, which fires on peer
sessions, call `super()._on_presence_change(**kwargs)`, and diff
`list_presences()` against the list you saw last time.

## PresenceMixin API

### Class Attributes

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `presence_key` | `str` or `None` | `None` | Group identifier. Supports format variables from view attributes (e.g., `"doc:{doc_id}"`). |
| `presence_unique_per_connection` *(v1.0.0rc12+)* | `bool` | `False` | When `True`, anonymous users get a per-WebSocket-connection unique id (`anon_conn_<ws_session_id>`) instead of a per-session id. Authenticated users always use `user.id` regardless. Use for anonymous-tab demos. |

### Instance Attributes (auto-maintained)

| Attribute | Type | Description |
|-----------|------|-------------|
| `online_count` *(v1.0.0rc12+)* | `int` | Number of active presences in the group. Auto-set by `track_presence`, `untrack_presence`, `_restore_presence`, and `_on_presence_change`. Use directly in templates: `{{ online_count }}`. |

### Methods

| Method | Description |
|--------|-------------|
| `track_presence(meta=None)` | Start tracking this user. Meta dict can include name, color, avatar, etc. No-op during HTTP-prerender (registers only under WebSocket). |
| `untrack_presence()` | Stop tracking. Called automatically on disconnect. |
| `list_presences()` | Returns all active presences in the group as a list of dicts. |
| `presence_count()` | Returns count of active users (imperative method; for template binding prefer `{{ online_count }}`). |
| `get_presence_key()` | Returns formatted presence key. Override for dynamic keys. |
| `get_presence_user_id()` | Returns unique user ID. Defaults to `request.user.id` for authenticated users, `anon_conn_<ws_session_id>` if `presence_unique_per_connection=True`, else `anon_<session_key>`. |
| `broadcast_to_presence(event, payload)` | Broadcast a custom event to all users in the group. |

### Callbacks

| Callback | When Called |
|----------|------------|
| `handle_presence_join(presence)` | This view's own `track_presence()` joins the group (runs on the joining session only) |
| `handle_presence_leave(presence)` | This view's own `untrack_presence()` leaves the group (runs on the leaving session only) |
| `_on_presence_change(**kwargs)` *(v1.0.0rc12+)* | Auto-fires on every other session when this view's `track`/`untrack` runs. Default body refreshes `online_count`. Override to do additional work; call `super()._on_presence_change(**kwargs)` to preserve the count refresh. |

## CursorTracker

Manages live cursor positions using Django's cache framework.

```python
from djust.presence import CursorTracker

# Update a cursor position
CursorTracker.update_cursor("doc:123", user_id, x=450, y=200, meta={"color": "#e74c3c"})

# Get all cursors for a group
cursors = CursorTracker.get_cursors("doc:123")
# Returns: {user_id: {x, y, timestamp, meta}}

# Remove a cursor
CursorTracker.remove_cursor("doc:123", user_id)
```

Cursors time out after 10 seconds to avoid showing stale positions.

## LiveCursorMixin

Combines `PresenceMixin` with cursor tracking for collaborative editing and whiteboard features.

```python
from djust import LiveView
from djust.presence import LiveCursorMixin
from djust.decorators import event_handler

class WhiteboardView(LiveCursorMixin, LiveView):
    template_name = 'whiteboard.html'
    presence_key = "whiteboard:{board_id}"

    def mount(self, request, **kwargs):
        self.board_id = kwargs.get("board_id")
        self.track_presence(meta={
            "name": request.user.username,
            "color": self.assign_color(),
        })

    @event_handler()
    def cursor_move(self, x=0, y=0, **kwargs):
        self.handle_cursor_move(int(x), int(y))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["cursors"] = self.get_cursors()
        return ctx

    def assign_color(self):
        colors = ["#e74c3c", "#3498db", "#2ecc71", "#f39c12", "#9b59b6"]
        return colors[self.presence_count() % len(colors)]
```

## Example: Chat Room with Online Users

```python
from djust import LiveView
from djust.presence import PresenceMixin

class ChatView(PresenceMixin, LiveView):
    template_name = 'chat.html'
    presence_key = "chat:{room_id}"

    def mount(self, request, **kwargs):
        self.room_id = kwargs.get("room_id")
        self.track_presence(meta={
            "name": request.user.username,
            "avatar": request.user.profile.avatar_url,
        })

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["online_users"] = self.list_presences()
        ctx["online_count"] = self.presence_count()
        return ctx
```

```html
<aside class="sidebar">
    <h3>Online ({{ online_count }})</h3>
    <ul class="user-list">
        {% for user in online_users %}
        <li>
            <img src="{{ user.meta.avatar }}" alt="{{ user.meta.name }}">
            <span>{{ user.meta.name }}</span>
        </li>
        {% endfor %}
    </ul>
</aside>
```

## Best Practices

- **Heartbeat**: A presence is stale, and pruned by the next `list_presences()` / count refresh, if no heartbeat arrives within 60 seconds (`PRESENCE_TIMEOUT`). **Known issue: #2968** — the client never sends the `presence_heartbeat` message, so at rc10 users drop out of the list about 60 seconds after joining even while still connected. Until it is fixed, refresh the heartbeat yourself (see the workaround below the list).
- **Cursor timeout**: Positions expire after 10 seconds. Use `CursorTracker` for high-frequency cursor updates.
- **Presence keys**: Use descriptive, hierarchical keys like `"document:{doc_id}"` or `"room:{room_id}"`. Format variables resolve from view attributes.
- **Cleanup**: Presences are removed automatically on WebSocket disconnect. Stale presences (missed heartbeats) are pruned when the group is next listed or counted.
- **Backend selection**: Use the memory backend for development, Redis for multi-server production deployments. Configure via `DJUST_CONFIG['PRESENCE_BACKEND']` (`'memory'` or `'redis'`) and `PRESENCE_REDIS_URL`.

Heartbeat workaround for #2968, refreshing from a tick:

```python
class DocumentView(PresenceMixin, LiveView):
    tick_interval = 30_000  # ms

    def handle_tick(self):
        self.update_presence_heartbeat()
```
