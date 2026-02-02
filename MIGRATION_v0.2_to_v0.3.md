# Migration Guide: djust v0.2 → v0.3

## Overview

djust v0.3 is **fully backward compatible** with v0.2. Your existing code will work without changes. This guide covers how to adopt new features.

---

## ⚠️ Breaking Changes

**None.** All v0.2 APIs remain unchanged.

---

## 📦 New Dependencies

### Required (already dependencies)
- `channels` — already required for WebSocket support
- `django` ≥ 3.2

### Optional (for new features)
| Feature | Dependency | Install |
|---------|-----------|---------|
| Presence tracking | `channels` + cache backend | Already available |
| Streaming | `channels` (async) | Already available |
| File uploads | None (built-in) | — |
| Push from background | `channels` | Already available |

No new pip packages required.

---

## 🔄 Updating client.js

The client JavaScript has been expanded with 6 new modules. **You must rebuild/update the client bundle.**

```bash
# If building from source:
cd djust
make install   # Rebuilds everything including client.js

# Or manually:
cd python/djust/static/djust/src/
# Files are already concatenated in order (00-*.js through 18-*.js)
# The Django collectstatic will pick them up
python manage.py collectstatic --noinput
```

**New JS files added:**
- `09b-transitions.js` — `dj-transition` support
- `15-uploads.js` — File upload handling
- `16-optimistic-ui.js` — Optimistic UI updates
- `17-streaming.js` — Stream DOM updates
- `18-navigation.js` — `live_patch`/`live_redirect` client handling

**Template tag update** — If using `live_session()`, add the route map tag to your base template:

```html
{% load live_tags %}
{% djust_route_map %}
```

---

## ⚙️ New Settings & Config

### LIVEVIEW_CONFIG additions (all optional)

```python
# settings.py
LIVEVIEW_CONFIG = {
    # Existing settings still work...
    'use_websocket': True,
    'debug_vdom': False,
    'css_framework': 'bootstrap5',

    # New in v0.3 (all optional with sensible defaults):
    # No new required settings!
}
```

### Presence tracking
Requires a cache backend (Django's default `LocMemCache` works for development):

```python
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    }
}
# For production, use Redis or Memcached for cross-process presence
```

---

## 🚀 Step-by-Step Upgrade Guide

### Step 1: Update djust

```bash
pip install djust==0.3.0
# Or from source:
git checkout v0.3.0
make install
```

### Step 2: Rebuild client assets

```bash
python manage.py collectstatic --noinput
```

### Step 3: Verify existing views work

```bash
python manage.py test
```

Your existing `dj-click`, `dj-input`, `dj-change`, `dj-submit` handlers all work identically.

### Step 4: Adopt new features (optional)

Pick any features you want — all are opt-in:

#### Use `dj-model` for two-way binding
**Before (v0.2):**
```html
<input dj-input="update_search" />
```
```python
@event_handler
def update_search(self, value="", **kwargs):
    self.search_query = value
```

**After (v0.3):**
```html
<input dj-model="search_query" />
```
No handler needed — binding is automatic.

#### Use `dj-confirm` for dangerous actions
```html
<!-- Just add the directive -->
<button dj-click="delete" dj-confirm="Delete this item?">Delete</button>
```

#### Use `live_patch` for URL state
```python
@event_handler
def filter(self, category="all", **kwargs):
    self.category = category
    self.live_patch(params={"category": category})
```

#### Use LiveForm for validation
```python
from djust.forms import LiveForm

def mount(self, request, **kwargs):
    self.form = LiveForm({
        "email": {"required": True, "email": True},
    })
```

#### Use streaming for LLM/real-time content
```python
@event_handler
async def chat(self, message, **kwargs):
    async for token in llm_stream(message):
        self.response += token
        await self.stream_to("response", target="#output")
```

#### Use file uploads
```python
class MyView(LiveView, UploadMixin):
    def mount(self, request, **kwargs):
        self.allow_upload('file', accept='.pdf,.jpg', max_file_size=10_000_000)
```

#### Add presence tracking
```python
class MyView(LiveView, PresenceMixin):
    presence_key = "page:{page_id}"

    def mount(self, request, **kwargs):
        self.track_presence(meta={"name": request.user.username})
```

#### Use JS hooks for third-party libraries
```html
<div dj-hook="MapWidget" data-lat="{{ lat }}" data-lng="{{ lng }}"></div>
```
```javascript
djust.hooks.MapWidget = {
    mounted() { this.map = new Map(this.el, ...); },
    updated() { this.map.setCenter(...); }
};
```

#### Use testing utilities
```python
from djust.testing import LiveViewTestClient

def test_my_view(self):
    client = LiveViewTestClient(MyView)
    client.mount()
    client.send_event("increment")
    client.assert_state(count=1)
```

### Step 5: Update URL routing (if using live_session)

```python
# urls.py
from djust.routing import live_session

urlpatterns = [
    *live_session("/app", [
        path("", DashboardView.as_view(), name="dashboard"),
        path("settings/", SettingsView.as_view(), name="settings"),
    ]),
]
```

Add to your base template:
```html
{% load live_tags %}
{% djust_route_map %}
```

---

## ❓ FAQ

**Q: Will my v0.2 views break?**  
A: No. Zero breaking changes. All existing code works as-is.

**Q: Do I need to change my ASGI config?**  
A: No. The existing WebSocket consumer handles all new features.

**Q: Is `dj-model` a replacement for `dj-input`?**  
A: It's a higher-level alternative. `dj-input` still works and gives you more control. Use `dj-model` for simple bindings, `dj-input` when you need custom handler logic.

**Q: Do I need Redis for presence?**  
A: Only in production with multiple workers. `LocMemCache` works for development. Django's cache framework handles the abstraction.
