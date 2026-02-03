# Presence Tracking & Live Cursors

djust provides real-time presence tracking to show who's viewing or editing a page, with optional live cursor sharing for collaborative features.

## Quick Start

```python
from djust import LiveView, event_handler
from djust.presence import PresenceMixin

class DocumentView(LiveView, PresenceMixin):
    template_name = "document.html"
    presence_key = "document:{doc_id}"  # Group users by document

    def mount(self, request, **kwargs):
        self.doc_id = kwargs.get('doc_id')
        self.document = Document.objects.get(id=self.doc_id)
        
        # Start tracking this user's presence
        self.track_presence(meta={
            "name": request.user.username,
            "color": "#6c63ff",
            "avatar": request.user.avatar_url,
        })

    def get_context_data(self):
        ctx = super().get_context_data()
        ctx["presences"] = self.list_presences()
        ctx["presence_count"] = self.presence_count()
        return ctx
```

```html
<!-- document.html -->
<div class="presence-bar">
    <span class="count">{{ presence_count }} online</span>
    <div class="avatars">
        {% for p in presences %}
        <div class="avatar" 
             style="background: {{ p.color }}"
             title="{{ p.name }}">
            {{ p.name|first }}
        </div>
        {% endfor %}
    </div>
</div>

<div class="document-content">
    {{ document.content }}
</div>
```

## PresenceMixin

Add `PresenceMixin` to track who's viewing your LiveView.

### presence_key

Set `presence_key` to define the presence group. Users on views with the same key are grouped together.

```python
class DocumentView(LiveView, PresenceMixin):
    # Static key — all users of this view are grouped
    presence_key = "document_editors"

class DocumentView(LiveView, PresenceMixin):
    # Dynamic key with placeholder — grouped per document
    presence_key = "document:{doc_id}"
    
    def mount(self, request, **kwargs):
        self.doc_id = kwargs.get('doc_id')  # Placeholder gets value from self.doc_id
        self.track_presence()
```

You can also override `get_presence_key()` for complex logic:

```python
def get_presence_key(self):
    return f"project:{self.project.id}:document:{self.document.id}"
```

### track_presence()

Call `track_presence()` in `mount()` to start tracking:

```python
def mount(self, request, **kwargs):
    self.track_presence(meta={
        "name": request.user.username,
        "color": "#ff6b6b",
        "role": "editor",
        "avatar_url": request.user.avatar_url,
    })
```

The `meta` dict can contain any data you want to share with other users.

### untrack_presence()

Call `untrack_presence()` to stop tracking (automatic on view disconnect):

```python
@event_handler
def go_invisible(self):
    self.untrack_presence()
```

### list_presences()

Get all active users in the presence group:

```python
presences = self.list_presences()
# [
#     {"id": "user_1", "name": "Alice", "color": "#ff6b6b", ...},
#     {"id": "user_2", "name": "Bob", "color": "#4ecdc4", ...},
# ]
```

### presence_count()

Get the count of active users:

```python
count = self.presence_count()  # e.g., 3
```

## Presence Events

Override these methods to react when users join or leave:

```python
class DocumentView(LiveView, PresenceMixin):
    presence_key = "document:{doc_id}"

    def handle_presence_join(self, presence):
        """Called when a user joins."""
        self.push_event("flash", {
            "message": f"{presence['name']} joined",
            "type": "info"
        })

    def handle_presence_leave(self, presence):
        """Called when a user leaves."""
        self.push_event("flash", {
            "message": f"{presence['name']} left",
            "type": "info"
        })
```

## User Identification

By default, presence uses:
- `request.user.id` for authenticated users
- `anon_{session_key}` for anonymous users

Override `get_presence_user_id()` for custom identification:

```python
def get_presence_user_id(self):
    # Use a custom identifier
    return f"custom_{self.request.session.get('guest_id')}"
```

## Broadcast to Presence Group

Send events to all users in the presence group:

```python
@event_handler
def notify_all(self, message, **kwargs):
    self.broadcast_to_presence("notification", {
        "message": message,
        "from": self.request.user.username,
    })
```

## Live Cursors

For collaborative cursor tracking, use `LiveCursorMixin`:

```python
from djust.presence import LiveCursorMixin

class CollaborativeDocView(LiveView, LiveCursorMixin):
    template_name = "collab_doc.html"
    presence_key = "doc:{doc_id}"

    def mount(self, request, **kwargs):
        self.doc_id = kwargs.get('doc_id')
        self.track_presence(meta={
            "name": request.user.username,
            "color": self.assign_color(request.user.id),
        })

    @event_handler
    def cursor_move(self, x, y, **kwargs):
        """Called when user moves their cursor."""
        self.update_cursor_position(int(x), int(y))

    def assign_color(self, user_id):
        colors = ["#ff6b6b", "#4ecdc4", "#45b7d1", "#96c93d", "#f9ca24"]
        return colors[user_id % len(colors)]
```

```html
<!-- collab_doc.html -->
<div id="canvas" dj-mousemove="cursor_move">
    <!-- Render other users' cursors -->
    {% for user_id, cursor in cursors.items %}
    <div class="remote-cursor" 
         style="left: {{ cursor.x }}px; top: {{ cursor.y }}px; background: {{ cursor.meta.color }}">
        <span class="cursor-label">{{ cursor.meta.name }}</span>
    </div>
    {% endfor %}

    <div class="document-content">
        {{ document.content }}
    </div>
</div>

<style>
.remote-cursor {
    position: absolute;
    width: 16px;
    height: 16px;
    border-radius: 50%;
    pointer-events: none;
    transition: all 0.1s ease-out;
}
.cursor-label {
    position: absolute;
    left: 20px;
    top: -5px;
    background: inherit;
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 12px;
    white-space: nowrap;
}
</style>
```

### Get Cursor Positions

```python
def get_context_data(self):
    ctx = super().get_context_data()
    ctx["cursors"] = self.get_cursors()
    # {
    #     "user_1": {"x": 150, "y": 300, "meta": {"name": "Alice", "color": "#ff6b6b"}},
    #     "user_2": {"x": 400, "y": 200, "meta": {"name": "Bob", "color": "#4ecdc4"}},
    # }
    return ctx
```

## Backend Configuration

By default, presence data is stored in Django's cache. For production with multiple servers, use Redis:

```python
# settings.py
DJUST_CONFIG = {
    'presence_backend': 'redis',  # 'memory' (default) or 'redis'
}

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': 'redis://127.0.0.1:6379/1',
    }
}
```

### Heartbeat Configuration

Presence uses heartbeats to detect disconnected users:

```python
DJUST_CONFIG = {
    'presence_heartbeat_interval': 30,  # seconds (default)
    'presence_timeout': 60,              # stale after this many seconds (default)
}
```

## Full Example: Collaborative Editing

```python
from djust import LiveView, event_handler
from djust.presence import LiveCursorMixin

class CollaborativeEditor(LiveView, LiveCursorMixin):
    template_name = "editor.html"
    presence_key = "editor:{doc_id}"

    COLORS = ["#ff6b6b", "#4ecdc4", "#45b7d1", "#96c93d", "#f9ca24", "#a55eea"]

    def mount(self, request, **kwargs):
        self.doc_id = kwargs.get('doc_id')
        self.document = Document.objects.get(id=self.doc_id)
        self.content = self.document.content
        
        # Track presence with assigned color
        user_color = self.COLORS[request.user.id % len(self.COLORS)]
        self.track_presence(meta={
            "name": request.user.username,
            "color": user_color,
            "avatar": request.user.avatar_url,
        })

    def get_context_data(self):
        ctx = super().get_context_data()
        ctx["presences"] = self.list_presences()
        ctx["cursors"] = self.get_cursors()
        ctx["my_color"] = self._presence_meta.get("color")
        return ctx

    def handle_presence_join(self, presence):
        self.push_event("user_joined", {
            "name": presence.get("name"),
            "color": presence.get("color"),
        })

    def handle_presence_leave(self, presence):
        self.push_event("user_left", {
            "name": presence.get("name"),
        })

    @event_handler
    def cursor_move(self, x, y, **kwargs):
        self.update_cursor_position(int(x), int(y))

    @event_handler
    def update_content(self, content, **kwargs):
        self.content = content
        self.document.content = content
        self.document.save()
        
        # Broadcast content change to all users
        self.broadcast_to_presence("content_changed", {
            "content": content,
            "by": self.request.user.username,
        })
```

```html
<!-- editor.html -->
<div class="editor-container">
    <!-- Presence sidebar -->
    <aside class="presence-sidebar">
        <h3>{{ presences|length }} Editing</h3>
        <ul class="user-list">
            {% for p in presences %}
            <li>
                <span class="dot" style="background: {{ p.color }}"></span>
                {{ p.name }}
            </li>
            {% endfor %}
        </ul>
    </aside>

    <!-- Editor area -->
    <main class="editor-main" 
          dj-mousemove="cursor_move" 
          dj-throttle="50"
          style="position: relative">
        
        <!-- Remote cursors -->
        {% for user_id, cursor in cursors.items %}
        <div class="cursor" 
             style="left: {{ cursor.x }}px; top: {{ cursor.y }}px;">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="{{ cursor.meta.color }}">
                <path d="M0 0 L16 6 L6 16 Z"/>
            </svg>
            <span class="cursor-name" style="background: {{ cursor.meta.color }}">
                {{ cursor.meta.name }}
            </span>
        </div>
        {% endfor %}

        <!-- Editor -->
        <textarea 
            dj-input="update_content"
            dj-debounce="500"
            class="editor-textarea"
        >{{ content }}</textarea>
    </main>
</div>

<script>
// Handle server-pushed content changes
window.djust.hooks = {
    Editor: {
        mounted() {
            this.handleEvent("content_changed", ({content, by}) => {
                if (by !== document.body.dataset.username) {
                    this.el.querySelector('textarea').value = content;
                }
            });
        }
    }
};
</script>
```

## Tips

1. **Assign consistent colors**: Use user ID modulo a color list for consistent colors across sessions.

2. **Throttle cursor updates**: Use `dj-throttle="50"` on mousemove to limit updates to ~20/sec.

3. **Show "typing" indicators**: Track which input has focus and broadcast it as presence metadata.

4. **Handle reconnection**: Presence re-tracks automatically on WebSocket reconnect.

5. **Redis for production**: Use Redis backend when running multiple server processes.
