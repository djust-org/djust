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
- **One presence per user, however many tabs** -- Each open tab is its own connection; a user stays present until their last connection leaves (v1.3+)
- **Stale-presence cleanup** -- A connection with no heartbeat for 60 seconds is pruned. The client's connection ping (every 30 seconds) is the heartbeat

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
broadcast fans out to other sessions automatically. Open the page as two
different users — both chips show `2 online`. When one of them closes their
last tab, the other drops to `1 online` as that tab's WebSocket disconnects.

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
— logged-in tabs still collapse to one identity (intentional). Collapsing
means one entry in `list_presences()` and one count, not one tab: see
[One user, several tabs](#one-user-several-tabs-connections).

Each presence record is `{"id": <user id>, "joined_at": <timestamp>, "meta": <the dict you passed to track_presence>}`, so your metadata lives under `meta`.

### One user, several tabs (connections)

*(v1.3+)* Presence is stored **per connection** and reported **per user**. Each
view that calls `track_presence()` is one *connection* of its user in the room
(a browser tab, or each view of a `mount_batch`). A user is present from the
moment their first connection arrives until their last one leaves or times out:

- `list_presences()`, `presence_count()` and `online_count` have **one entry
  per user**, however many tabs that user has open.
- Closing or navigating away from one tab never removes the user while another
  tab is still open, and a peer never sees the count dip.
- `handle_presence_join` runs when the user's **first** connection arrives and
  `handle_presence_leave` when their **last** one leaves. A second tab of a user
  who is already present joins and leaves silently.
- Each connection has its own heartbeat and its own 60-second timeout: a tab
  that died is dropped on its own and the user's other tabs stay listed.
- Navigating to another page of the **same room** (`live_redirect`, a second
  `mount`) is not a leave and a rejoin. The new page joins as a second
  connection before the old page's connection is removed, so the user is never
  absent in between and no join or leave callback runs.
- When a user's tabs supply different `meta`, the record carries the `meta`
  of the connection that joined (or refreshed itself) last, and `joined_at` is
  the earliest connection's.

The record shape did not change: `{"id", "joined_at", "meta"}`. A timed-out
user (every connection stale) is removed without calling
`handle_presence_leave`, as before: there is no sweeper to run the hook.

Before v1.3 a record was keyed by `(room, user)`: a second tab collapsed onto
the first tab's record, so the first tab to close removed the user and a
same-room navigation made them leave and rejoin. If you worked around that by
giving each tab its own `get_presence_user_id()` and collapsing the tabs by name
when rendering, you can delete the workaround.

### 3. Handle Join/Leave Events

These callbacks run on the view that is itself joining or leaving, not on the
other users' sessions. They report the **user** arriving or leaving, not each
tab: `handle_presence_join` runs for a user's first connection and
`handle_presence_leave` for their last
([above](#one-user-several-tabs-connections)). Use them for per-session work
such as a welcome message:

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

### Multi-room views: who a join wakes

Each join or leave pushes `_on_presence_change` to peer sessions. In a view
that sets [`push_scope`](../advanced/server-push.md#scoped-push-one-room-not-every-room)
(one room per session), that push reaches **only the sessions that share this
session's presence key**, so a join in one room does not wake every other
room. Every WebSocket session of such a view is in the group of its own
presence key, including sessions that show `online_count` without calling
`track_presence()`. For a session that tracks, the key is the one
`track_presence()` used; for one that does not, it is worked out again
whenever its `push_scope` changes.

Sessions that share a presence key must agree on scoping. If only some
sessions of a view set `push_scope` (a lobby without one, rooms with one),
set `presence_broadcast_scoped = True` on the class so they all join.

A view without `push_scope` keeps the view-wide broadcast. Set
`presence_broadcast_scoped` to choose explicitly:

```python
class RoomView(PresenceMixin, LiveView):
    presence_key = "room:{room}"
    presence_broadcast_scoped = True   # scoped even without push_scope
    # presence_broadcast_scoped = False  # every session of the view, every room
```

Use `False` only when an `_on_presence_change` override must hear about joins
and leaves under *other* presence keys.

## PresenceMixin API

### Class Attributes

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `presence_key` | `str` or `None` | `None` | Group identifier. Supports format variables from view attributes (e.g., `"doc:{doc_id}"`). |
| `presence_broadcast_scoped` *(v1.3+)* | `bool` or `None` | `None` | Who a join or leave wakes. `None`: the sessions sharing the presence key when the view sets `push_scope`, otherwise every session of the view. `True`: the sessions sharing the presence key. `False`: every session of the view. |
| `presence_unique_per_connection` *(v1.0.0rc12+)* | `bool` | `False` | When `True`, anonymous users get a per-WebSocket-connection unique id (`anon_conn_<ws_session_id>`) instead of a per-session id. Authenticated users always use `user.id` regardless. Use for anonymous-tab demos. |

### Instance Attributes (auto-maintained)

| Attribute | Type | Description |
|-----------|------|-------------|
| `online_count` *(v1.0.0rc12+)* | `int` | Number of active presences in the group. Auto-set by `track_presence`, `untrack_presence`, `_restore_presence`, and `_on_presence_change`. Use directly in templates: `{{ online_count }}`. |

### Methods

| Method | Description |
|--------|-------------|
| `track_presence(meta=None)` | Start tracking this user. Meta dict can include name, color, avatar, etc. No-op during HTTP-prerender (registers only under WebSocket). |
| `untrack_presence()` | Stop tracking this view's connection. Called automatically on disconnect, navigation and view replacement. The user stays present while another of their connections is open. |
| `list_presences()` | Returns all active presences in the group as a list of dicts, one per user. |
| `presence_count()` | Returns count of active users (imperative method; for template binding prefer `{{ online_count }}`). |
| `get_presence_key()` | Returns formatted presence key. Override for dynamic keys. |
| `get_presence_user_id()` | Returns unique user ID. Defaults to `request.user.id` for authenticated users, `anon_conn_<ws_session_id>` if `presence_unique_per_connection=True`, else `anon_<session_key>`. |
| `broadcast_to_presence(event, payload)` | Broadcast a custom event to all users in the group. |

### Callbacks

| Callback | When Called |
|----------|------------|
| `handle_presence_join(presence)` | This view's own `track_presence()` makes the user present: it is the user's **first** connection (runs on the joining session only) |
| `handle_presence_leave(presence)` | This view's own `untrack_presence()` removes the user's **last** connection (runs on the leaving session only) |
| `_on_presence_change(**kwargs)` *(v1.0.0rc12+)* | Auto-fires on other sessions when this view's `track`/`untrack` runs: every session of the view, or only those sharing the presence key (see `presence_broadcast_scoped`). Default body refreshes `online_count`. Override to do additional work; call `super()._on_presence_change(**kwargs)` to preserve the count refresh. |

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

- **Heartbeat**: A connection is stale, and pruned by the next `list_presences()` / count refresh, if no heartbeat arrives within 60 seconds (`PRESENCE_TIMEOUT`). The client pings its WebSocket every 30 seconds and the server refreshes the presence connection of every view mounted on the socket on each ping, so a user stays listed while the page is open. A user is gone when every one of their connections is stale. (Before 1.2.1 nothing refreshed it and users dropped out after about a minute; #2968.) Browsers throttle timers in background tabs, so a tab hidden for several minutes can ping less often than every 30 seconds.
- **Cursor timeout**: Positions expire after 10 seconds. Use `CursorTracker` for high-frequency cursor updates.
- **Presence keys**: Use descriptive, hierarchical keys like `"document:{doc_id}"` or `"room:{room_id}"`. Format variables resolve from view attributes.
- **Cleanup**: A view's connection is removed automatically on WebSocket disconnect, on navigation and when the view is replaced. Stale connections (missed heartbeats) are pruned when the group is next listed or counted.
- **Backend selection**: Use the memory backend for development, Redis for multi-server production deployments. Configure via `DJUST_CONFIG['PRESENCE_BACKEND']` (`'memory'` or `'redis'`) and `PRESENCE_REDIS_URL`.

## Custom presence backends

`DJUST_CONFIG['PRESENCE_BACKEND']` takes `'memory'` or `'redis'` (and the
`tenant_*` aliases); the four built-in backends (`InMemoryPresenceBackend`,
`RedisPresenceBackend`, and the tenant-aware pair in `djust.tenants.backends`)
all store one record per connection. If you wrote your own `PresenceBackend`
subclass and install it with `set_presence_backend()`, nothing breaks:

*(v1.3+)* The per-connection methods have defaults on the base class
(`join_connection`, `leave_connection`, `heartbeat_connection`). They call your
existing `join(key, user_id, meta)`, `leave(key, user_id)` and
`heartbeat(key, user_id)` with no connection id, so a backend written against
the old contract keeps **one record per user**: a second tab collapses onto the
first, the first tab to close removes the user, and `handle_presence_join` /
`handle_presence_leave` run on every track and untrack, exactly as before.
That includes navigation: djust only holds a replaced view's untrack until
after the replacement mounts when the backend is per-connection
(`PresenceManager.per_connection()`, the `per_connection` class attribute). With
one record per user the order stays leave, mount, join, because the
replacement's join and the old view's leave would address the same record.

A subclass of a built-in backend that overrides `join`, `leave` or `heartbeat`
**without** a `connection_id` parameter is treated the same way (it is a
one-record-per-user backend, and your override is called as it always was). To
keep the built-in per-connection behaviour, give the override a
`connection_id=None` parameter and pass it on to `super()`.

To get per-connection presence in a custom backend, store one record per
`(presence_key, user_id, connection_id)` and override the three methods:

| Method | Contract |
|---|---|
| `join_connection(key, user_id, connection_id, meta)` | Add or refresh that connection (re-joining a live connection is not a new arrival). Return `(record, first)`: the user's aggregated record, and `True` when no live connection of the user was in the group. |
| `leave_connection(key, user_id, connection_id)` | Remove that connection. Return the user's aggregated record when it was their last live connection, else `None` (also `None` for an unknown connection). |
| `heartbeat_connection(key, user_id, connection_id)` | Refresh that connection only. Do not recreate one that expired. |
| `list(key)` / `count(key)` | One record per user, skipping connections with no heartbeat in the timeout. |

`djust.backends.base` has helpers for the aggregation
(`merge_connection_records`, `aggregate_by_user`, `connection_member`). The
built-in `join` / `leave` / `heartbeat` also accept an optional
`connection_id`; without one they address the user's single legacy connection
(`join`, `heartbeat`) or every connection of the user (`leave`, which is also
how an operator removes a user outright). Make `first` and `last` atomic if
several nodes share the store: the Redis backends read them in the same
`MULTI` that writes the connection.

**Upgrading Redis.** A record written by an older node (keyed by the bare user
id, no connection id) reads as that user's one legacy connection, so a rolling
deploy does not lose anyone. During the roll an old node lists a user once per
connection of a new node, so finish the deploy promptly.

A connection a stopped pod left behind lingers until its 60 s timeout beside the
connection the restored view makes. If the user closes that tab inside the
window they stay listed, with no `handle_presence_leave` and no peer
notification, until it expires. Expiry has never run the hook, and a rolling
deploy makes this routine for a minute, so expect it.
