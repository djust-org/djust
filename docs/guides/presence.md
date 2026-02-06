# Real-Time Presence Tracking

djust provides a presence system for tracking which users are currently viewing a page, with support for live cursors and collaborative features. Inspired by Phoenix LiveView's Presence.

## Overview

- **PresenceMixin** - Track user presence in any LiveView with join/leave callbacks
- **PresenceManager** - Application-wide presence state backed by configurable backends (memory, Redis)
- **CursorTracker** - Track and broadcast live cursor positions for collaborative editing
- **LiveCursorMixin** - Combined presence + cursor tracking in a single mixin
- **Automatic heartbeat** - Stale presences are cleaned up after timeout

## Quick Start

### 1. Add PresenceMixin to your view

```python
from djust import LiveView
from djust.presence import PresenceMixin

class DocumentView(PresenceMixin, LiveView):
    template_name = 'document.html'
    presence_key = "document:{doc_id}"  # Group key with format variable

    def mount(self, request, **kwargs):
        self.doc_id = kwargs.get("doc_id")
        # Start tracking this user's presence
        self.track_presence(meta={
            "name": request.user.username,
            "color": "#6c63ff",
        })

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["presences"] = self.list_presences()
        ctx["presence_count"] = self.presence_count()
        return ctx
```

### 2. Display presence in templates

```html
<div class="presence-bar">
    {{ presence_count }} users online
    {% for p in presences %}
        <span class="avatar" style="background: {{ p.color }}">
            {{ p.name.0 }}
        </span>
    {% endfor %}
</div>
```

### 3. Handle join/leave events

```python
class DocumentView(PresenceMixin, LiveView):
    # ...

    def handle_presence_join(self, presence):
        """Called when a user joins the presence group."""
        self.push_event("flash", {
            "message": f"{presence['name']} joined"
        })

    def handle_presence_leave(self, presence):
        """Called when a user leaves the presence group."""
        self.push_event("flash", {
            "message": f"{presence['name']} left"
        })
```

## API Reference

### PresenceMixin

Mix into any LiveView to add presence tracking.

#### Class Attributes

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `presence_key` | `str` or `None` | `None` | Presence group identifier. Supports format variables from view attributes (e.g., `"doc:{doc_id}"`). |

#### Methods

##### `track_presence(meta=None)`

Start tracking the current user's presence in the group.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `meta` | `dict` or `None` | `None` | Metadata to associate with the user (name, color, avatar, etc.). Defaults include `name` and `user_id` for authenticated users. |

##### `untrack_presence()`

Stop tracking the current user's presence. Called automatically on disconnect.

##### `list_presences() -> list[dict]`

Returns all active presences in this view's group. Each presence is a dict containing the metadata passed to `track_presence()`.

##### `presence_count() -> int`

Returns the count of active users in the group.

##### `update_presence_heartbeat()`

Manually update the heartbeat timestamp. The framework handles this automatically, but you can call it explicitly for custom heartbeat intervals.

##### `get_presence_key() -> str`

Returns the formatted presence key. Override for dynamic keys:

```python
def get_presence_key(self):
    return f"room:{self.room_id}"
```

##### `get_presence_user_id() -> str`

Returns the unique user identifier. Defaults to `request.user.id` for authenticated users, or `anon_{session_key}` for anonymous users. Override for custom identification.

##### `broadcast_to_presence(event, payload=None)`

Broadcast a custom event to all users in the presence group via the channel layer.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `event` | `str` | required | Event name |
| `payload` | `dict` or `None` | `None` | Event data |

#### Callbacks

##### `handle_presence_join(presence)`

Called when a user joins the group. Override to handle join events.

##### `handle_presence_leave(presence)`

Called when a user leaves the group. Override to handle leave events.

### PresenceManager

Static class for managing presence state across the application. Delegates to the configured backend.

| Method | Description |
|--------|-------------|
| `join_presence(presence_key, user_id, meta)` | Add a user to a group. Returns the presence record. |
| `leave_presence(presence_key, user_id)` | Remove a user. Returns the removed record or `None`. |
| `list_presences(presence_key)` | Get all active presences for a group. |
| `presence_count(presence_key)` | Count of active users. |
| `update_heartbeat(presence_key, user_id)` | Update heartbeat timestamp. |
| `presence_group_name(presence_key)` | Get the Channels group name for broadcasting. |

### CursorTracker

Manages live cursor positions for collaborative features. Uses Django's cache framework.

| Method | Description |
|--------|-------------|
| `update_cursor(presence_key, user_id, x, y, meta=None)` | Update cursor position. |
| `get_cursors(presence_key)` | Get all active cursor positions. Returns `{user_id: {x, y, timestamp, meta}}`. |
| `remove_cursor(presence_key, user_id)` | Remove a user's cursor. |

**Constants:**

| Constant | Value | Description |
|----------|-------|-------------|
| `CURSOR_TIMEOUT` | 10 seconds | Cursors older than this are cleaned up. |

### LiveCursorMixin

Extends `PresenceMixin` with cursor tracking. Use instead of `PresenceMixin` when you need live cursors.

| Method | Description |
|--------|-------------|
| `update_cursor_position(x, y)` | Update and broadcast this user's cursor position. |
| `get_cursors()` | Get all active cursors for the group. |
| `handle_cursor_move(x, y)` | Callback when cursor position received from client. Override for custom logic. |

## Examples

### "Users Online" Indicator

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
            <img src="{{ user.avatar }}" alt="{{ user.name }}">
            <span>{{ user.name }}</span>
        </li>
        {% endfor %}
    </ul>
</aside>
```

### Collaborative Cursors

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
        count = self.presence_count()
        return colors[count % len(colors)]
```

## Best Practices

### Heartbeat and Timeouts

- The default heartbeat interval is **30 seconds** and presence timeout is **60 seconds**. A user is considered stale if no heartbeat is received within the timeout.
- Cursor positions time out after **10 seconds** to avoid showing stale cursors.
- For high-frequency updates (like cursors), use `CursorTracker` which is optimized for frequent writes.

### Presence Cleanup

- Presences are automatically cleaned up when a WebSocket disconnects and `untrack_presence()` is called.
- Stale presences (missed heartbeats) are cleaned up periodically (every 5 minutes by default).
- `LiveCursorMixin` automatically removes cursor data when a user's presence is untracked.

### Presence Key Design

- Use descriptive, hierarchical keys: `"document:{doc_id}"`, `"room:{room_id}"`.
- Format variables are resolved from view instance attributes (e.g., `self.doc_id`).
- If no `presence_key` is set, defaults to the fully qualified class name.

### Backend Selection

- Use the **memory backend** for development and single-server deployments.
- Use the **Redis backend** for production multi-server deployments where presence must be shared across processes.
- Configure via `djust.backends.registry`.
