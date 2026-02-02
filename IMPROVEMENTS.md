# djust Framework Improvements

## Implemented in this branch

### ✨ Navigation & URL State — live_patch / live_redirect (LATEST)

**What:** Full URL state management inspired by Phoenix LiveView. Update the browser URL without remounting (`live_patch`), navigate between views over the same WebSocket (`live_redirect`), handle browser back/forward, and declarative template directives (`dj-patch`, `dj-navigate`). Includes `live_session()` for grouping views that share a WebSocket connection.

**Server-side API:**
```python
from djust import LiveView
from djust.decorators import event_handler

class ProductListView(LiveView):
    template_name = "products.html"

    def mount(self, request, **kwargs):
        # Read URL params on initial mount
        self.category = request.GET.get("category", "all")
        self.page = int(request.GET.get("page", 1))

    def handle_params(self, params, uri):
        # Called on browser back/forward and live_patch
        self.category = params.get("category", "all")
        self.page = int(params.get("page", 1))

    @event_handler
    def filter_results(self, category="all", **kwargs):
        self.category = category
        self.live_patch(params={"category": category, "page": 1})
        # URL updates to ?category=electronics&page=1 — no remount

    @event_handler
    def go_to_detail(self, item_id, **kwargs):
        self.live_redirect(f"/items/{item_id}/")
        # Navigates to new view over same WebSocket — no page reload
```

**Template directives:**
```html
<!-- Update URL params without remount -->
<a dj-patch="?category=electronics&page=1">Electronics</a>

<!-- Navigate to different view -->
<a dj-navigate="/items/42/">View Item</a>
```

**URL routing with live_session:**
```python
from djust.routing import live_session
from django.urls import path

urlpatterns = [
    *live_session("/app", [
        path("", DashboardView.as_view(), name="dashboard"),
        path("settings/", SettingsView.as_view(), name="settings"),
        path("items/<int:id>/", ItemDetailView.as_view(), name="item_detail"),
    ]),
]
```

**Features:**
- `live_patch(params, path, replace)` — update URL without remount, triggers re-render
- `live_redirect(path, params, replace)` — navigate to different view over same WebSocket
- `handle_params(params, uri)` — callback when URL params change (back/forward, live_patch)
- `dj-patch` / `dj-navigate` — declarative template directives
- Browser back/forward support via `popstate` event handling
- `live_session()` — group views sharing a WebSocket connection with client-side route map
- `{% djust_route_map %}` template tag for client-side view resolution

**Files:**
- `python/djust/mixins/navigation.py` — NavigationMixin (live_patch, live_redirect, handle_params)
- `python/djust/routing.py` — live_session(), get_route_map_script()
- `python/djust/static/djust/src/18-navigation.js` — Client-side navigation handling
- `python/tests/test_navigation.py` — 21 tests (17 pass, 4 skip without channels)

---

### ✨ LiveForm — Standalone Form Validation

**What:** A standalone `LiveForm` class for declarative form validation with live inline feedback — no Django Form required. Inspired by Phoenix LiveView changesets. Also includes `live_form_from_model()` for auto-generating forms from Django models, and enhanced `dj-change` to fire on blur for text inputs.

**Usage:**
```python
from djust.forms import LiveForm

class ContactView(LiveView):
    def mount(self, request, **kwargs):
        self.form = LiveForm({
            "name": {"required": True, "min_length": 2},
            "email": {"required": True, "email": True},
            "message": {"required": True, "min_length": 10, "max_length": 500},
        })

    @event_handler
    def validate(self, field=None, value=None, **kwargs):
        self.form.validate_field(field, value)

    @event_handler
    def submit_form(self, **kwargs):
        self.form.set_values(kwargs)
        if self.form.validate_all():
            # process self.form.data
            self.form.reset()
```

**Template:**
```html
<form dj-submit="submit_form">
    <input name="name" dj-change="validate" dj-debounce="300"
           class="{% if form.errors.name %}border-red-500{% endif %}" />
    {% if form.errors.name %}
        <span class="text-red-400 text-sm">{{ form.errors.name }}</span>
    {% endif %}
    <button type="submit" {% if not form.valid %}disabled{% endif %}>Send</button>
</form>
```

**Built-in validators:** `required`, `min_length`, `max_length`, `pattern`, `email`, `url`, `min`, `max`, `choices`
**Custom validators:** `"validators": [lambda v: "Error" if bad else None]`

**Model integration:**
```python
from djust.forms import live_form_from_model
form = live_form_from_model(Contact, exclude=["id", "created_at"])
```

**JS improvements:**
- `dj-change` now also fires on `blur` for text inputs (validates required fields even when user tabs away without typing)
- `dj-debounce` works on `dj-change` handlers

**Files:** `python/djust/forms.py` (LiveForm class), `python/tests/test_live_form.py` (29 tests)

---

### ✨ Presence Tracking and Live Cursors

**What:** Real-time presence tracking system showing who's viewing/editing a page, with optional live cursor tracking for collaborative features. Similar to Phoenix LiveView's Presence system.

**Usage:**
```python
# Basic presence tracking
class DocumentView(LiveView, PresenceMixin):
    presence_key = "document:{doc_id}"  # Group identifier
    
    def mount(self, request, **kwargs):
        self.doc_id = kwargs.get("doc_id")
        # Auto-track this user's presence
        self.track_presence(meta={
            "name": request.user.username,
            "color": "#6c63ff"
        })
    
    def get_context_data(self):
        ctx = super().get_context_data()
        ctx["presences"] = self.list_presences()  # List of active users
        ctx["presence_count"] = self.presence_count()  # Count
        return ctx
    
    def handle_presence_join(self, presence):
        """Called when a user joins"""
        self.push_event("flash", {
            "message": f"{presence['meta']['name']} joined",
            "type": "success"
        })
    
    def handle_presence_leave(self, presence):
        """Called when a user leaves"""
        self.push_event("flash", {
            "message": f"{presence['meta']['name']} left",
            "type": "info"
        })
```

**Template usage:**
```html
<div class="presence-bar">
  <h3>{{ presence_count }} user{{ presence_count|pluralize }} online</h3>
  <div class="user-avatars">
    {% for p in presences %}
      <span class="avatar" style="background: {{ p.meta.color }}"
            title="{{ p.meta.name }}">
        {{ p.meta.name.0|upper }}
      </span>
    {% endfor %}
  </div>
</div>
```

**Live cursors (bonus):**
```python
# Enhanced with live cursor tracking
class DocumentView(LiveView, LiveCursorMixin):
    presence_key = "document:{doc_id}"
    
    def handle_cursor_move(self, x, y):
        """Called when client sends cursor position"""
        # Automatically broadcasts to other users
        pass
```

**Client-side cursor tracking:**
```javascript
// Auto-sends throttled cursor positions (10fps)
document.addEventListener('mousemove', (e) => {
  djust.sendCursorMove(e.clientX, e.clientY);
});

// Receive other users' cursors
document.addEventListener('djust:cursor_move', (e) => {
  const { user_id, x, y, meta } = e.detail.payload;
  showCursor(user_id, x, y, meta.color, meta.name);
});
```

**Features:**
- **Real-time presence tracking** — See who's viewing the page
- **Heartbeat-based cleanup** — Auto-removes stale connections (60s timeout)
- **Presence groups** — Isolate presence by document/room/etc
- **User metadata** — Store name, color, avatar, etc
- **Join/leave events** — React to presence changes
- **Live cursors** — Optional real-time mouse position sharing
- **Channel isolation** — Uses Django Channels groups for scalability
- **Cache-based storage** — Efficient presence state management

**Architecture:**
- `PresenceManager` — Core presence logic and cache management
- `PresenceMixin` — LiveView mixin for basic presence tracking
- `LiveCursorMixin` — Enhanced mixin with cursor position tracking
- `CursorTracker` — Manages live cursor positions
- WebSocket integration for real-time updates
- Automatic cleanup of stale presences/cursors

**Demo:** See `examples/demo_project/djust_demos/demo_classes/presence.py` for live demos showing presence tracking and live cursors in action.

### 1. `dj-hook` — Client-Side JS Hooks (Phoenix-style)

**What:** Register named JavaScript hooks that fire lifecycle callbacks when elements are mounted, updated, or destroyed. Like Phoenix LiveView's `phx-hook`.

**Usage:**
```html
<div dj-hook="Chart" data-values="{{ chart_data|json }}">
  <canvas></canvas>
</div>
```

```javascript
djust.hooks.Chart = {
  mounted() {
    // this.el = the DOM element
    // this.pushEvent(name, params) sends event to server
    this.chart = new Chart(this.el.querySelector('canvas'), {
      data: JSON.parse(this.el.dataset.values)
    });
  },
  updated() {
    this.chart.update(JSON.parse(this.el.dataset.values));
  },
  destroyed() {
    this.chart.destroy();
  }
};
```

**Lifecycle callbacks:**
- `mounted()` — element inserted into DOM
- `updated()` — element's attributes changed via VDOM patch
- `destroyed()` — element removed from DOM
- `disconnected()` — WebSocket lost
- `reconnected()` — WebSocket restored

**Properties available in hooks:**
- `this.el` — the DOM element
- `this.pushEvent(name, params)` — send event to server
- `this.handleEvent(name, callback)` — listen for server-pushed events

### 2. `dj-model` — Two-Way Data Binding

**What:** Automatic two-way binding between form inputs and server state. Combines `dj-input` debouncing with automatic `name`→`value` synchronization.

**Usage:**
```html
<input type="text" dj-model="search_query" placeholder="Search...">
<select dj-model="sort_by">
  <option value="name">Name</option>
  <option value="date">Date</option>
</select>
<textarea dj-model="message"></textarea>
```

On the server, the view receives `update_model` events automatically:
```python
class MyView(LiveView):
    search_query = ""
    sort_by = "name"

    @event_handler
    def update_model(self, field, value):
        setattr(self, field, value)
```

**Options:**
- `dj-model.lazy` — only sync on blur (not every keystroke)
- `dj-model.debounce-500` — custom debounce (ms)
- `dj-model.trim` — trim whitespace

### 3. Error Overlay (Development Mode)

**What:** A rich error overlay (like Next.js/Vite) that displays server errors with full tracebacks, event context, and suggested fixes. Replaces the console-only error logging.

**Features:**
- Full-screen overlay with syntax-highlighted Python traceback
- Shows which event triggered the error
- Validation error details with field-level info
- Dismiss with Escape or click
- Stack frames with file paths
- Copy traceback to clipboard button

### 4. `dj-confirm` — Confirmation Dialog Before Click Events

**What:** Simple directive to show a browser `confirm()` dialog before sending a `dj-click` event. If the user cancels, the event is not sent.

**Usage:**
```html
<button dj-click="delete_item" dj-confirm="Are you sure you want to delete this?">Delete</button>
```

- Works with `dj-click` only (not `dj-change`, `dj-input`)
- Works on both initially-rendered elements and VDOM-patched elements

### 5. `dj-transition` — CSS Transition Classes on Mount/Remove

**What:** Applies CSS transition classes when elements are added to or removed from the DOM via VDOM patches. Inspired by Vue's `<Transition>`.

**Usage (named):**
```html
<div dj-transition="fade">...</div>
```
Applies: `fade-enter-from` → `fade-enter-to` on mount, `fade-leave-from` → `fade-leave-to` on remove.

**Usage (explicit classes):**
```html
<div dj-transition-enter="opacity-0" dj-transition-enter-to="opacity-100"
     dj-transition-leave="opacity-100" dj-transition-leave-to="opacity-0">
```

**Built-in presets** (in `transitions.css`): `fade`, `slide-up`, `slide-down`, `scale`

### 6. `dj-loading` Improvements

**What:** Enhanced loading state management with additional modifiers.

**Usage:**
```html
<button dj-click="save" dj-loading.disable>Save</button>
<div dj-loading.class="opacity-50">Content</div>
<span dj-loading.show>Saving...</span>
<div dj-loading.remove>Hide while loading</div>
```

- `.disable` — disable element while event is processing
- `.class="..."` — add CSS classes while loading
- `.show` — show element only while loading (hidden otherwise)
- `.remove` / `.hide` — hide element while loading

### 7. `dj-optimistic` — Optimistic UI Updates

**What:** Apply client-side state updates immediately before server confirmation, with automatic rollback on errors.

**Usage:**
```html
<button dj-click="toggle_like" dj-optimistic="liked:!liked">❤️</button>
<button dj-click="increment" dj-optimistic="count:count+1">+1</button>
<span dj-click="mark_read" dj-optimistic="class:read">Mark as read</span>
```

**Syntax:**
- `dj-optimistic="field:value"` — set field to literal value
- `dj-optimistic="field:!field"` — toggle boolean field
- Works with attributes, classes, text content, and data attributes

**Features:**
- Immediate DOM updates for better perceived performance
- Automatic rollback if server returns error or different state
- Flash notification on errors
- Works with `dj-target` for scoped updates

### 8. Universal `dj-debounce` and `dj-throttle`

**What:** Add debouncing and throttling to any event type, not just form inputs.

**Usage:**
```html
<input dj-input="search" dj-debounce="300" />
<button dj-click="save" dj-debounce="1000" />
<div dj-scroll="handle_scroll" dj-throttle="100" />
<input dj-keydown="validate" dj-debounce="500" />
```

**Features:**
- `dj-debounce="N"` — delay event N milliseconds, reset on new trigger
- `dj-throttle="N"` — limit to maximum once per N milliseconds
- Works with all event types: `dj-click`, `dj-input`, `dj-change`, `dj-keydown`, etc.
- Replaces existing smart defaults for form inputs
- Per-element timing control

### 9. `dj-target` — Scoped Updates

**What:** Specify which part of the DOM should be updated, reducing patch size for large pages.

**Usage:**
```html
<div id="search-results" dj-target>
  <!-- Only this section re-renders -->
</div>
<input dj-input="search" dj-target="#search-results" />

<section class="comments" dj-target>
  <!-- Comments area -->
</section>
<button dj-click="add_comment" dj-target=".comments">Add Comment</button>
```

**Features:**
- Scopes VDOM patches to specific DOM sections
- Reduces network payload and update time
- Works with any CSS selector
- Compatible with optimistic updates
- Server-side: patches automatically scoped to target element

**Server Integration:**
```python
class MyView(LiveView):
    @event_handler 
    def search(self, value):
        # Server automatically scopes patches to dj-target
        self.search_results = filter_results(value)
```

---

## Proposed (Not Yet Implemented)

### 10. Streaming — Real-time Partial DOM Updates

**What:** Send incremental DOM updates during async handler execution, without waiting for the handler to return. Perfect for LLM chat (token-by-token streaming), live feeds, progress indicators.

**Server API:**
```python
class ChatView(LiveView):
    @event_handler
    async def send_message(self, content, **kwargs):
        self.messages.append({"role": "user", "content": content})
        self.messages.append({"role": "assistant", "content": ""})

        # Stream tokens — each call sends a WebSocket message immediately
        async for token in llm_stream(content):
            self.messages[-1]["content"] += token
            await self.stream_to("messages", target="#message-list")

        # Also available:
        # await self.stream_insert("feed", "<li>New</li>", at="append")
        # await self.stream_delete("messages", "#msg-42")
        # await self.push_state()  # Full re-render, sent immediately
```

**Template:**
```html
<div id="message-list" dj-stream="messages">
    {% for msg in messages %}
        <div class="message">{{ msg.content }}</div>
    {% endfor %}
</div>
```

**Implementation:**
- `StreamingMixin` added to LiveView (server: `python/djust/streaming.py`)
- WebSocket consumer stores `_ws_consumer` ref on view for direct sends
- New message type: `{"type": "stream", "stream": "name", "ops": [...]}`
- Operations: `replace`, `append`, `prepend`, `delete`
- Batches rapid updates to ~60fps (16ms minimum interval)
- Client JS (`src/17-streaming.js`): applies DOM ops directly, auto-scrolls chat containers
- `push_state()` sends a full re-render mid-handler for non-stream state changes

**Files:**
- `python/djust/streaming.py` — StreamingMixin
- `python/djust/static/djust/src/17-streaming.js` — Client-side handler
- `python/tests/test_streaming.py` — Tests
- `examples/streaming_demo.py` — Chat demo with simulated typing

### Core Features
- `dj-submit` form handling improvements (nested forms, file inputs)
- Upload support (file uploads via WebSocket with progress)
- Dead view detection improvements (exponential backoff, offline indicator)
- Connection multiplexing (multiple views on one WebSocket)

### Performance
- Template fragment caching (only re-render changed sections)
- Lazy loading / virtual scrolling for large lists
- Batch multiple events into single round-trip
- ~~VDOM diff streaming (send patches as they're computed)~~ ✅ Implemented (see Streaming below)

### Developer Experience
- Component scaffolding CLI (`djust create component MyComponent`)
- TypeScript types for client-side JS API
- Storybook-like component preview mode
- Network inspector tab in debug panel

### Testing
- LiveView test client (mount + send events + assert state)
- Snapshot testing for VDOM diffs
- Performance benchmarking suite with CI integration
