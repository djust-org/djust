# djust v0.3.0 — "Phoenix Rising" 🔥

**Release Date:** TBD  
**Codename:** Phoenix Rising  
**Branch:** `experimental/improvements`

> The biggest djust release yet — 13 major features bringing djust to full parity with Phoenix LiveView.

---

## ✨ New Features

### 1. Navigation & URL State (`live_patch` / `live_redirect`)
Full URL state management inspired by Phoenix LiveView. Update the browser URL without remounting, navigate between views over the same WebSocket, and handle browser back/forward.

```python
class ProductListView(LiveView):
    def handle_params(self, params, uri):
        self.category = params.get("category", "all")

    @event_handler
    def filter_results(self, category="all", **kwargs):
        self.category = category
        self.live_patch(params={"category": category, "page": 1})
```

```html
<a dj-patch="?category=electronics">Electronics</a>
<a dj-navigate="/items/42/">View Item</a>
```

**Routing with `live_session()`:**
```python
from djust.routing import live_session

urlpatterns = [
    *live_session("/app", [
        path("", DashboardView.as_view(), name="dashboard"),
        path("settings/", SettingsView.as_view(), name="settings"),
    ]),
]
```

📁 `python/djust/mixins/navigation.py`, `python/djust/routing.py`, `static/djust/src/18-navigation.js`

---

### 2. LiveForm — Standalone Form Validation
Declarative form validation with live inline feedback — no Django Form required. Inspired by Phoenix changesets.

```python
from djust.forms import LiveForm

class ContactView(LiveView):
    def mount(self, request, **kwargs):
        self.form = LiveForm({
            "name": {"required": True, "min_length": 2},
            "email": {"required": True, "email": True},
        })
```

Built-in validators: `required`, `min_length`, `max_length`, `pattern`, `email`, `url`, `min`, `max`, `choices`.  
Model integration: `live_form_from_model(Contact, exclude=["id"])`.

📁 `python/djust/forms.py` (29 tests)

---

### 3. Presence Tracking & Live Cursors
Real-time presence tracking showing who's viewing/editing a page, with optional collaborative cursor sharing.

```python
class DocumentView(LiveView, PresenceMixin):
    presence_key = "document:{doc_id}"

    def mount(self, request, **kwargs):
        self.track_presence(meta={"name": request.user.username, "color": "#6c63ff"})
```

Features: heartbeat-based cleanup, presence groups, join/leave events, `LiveCursorMixin` for mouse position sharing.

📁 `python/djust/presence.py`

---

### 4. Streaming — Real-time Partial DOM Updates
Token-by-token streaming for LLM chat, live feeds, progress indicators. Send incremental DOM updates during async handler execution.

```python
class ChatView(LiveView):
    @event_handler
    async def send_message(self, content, **kwargs):
        async for token in llm_stream(content):
            self.messages[-1]["content"] += token
            await self.stream_to("messages", target="#message-list")
```

Operations: `replace`, `append`, `prepend`, `delete`. Batched at ~60fps.

📁 `python/djust/streaming.py`, `static/djust/src/17-streaming.js`

---

### 5. File Uploads via WebSocket
Phoenix-style file uploads with progress tracking, drag-and-drop, magic byte validation, and chunked transfer.

```python
class ProfileView(LiveView, UploadMixin):
    def mount(self, request, **kwargs):
        self.allow_upload('avatar', accept='.jpg,.png', max_file_size=5_000_000)
```

📁 `python/djust/uploads.py`, `static/djust/src/15-uploads.js`

---

### 6. `dj-hook` — Client-Side JS Hooks
Register named JavaScript hooks with lifecycle callbacks (mounted, updated, destroyed, disconnected, reconnected).

```javascript
djust.hooks.Chart = {
    mounted() { this.chart = new Chart(this.el.querySelector('canvas'), {...}); },
    updated() { this.chart.update(...); },
    destroyed() { this.chart.destroy(); }
};
```

```html
<div dj-hook="Chart" data-values="{{ chart_data|json }}">...</div>
```

---

### 7. `dj-model` — Two-Way Data Binding
Automatic two-way binding between form inputs and server state.

```html
<input type="text" dj-model="search_query" placeholder="Search...">
<select dj-model="sort_by">...</select>
```

Options: `dj-model.lazy`, `dj-model.debounce-500`, `dj-model.trim`.

📁 `python/djust/mixins/model_binding.py`

---

### 8. `dj-confirm` — Confirmation Dialogs
Show `confirm()` before sending click events.

```html
<button dj-click="delete_item" dj-confirm="Are you sure?">Delete</button>
```

---

### 9. `dj-transition` — CSS Transitions on Mount/Remove
Apply CSS transition classes when elements enter/leave the DOM.

```html
<div dj-transition="fade">...</div>
```

Built-in presets: `fade`, `slide-up`, `slide-down`, `scale`.

📁 `static/djust/src/09b-transitions.js`

---

### 10. `dj-optimistic` — Optimistic UI Updates
Apply client-side state updates immediately before server confirmation, with automatic rollback on errors.

```html
<button dj-click="toggle_like" dj-optimistic="liked:!liked">❤️</button>
<button dj-click="increment" dj-optimistic="count:count+1">+1</button>
```

📁 `static/djust/src/16-optimistic-ui.js`

---

### 11. Push Events API
Server-to-client event pushing from handlers, plus external `push_event_to_view()` for background tasks.

```python
self.push_event("flash", {"message": "Saved!", "type": "success"})
```

```python
from djust.push import push_event_to_view
push_event_to_view("myapp.views.Dashboard", "refresh", {"data": new_data})
```

📁 `python/djust/mixins/push_events.py`, `python/djust/push.py`

---

### 12. Testing Utilities
Full testing toolkit: `LiveViewTestClient`, `ComponentTestClient`, `MockUploadFile`, snapshot testing, performance benchmarks.

```python
client = LiveViewTestClient(CounterView)
client.mount()
client.send_event('increment')
client.assert_state(count=1)
```

📁 `python/djust/testing.py`

---

### 13. Error Overlay (Development Mode)
Rich error overlay with syntax-highlighted tracebacks, event context, and suggested fixes. Like Next.js/Vite error overlays.

---

## 🔧 Improvements

### Enhanced `dj-loading` States
New modifiers: `.disable`, `.class="..."`, `.show`, `.remove`.

```html
<button dj-click="save" dj-loading.disable>Save</button>
<span dj-loading.show>Saving...</span>
```

### Universal `dj-debounce` and `dj-throttle`
Now works on **all** event types, not just form inputs.

```html
<button dj-click="save" dj-debounce="1000" />
<div dj-scroll="load_more" dj-throttle="100" />
```

### `dj-target` — Scoped DOM Updates
Reduce patch size by targeting specific DOM sections.

```html
<input dj-input="search" dj-target="#search-results" />
```

### `dj-change` Fires on Blur
Text inputs now fire `dj-change` on blur too, catching required field validation when users tab away.

---

## ⚠️ Breaking Changes

None! v0.3.0 is fully backward compatible with v0.2.x. All new features are additive.

---

## 📊 Stats

- **13 major features**
- **100+ new tests** across navigation, forms, presence, streaming, uploads, model binding, push events
- **6 new client-side JS modules** (uploads, optimistic UI, streaming, navigation, transitions, hooks)
- **7 server-side mixins** composing into the LiveView base class
