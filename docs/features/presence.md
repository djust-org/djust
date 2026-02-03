# Presence & Live Cursors

djust provides real-time presence tracking to show who's viewing a page, with optional live cursor tracking for collaborative features. Similar to Phoenix LiveView's Presence system.

## Core Concepts

- **`PresenceMixin`** — Track users viewing a page
- **`LiveCursorMixin`** — Track cursor positions for collaboration
- **Presence groups** — Isolate presence by document/room/page
- **Heartbeat cleanup** — Automatic removal of stale connections

## Basic Presence Tracking

```python
from djust import LiveView
from djust.presence import PresenceMixin
from djust.decorators import event_handler

class DocumentView(LiveView, PresenceMixin):
    template_name = "document.html"
    presence_key = "document:{doc_id}"  # Group identifier

    def mount(self, request, **kwargs):
        self.doc_id = kwargs.get("doc_id")
        self.doc = Document.objects.get(id=self.doc_id)
        
        # Track this user's presence
        self.track_presence(meta={
            "name": request.user.username,
            "color": self.get_user_color(request.user),
            "avatar": request.user.profile.avatar_url,
        })

    def get_user_color(self, user):
        # Generate consistent color from user ID
        colors = ["#6c63ff", "#ff6b6b", "#4ecdc4", "#45b7d1", "#96ceb4"]
        return colors[user.id % len(colors)]

    def get_context_data(self):
        ctx = super().get_context_data()
        ctx["presences"] = self.list_presences()
        ctx["presence_count"] = self.presence_count()
        return ctx
```

```html
<!-- document.html -->
<div class="document-header">
    <h1>{{ doc.title }}</h1>
    
    <!-- Presence indicator -->
    <div class="presence-bar">
        <span class="count">{{ presence_count }} viewing</span>
        <div class="avatars">
            {% for p in presences %}
                <span class="avatar" 
                      style="background: {{ p.meta.color }}"
                      title="{{ p.meta.name }}">
                    {{ p.meta.name|slice:":1"|upper }}
                </span>
            {% endfor %}
        </div>
    </div>
</div>

<style>
.presence-bar {
    display: flex;
    align-items: center;
    gap: 1rem;
}
.avatars {
    display: flex;
}
.avatar {
    width: 32px;
    height: 32px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    color: white;
    font-weight: bold;
    margin-left: -8px;
    border: 2px solid white;
}
.avatar:first-child {
    margin-left: 0;
}
</style>
```

## PresenceMixin API

### Class Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `presence_key` | `str` | Group identifier pattern. Supports `{attr}` interpolation |

### Methods

| Method | Description |
|--------|-------------|
| `track_presence(meta=None)` | Start tracking this user |
| `untrack_presence()` | Stop tracking this user |
| `list_presences()` | Get all active presences in group |
| `presence_count()` | Get count of active users |
| `update_presence_heartbeat()` | Refresh heartbeat timestamp |
| `broadcast_to_presence(event, payload)` | Send event to all in group |

### Callbacks

Override these methods to react to presence changes:

```python
def handle_presence_join(self, presence):
    """Called when a user joins the presence group"""
    self.push_event("notification", {
        "message": f"{presence['meta']['name']} joined",
        "type": "info"
    })

def handle_presence_leave(self, presence):
    """Called when a user leaves the presence group"""
    self.push_event("notification", {
        "message": f"{presence['meta']['name']} left",
        "type": "info"
    })
```

## Presence Keys

The `presence_key` defines which users see each other. Users with the same resolved key share a presence group.

```python
# Static key — all users on this view share presence
class GlobalChatView(LiveView, PresenceMixin):
    presence_key = "global_chat"

# Dynamic key — scoped to specific resource
class DocumentView(LiveView, PresenceMixin):
    presence_key = "document:{doc_id}"
    
    def mount(self, request, **kwargs):
        self.doc_id = kwargs["doc_id"]  # {doc_id} resolved from self.doc_id
        self.track_presence()

# Override for complex logic
class ProjectView(LiveView, PresenceMixin):
    def get_presence_key(self):
        if self.user.is_admin:
            return f"project:{self.project_id}:admin"
        return f"project:{self.project_id}:member"
```

## User Identification

By default, users are identified by:
1. `request.user.id` for authenticated users
2. Session key for anonymous users

Override `get_presence_user_id()` for custom identification:

```python
def get_presence_user_id(self):
    # Use a custom identifier
    return f"user:{self.request.user.email}"
```

## Presence Metadata

Include any metadata you want to display:

```python
self.track_presence(meta={
    "name": user.get_full_name(),
    "email": user.email,
    "avatar": user.profile.avatar_url,
    "color": "#6c63ff",
    "role": "editor",
    "cursor_enabled": True,
})
```

Access in templates:

```html
{% for p in presences %}
    <div class="user" style="border-color: {{ p.meta.color }}">
        <img src="{{ p.meta.avatar }}" />
        <span>{{ p.meta.name }}</span>
        <span class="role">{{ p.meta.role }}</span>
    </div>
{% endfor %}
```

## Live Cursors

Use `LiveCursorMixin` for real-time cursor position sharing — useful for collaborative editing, whiteboards, etc.

```python
from djust.presence import LiveCursorMixin

class WhiteboardView(LiveView, LiveCursorMixin):
    template_name = "whiteboard.html"
    presence_key = "whiteboard:{board_id}"

    def mount(self, request, **kwargs):
        self.board_id = kwargs["board_id"]
        self.track_presence(meta={
            "name": request.user.username,
            "color": self.get_user_color(request.user),
        })

    def handle_cursor_move(self, x, y):
        """Called when client sends cursor position"""
        # Cursor is automatically broadcast to others
        # Override for custom logic
        pass
```

### Client-Side Cursor Tracking

```javascript
// Track mouse movement (throttled to 10fps by default)
document.addEventListener('mousemove', (e) => {
    if (window.djust && window.djust.sendCursorMove) {
        djust.sendCursorMove(e.clientX, e.clientY);
    }
});

// Receive other users' cursor positions
document.addEventListener('djust:cursor_move', (e) => {
    const { user_id, x, y, meta } = e.detail.payload;
    showRemoteCursor(user_id, x, y, meta.color, meta.name);
});

// Example cursor display function
function showRemoteCursor(userId, x, y, color, name) {
    let cursor = document.getElementById(`cursor-${userId}`);
    if (!cursor) {
        cursor = document.createElement('div');
        cursor.id = `cursor-${userId}`;
        cursor.className = 'remote-cursor';
        cursor.innerHTML = `
            <svg width="24" height="24" viewBox="0 0 24 24">
                <path d="M5 2l14 14-6 2-2 6z" fill="${color}"/>
            </svg>
            <span class="name">${name}</span>
        `;
        document.body.appendChild(cursor);
    }
    cursor.style.left = x + 'px';
    cursor.style.top = y + 'px';
}
```

```css
.remote-cursor {
    position: fixed;
    pointer-events: none;
    z-index: 9999;
    transition: left 0.05s, top 0.05s;
}
.remote-cursor .name {
    position: absolute;
    left: 20px;
    top: 20px;
    background: var(--cursor-color, #333);
    color: white;
    padding: 2px 6px;
    border-radius: 3px;
    font-size: 12px;
    white-space: nowrap;
}
```

## Backend Configuration

### Using Django Cache (Default)

Presence data is stored in Django's cache backend:

```python
# settings.py
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': 'redis://127.0.0.1:6379/1',
    }
}
```

### Configuring Timeouts

```python
# djust/presence.py constants (override in settings)
DJUST_PRESENCE_HEARTBEAT_INTERVAL = 30  # seconds
DJUST_PRESENCE_TIMEOUT = 60  # stale after this time
DJUST_CURSOR_TIMEOUT = 10  # cursor positions expire faster
```

### Channels Integration

For real-time broadcasts, presence uses Django Channels groups:

```python
# settings.py
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [("127.0.0.1", 6379)],
        },
    },
}
```

## Complete Example: Collaborative Document

```python
# views.py
from djust import LiveView
from djust.presence import LiveCursorMixin
from djust.decorators import event_handler

class CollabDocumentView(LiveView, LiveCursorMixin):
    template_name = "collab_doc.html"
    presence_key = "doc:{doc_id}"

    def mount(self, request, **kwargs):
        self.doc_id = kwargs["doc_id"]
        self.doc = Document.objects.get(id=self.doc_id)
        self.content = self.doc.content
        
        # Generate unique color for this user
        colors = ["#e74c3c", "#3498db", "#2ecc71", "#9b59b6", "#f39c12"]
        color = colors[request.user.id % len(colors)]
        
        self.track_presence(meta={
            "name": request.user.username,
            "color": color,
            "avatar": request.user.profile.avatar_url,
        })

    def handle_presence_join(self, presence):
        self.push_event("user_joined", {
            "name": presence["meta"]["name"],
            "color": presence["meta"]["color"],
        })

    def handle_presence_leave(self, presence):
        self.push_event("user_left", {
            "name": presence["meta"]["name"],
        })

    @event_handler
    def update_content(self, content, **kwargs):
        self.content = content
        self.doc.content = content
        self.doc.save()
        
        # Broadcast change to other viewers
        self.broadcast_to_presence("content_updated", {
            "content": content,
            "editor": self.request.user.username,
        })

    def get_context_data(self):
        ctx = super().get_context_data()
        ctx["presences"] = self.list_presences()
        ctx["presence_count"] = self.presence_count()
        return ctx
```

```html
<!-- collab_doc.html -->
<div class="collab-editor">
    <header>
        <h1>{{ doc.title }}</h1>
        <div class="presence">
            <span>{{ presence_count }} editing</span>
            <div class="user-list">
                {% for p in presences %}
                <span class="user" style="background: {{ p.meta.color }}">
                    {{ p.meta.name|slice:":1"|upper }}
                </span>
                {% endfor %}
            </div>
        </div>
    </header>

    <textarea 
        dj-change="update_content"
        dj-debounce="500"
        name="content"
    >{{ content }}</textarea>
</div>

<!-- Remote cursors rendered by JS -->
<div id="cursors"></div>

<script>
// Handle incoming content updates from other users
document.addEventListener('djust:push_event', (e) => {
    if (e.detail.event === 'content_updated') {
        const textarea = document.querySelector('textarea');
        const currentPos = textarea.selectionStart;
        textarea.value = e.detail.payload.content;
        textarea.selectionStart = textarea.selectionEnd = currentPos;
    }
});

// Track and send cursor position
document.addEventListener('mousemove', (e) => {
    window.djust?.sendCursorMove?.(e.clientX, e.clientY);
});

// Render remote cursors
document.addEventListener('djust:cursor_move', (e) => {
    const { user_id, x, y, meta } = e.detail.payload;
    let cursor = document.getElementById(`cursor-${user_id}`);
    if (!cursor) {
        cursor = document.createElement('div');
        cursor.id = `cursor-${user_id}`;
        cursor.className = 'remote-cursor';
        cursor.style.setProperty('--color', meta.color);
        cursor.innerHTML = `<span>${meta.name}</span>`;
        document.getElementById('cursors').appendChild(cursor);
    }
    cursor.style.transform = `translate(${x}px, ${y}px)`;
});
</script>

<style>
.remote-cursor {
    position: fixed;
    pointer-events: none;
    z-index: 1000;
}
.remote-cursor::before {
    content: '';
    display: block;
    width: 0;
    height: 0;
    border: 8px solid transparent;
    border-left-color: var(--color);
    border-top-color: var(--color);
}
.remote-cursor span {
    background: var(--color);
    color: white;
    padding: 2px 6px;
    border-radius: 3px;
    font-size: 11px;
    margin-left: 12px;
}
</style>
```

## API Reference

### PresenceMixin

| Method/Attribute | Description |
|------------------|-------------|
| `presence_key` | Group identifier (supports `{attr}` interpolation) |
| `track_presence(meta)` | Start tracking user |
| `untrack_presence()` | Stop tracking |
| `list_presences()` | Get all users in group |
| `presence_count()` | Count users in group |
| `broadcast_to_presence(event, payload)` | Send to all in group |
| `handle_presence_join(presence)` | Callback on join |
| `handle_presence_leave(presence)` | Callback on leave |

### LiveCursorMixin (extends PresenceMixin)

| Method | Description |
|--------|-------------|
| `update_cursor_position(x, y)` | Update and broadcast cursor |
| `get_cursors()` | Get all cursor positions |
| `handle_cursor_move(x, y)` | Callback for cursor updates |

### Client-Side API

| Function/Event | Description |
|----------------|-------------|
| `djust.sendCursorMove(x, y)` | Send cursor position to server |
| `djust:cursor_move` | Event fired when cursor received |
