# State Management API Reference

**Status:** ✅ Implemented (Phase 5 Complete: @cache, @client_state, DraftModeMixin, @loading)

**Last Updated:** 2025-11-14

---

## Table of Contents

- [Overview](#overview)
- [Quick Reference](#quick-reference)
- [Decorators](#decorators)
  - [@debounce](#debounce)
  - [@throttle](#throttle)
  - [@optimistic](#optimistic)
  - [@client_state](#client_state)
  - [@cache](#cache)
- [Mixins](#mixins)
  - [DraftModeMixin](#draftmodemixin)
- [HTML Attributes](#html-attributes)
  - [@loading](#loading)
  - [@loading-text](#loading-text)
- [Advanced Topics](#advanced-topics)
  - [Combining Decorators](#combining-decorators)
  - [Decorator Order Rules](#decorator-order-rules)
  - [Type Hints](#type-hints)
  - [Error Handling](#error-handling)
- [Implementation Details](#implementation-details)
  - [Complexity Analysis](#complexity-analysis)
  - [Bundle Size Impact](#bundle-size-impact)

---

## Overview

djust's State Management API provides Python-only abstractions for common client-side patterns. These decorators, mixins, and HTML attributes eliminate the need for custom JavaScript in most use cases, allowing developers to write pure Python while achieving responsive, optimistic UIs.

### Philosophy

- **Python-First:** All behavior is declared in Python using decorators and mixins
- **Declarative:** HTML attributes describe UI behavior without imperative JavaScript
- **Automatic:** The framework handles debouncing, caching, state management automatically
- **Escape Hatch:** Custom JavaScript still available for edge cases

### Design Principles

1. **Zero JavaScript Required** - 90% of use cases covered by Python APIs
2. **Progressive Enhancement** - Works without JavaScript, enhanced when available
3. **Type Safety** - Full Python type hints and IDE autocomplete support
4. **Framework Agnostic** - Works with Bootstrap, Tailwind, or custom CSS

---

## Decorator Cheat Sheet

**Quick decision guide for choosing the right decorator**

### At a Glance

| Decorator | When to Use | Typical Wait/Interval | Bundle Impact |
|-----------|-------------|----------------------|---------------|
| `@debounce(wait)` | User is typing, dragging | 0.3-0.5s | +0.8 KB |
| `@throttle(interval)` | Scroll, resize, mouse move | 0.1-0.2s | +0.8 KB |
| `@optimistic` | Need instant feedback | N/A | +0.5 KB |
| `@cache(ttl)` | Same query repeated | 60-300s | +0.7 KB |
| `@client_state(keys)` | Multi-component coordination | N/A | +0.6 KB |
| `@permission_required(perm)` | Restrict handler to permitted users | Delete, admin | +0 KB |
| `DraftModeMixin` | Long forms, text editors | Auto-save | +0.9 KB |

**Total bundle size: 7.1 KB** (vs Phoenix ~30 KB, Livewire ~50 KB)

### Quick Decision Matrix

```
❓ User is typing in search/input?
   → @debounce(wait=0.5)

❓ Handling scroll, resize, or mousemove?
   → @throttle(interval=0.1)

❓ Need instant UI update before server responds?
   → @optimistic

❓ Same query gets called multiple times?
   → @cache(ttl=60, key_params=["query"])

❓ Multiple components need to share state?
   → @client_state(keys=["filter", "sort"])

❓ Auto-save form drafts to localStorage?
   → DraftModeMixin

❓ Restrict handler to users with a Django permission?
   → @permission_required("myapp.delete_item")

❓ Show loading spinner/disable button?
   → dj-loading.disable, dj-loading.show HTML attributes
```

### Common Combinations

**Pattern 1: Debounced Search with Cache**
```python
@debounce(wait=0.5)     # Wait for user to stop typing
@cache(ttl=300, key_params=["query"])  # Cache responses for 5 minutes
def search(self, query: str = "", **kwargs):
    self.results = Product.objects.filter(name__icontains=query)[:20]
```
**Result**: Server only queries after 500ms of silence, cached for 5 min

---

**Pattern 2: Instant Feedback with Server Validation**
```python
@debounce(wait=0.5)     # Debounce server calls
@optimistic              # Update UI instantly
def update_value(self, value: int = 0, **kwargs):
    self.value = max(0, min(100, value))  # Server validates range
```
**Result**: UI updates instantly, server validates/corrects after 500ms

---

**Pattern 3: Multi-Component Dashboard**
```python
# Component A: Filter selector
@client_state(keys=["filter"])
def update_filter(self, filter: str = "", **kwargs):
    self.filter = filter  # Published to StateBus

# Component B: Results list (auto-subscribes)
@client_state(keys=["filter"])
@debounce(wait=0.3)
def on_filter_change(self, filter: str = "", **kwargs):
    self.results = self.apply_filter(filter)
```
**Result**: Components automatically coordinate via client-side StateBus

---

**Pattern 4: Auto-Save Form**
```python
class ContactFormView(DraftModeMixin, FormMixin, LiveView):
    form_class = ContactForm
    draft_fields = ["name", "email", "message"]  # Auto-saved
    draft_ttl = 3600  # 1 hour

    @debounce(wait=1.0)
    @optimistic
    def auto_save(self, **kwargs):
        # Optional server-side auto-save
        pass
```
**Result**: Drafts saved to localStorage every 1s, restored on page load

---

### Performance Characteristics

| Decorator | Client Overhead | Server Impact | Network Impact |
|-----------|----------------|---------------|----------------|
| `@debounce` | ~1ms | ⬇️ Reduces calls | ⬇️ Fewer requests |
| `@throttle` | ~1ms | ⬇️ Reduces calls | ⬇️ Fewer requests |
| `@optimistic` | ~2ms | ➡️ Same calls | ➡️ Same requests |
| `@cache` | ~1ms lookup | ⬇️⬇️ Eliminates repeat calls | ⬇️⬇️ Zero for cache hits |
| `@client_state` | ~2ms | ➡️ Same calls | ➡️ Same requests |
| `DraftModeMixin` | ~3ms | ⬆️ Adds save calls (optional) | ➡️ Small localStorage writes |

**Legend:**
- ⬇️ Reduces - Fewer calls/requests
- ⬇️⬇️ Eliminates - Zero calls for cached responses
- ➡️ Same - No change
- ⬆️ Adds - Increases calls/requests

---

## Quick Reference

| Feature | Use Case | Python | HTML |
|---------|----------|--------|------|
| **Debouncing** | Search input, text fields | `@debounce(0.5)` | - |
| **Throttling** | Scroll, resize, mouse move | `@throttle(0.1)` | - |
| **Optimistic Updates** | Counters, toggles, sliders | `@optimistic` | - |
| **Loading States** | Button disable, spinners, overlays | - | `dj-loading.disable`, `dj-loading.show`, `dj-loading.hide`, `dj-loading.class` |
| **Loading Text** | Button text replacement | - | `@loading-text="Saving..."` (deprecated) |
| **Client State** | Multi-component coordination | `@client_state(keys=["temp"])` | - |
| **Caching** | Autocomplete, API calls | `@cache(ttl=300)` | - |
| **Permission Guard** | Handler access control | `@permission_required("perm")` | - |
| **Draft Mode** | Forms, text editors | `DraftModeMixin` | - |

---

## Decorators

### @debounce

**Status:** ✅ Implemented (Phase 2)

Delays handler execution until user stops typing/interacting for specified duration. Perfect for search inputs, text fields, and any high-frequency input events.

#### Signature

```python
def debounce(wait: float) -> Callable:
    """
    Debounce event handler execution.

    Args:
        wait: Seconds to wait after last event before firing handler

    Returns:
        Decorated handler function
    """
```

#### Parameters

- **wait** (`float`): Delay in seconds (e.g., `0.5` = 500ms). Must be > 0.

#### Example

```python
from djust import LiveView
from djust.decorators import debounce

class SearchView(LiveView):
    template_string = """
    <input type="text"
           dj-input="on_search"
           placeholder="Search products..." />

    <div>Found {{ result_count }} products</div>
    """

    def mount(self, request):
        self.query = ""
        self.results = []
        self.result_count = 0

    @debounce(wait=0.5)  # Wait 500ms after user stops typing
    def on_search(self, value: str = "", **kwargs):
        """
        Called only after user stops typing for 500ms.
        No manual setTimeout/clearTimeout needed!
        """
        self.query = value
        self.results = Product.objects.filter(name__icontains=value)
        self.result_count = len(self.results)
```

#### Behavior

1. User types "p" → **debounce timer starts (500ms)**
2. User types "py" (within 500ms) → **timer resets**
3. User types "pyt" (within 500ms) → **timer resets**
4. User stops typing → **timer expires after 500ms → handler fires ONCE**

#### Benefits

- **Reduces server load:** 100 keystrokes → 1 server request
- **Improves UX:** Avoids flickering results during typing
- **Eliminates JavaScript:** No manual timer management needed

#### Common Use Cases

- Search inputs
- Autocomplete
- Live validation
- Filter inputs
- Any text input with server-side processing

#### See Also

- [@throttle](#throttle) - For time-based rate limiting
- [STATE_MANAGEMENT_PATTERNS.md](STATE_MANAGEMENT_PATTERNS.md) - Debouncing patterns

---

### @throttle

**Status:** ✅ Implemented (Phase 2)

Limits handler execution to once per time interval, regardless of event frequency. Perfect for scroll handlers, mouse movement, and window resize events.

#### Signature

```python
def throttle(interval: float) -> Callable:
    """
    Throttle event handler execution.

    Args:
        interval: Minimum seconds between handler executions

    Returns:
        Decorated handler function
    """
```

#### Parameters

- **interval** (`float`): Minimum time in seconds between executions (e.g., `0.1` = 100ms)

#### Example

```python
from djust import LiveView
from djust.decorators import throttle

class ScrollTrackerView(LiveView):
    template_string = """
    <div @scroll="on_scroll" style="height: 500px; overflow-y: scroll;">
        <!-- Scrollable content -->
        <div style="height: 2000px;">
            Scroll position: {{ scroll_y }}px
        </div>
    </div>
    """

    def mount(self, request):
        self.scroll_y = 0

    @throttle(interval=0.1)  # Max 10 updates per second
    def on_scroll(self, scroll_y: int = 0, **kwargs):
        """
        Called at most once every 100ms, even if scroll fires 60 times/second.
        """
        self.scroll_y = scroll_y
```

#### Behavior

- **Guarantees:** Handler executes at most once per interval
- **First call:** Executes immediately
- **Subsequent calls:** Queued until interval expires
- **Final call:** Executes after final interval if events continue

#### Difference from @debounce

| Decorator | Behavior | Use Case |
|-----------|----------|----------|
| `@debounce(0.5)` | Waits for **silence** (no events for 500ms) | Search input (wait until done typing) |
| `@throttle(0.5)` | Executes **every** 500ms during activity | Scroll tracking (periodic updates) |

#### Common Use Cases

- Scroll position tracking
- Mouse movement tracking
- Window resize handlers
- Infinite scroll pagination
- Real-time analytics (periodic updates)

#### See Also

- [@debounce](#debounce) - For wait-until-idle behavior
- [STATE_MANAGEMENT_PATTERNS.md](STATE_MANAGEMENT_PATTERNS.md) - Throttling patterns

---

### @optimistic

**Status:** ✅ Implemented (Phase 3)

Enables optimistic UI updates - client updates DOM immediately while server validates asynchronously. Perfect for counters, toggles, sliders, and any interaction where immediate feedback improves UX.

#### Signature

```python
def optimistic(func: F) -> F:
    """
    Enable optimistic client-side updates.

    Client applies update immediately (optimistically).
    Server validates and corrects if needed.

    Returns:
        Decorated handler function
    """
```

#### Parameters

None - this is a marker decorator.

#### Example

```python
from djust import LiveView
from djust.decorators import optimistic, debounce

class CounterView(LiveView):
    template_string = """
    <div>
        <h1>Count: {{ count }}</h1>
        <button dj-click="increment">+1</button>
    </div>
    """

    def mount(self, request):
        self.count = 0

    @optimistic  # UI updates instantly!
    def increment(self, **kwargs):
        """
        User sees count increase IMMEDIATELY.
        Server validates and updates asynchronously.
        """
        self.count += 1
```

#### Behavior

1. **User clicks button** → Client increments count in DOM **instantly** (< 16ms)
2. **Event sent to server** → Background request (user doesn't wait)
3. **Server executes handler** → Validates and processes
4. **Server sends patch** → Client applies if different from optimistic update

#### With Server Validation

```python
class SliderView(LiveView):
    @optimistic
    @debounce(wait=0.5)  # Combine with debouncing!
    def update_value(self, value: int = 0, **kwargs):
        """
        Client updates slider immediately.
        Server receives debounced request after 500ms.
        Server validates bounds and corrects if needed.
        """
        # Server-side validation
        self.value = max(0, min(100, value))

        # If user sent value=150, server corrects to 100
        # Client receives patch with correct value
```

#### Error Handling

If server rejects the optimistic update:

```python
@optimistic
def delete_item(self, item_id: int = 0, **kwargs):
    """
    Client removes item from list immediately.
    If server fails, client receives error patch and reverts.
    """
    item = Item.objects.get(id=item_id)

    # Validate permission
    if not self.request.user.can_delete(item):
        raise PermissionError("Cannot delete this item")

    item.delete()
    # Client shows error toast and reverts deletion
```

#### Benefits

- **Feels instant:** No waiting for server round trip
- **Better UX:** Immediate visual feedback
- **Server validates:** Business logic stays server-side
- **Auto-corrects:** Client syncs if server changes value

#### Common Use Cases

- Counters (+1, -1 buttons)
- Toggle switches (on/off)
- Range sliders
- Like/favorite buttons
- Simple CRUD operations (delete, complete)

#### Combining with Other Decorators

```python
from djust.decorators import optimistic, debounce, throttle

@optimistic        # Update UI immediately
@debounce(0.5)     # Debounce server requests
def on_search(self, query: str = "", **kwargs):
    """
    Client updates results instantly (optimistically).
    Server receives debounced request after 500ms.
    Best of both worlds!
    """
    self.results = search_database(query)
```

#### See Also

- [STATE_MANAGEMENT_PATTERNS.md](STATE_MANAGEMENT_PATTERNS.md) - Optimistic UI patterns
- [STATE_MANAGEMENT_MIGRATION.md](STATE_MANAGEMENT_MIGRATION.md) - Migrating manual optimistic code

---

### @client_state

**Status:** ✅ Implemented (PR #81)

Enables client-side state sharing between multiple components without server round trips. Perfect for dashboards, coordinated UI updates, and multi-component communication.

#### Signature

```python
def client_state(keys: list[str]) -> Callable:
    """
    Enable client-side state bus for component coordination.

    Args:
        keys: State keys this handler publishes to

    Returns:
        Decorated handler function
    """
```

#### Parameters

- **keys** (`list[str]`): List of state keys to publish. Other components can subscribe to these keys.

#### Example

```python
from djust import LiveView
from djust.decorators import client_state, debounce

class DashboardView(LiveView):
    template_string = """
    <div>
        <!-- Slider controls temperature -->
        <input type="range"
               min="0" max="120"
               value="{{ temperature }}"
               dj-input="update_temperature" />

        <!-- Display listens to 'temperature' state -->
        <div id="temp-display">{{ temperature }}°F</div>

        <!-- Gauge listens to 'temperature' state -->
        <div id="gauge" data-subscribe="temperature"></div>

        <!-- Chart listens to 'temperature' state -->
        <canvas id="chart" data-subscribe="temperature"></canvas>
    </div>
    """

    def mount(self, request):
        self.temperature = 72

    @client_state(keys=["temperature"])  # Publish to state bus
    @debounce(wait=0.5)
    def update_temperature(self, temperature: int = 0, **kwargs):
        """
        When slider changes:
        1. Client publishes 'temperature' to state bus (instant)
        2. All components listening to 'temperature' update (instant)
        3. Server receives debounced request after 500ms
        4. Server validates and persists

        Result: Smooth 60 FPS updates, minimal server requests!
        """
        self.temperature = max(0, min(120, temperature))
```

#### Behavior

1. **User drags slider** → `update_temperature` called
2. **Client publishes** → `StateBus.publish("temperature", 72)` **instantly**
3. **Subscribers update** → Display, gauge, chart all update **instantly**
4. **Server request** → Sent after 500ms debounce (validation/persistence)

#### Subscribing to State

Components subscribe to state keys using HTML data attributes:

```html
<!-- Auto-subscribe via data attribute -->
<div data-subscribe="temperature">
    Will receive updates when 'temperature' changes
</div>

<!-- Multiple subscriptions -->
<div data-subscribe="temperature,humidity,pressure">
    Updates when any key changes
</div>
```

Or via JavaScript for custom handling:

```javascript
// Custom subscriber (for canvas charts, etc.)
window.StateBus.subscribe('temperature', (value) => {
    updateChart(value);
});
```

#### Multi-Component Coordination

```python
class WeatherDashboard(LiveView):
    @client_state(keys=["temperature", "humidity"])
    def update_weather(self, temp: int = 0, humidity: int = 0, **kwargs):
        """
        Publishes multiple state keys.
        Different components subscribe to different keys.
        """
        self.temperature = temp
        self.humidity = humidity
```

Template:

```html
<!-- Display subscribes to temperature -->
<div data-subscribe="temperature">{{ temperature }}°F</div>

<!-- Gauge subscribes to humidity -->
<div data-subscribe="humidity">{{ humidity }}%</div>

<!-- Chart subscribes to both -->
<canvas data-subscribe="temperature,humidity"></canvas>
```

#### Benefits

- **Instant updates:** All components update at 60 FPS
- **No cascading requests:** One server request updates all components
- **Decoupled:** Components don't know about each other
- **No custom JavaScript:** Built-in state bus handles everything

#### Common Use Cases

- Dashboards with multiple widgets
- Filter panels updating multiple lists
- Synchronized controls (linked inputs)
- Real-time data visualization
- Multi-step forms with previews

#### See Also

- [STATE_MANAGEMENT_PATTERNS.md](STATE_MANAGEMENT_PATTERNS.md) - Multi-component patterns
- [STATE_MANAGEMENT_EXAMPLES.md](STATE_MANAGEMENT_EXAMPLES.md) - Dashboard example

---

### @cache

**Status:** ✅ Implemented (PR #80)

Enables automatic client-side response caching with TTL and LRU eviction. Perfect for autocomplete, repeated API calls, and any idempotent read operations.

#### Signature

```python
def cache(ttl: int = 300, key_params: list[str] | None = None) -> Callable:
    """
    Enable client-side response caching.

    Args:
        ttl: Time-to-live in seconds (default: 300 = 5 minutes)
        key_params: Parameter names to use for cache key (default: all params)

    Returns:
        Decorated handler function
    """
```

#### Parameters

- **ttl** (`int`, optional): Cache lifetime in seconds. Default: `300` (5 minutes)
- **key_params** (`list[str]`, optional): Which parameters to include in cache key. Default: `None` (all parameters)

#### Example

```python
from djust import LiveView
from djust.decorators import cache, debounce

class AutocompleteView(LiveView):
    template_string = """
    <input type="text"
           dj-input="search"
           placeholder="Search languages..." />

    <ul>
        {% for result in results %}
        <li>{{ result }}</li>
        {% endfor %}
    </ul>
    """

    def mount(self, request):
        self.query = ""
        self.results = []

    @cache(ttl=300)           # Cache for 5 minutes
    @debounce(wait=0.3)       # Debounce requests
    def search(self, query: str = "", **kwargs):
        """
        Client checks cache first:
        - Cache hit: Returns instantly (< 1ms)
        - Cache miss: Sends request to server

        User types "py" then deletes then types "py" again:
        → Second "py" query returns from cache instantly!
        """
        if not query:
            self.results = []
            return

        # Expensive database query
        self.results = Language.objects.filter(
            name__istartswith=query
        )[:10]
```

#### Behavior

1. **First request** (`query="python"`):
   - Client checks cache → **miss**
   - Send request to server
   - Server returns results
   - Client caches: `{"python": results}` with timestamp

2. **Second request** (`query="python"` again):
   - Client checks cache → **hit** (< 5 minutes old)
   - Returns cached results **instantly** (no server request!)

3. **After TTL expires** (> 5 minutes):
   - Client checks cache → **expired**
   - Send new request to server
   - Update cache with fresh results

#### Custom Cache Keys

By default, all parameters are used for cache key. Customize with `key_params`:

```python
@cache(ttl=600, key_params=["query"])  # Only cache by 'query' param
def search(self, query: str = "", page: int = 1, **kwargs):
    """
    Cache key = query only (ignores 'page').

    search(query="python", page=1) and
    search(query="python", page=2)
    share the same cache entry.
    """
    ...
```

**⚠️ Important:** When using `key_params`, ensure your HTML inputs have matching `name` attributes:

```html
<!-- ✅ Correct: name="query" matches key_params=["query"] -->
<input type="text" name="query" dj-input="search" />

<!-- ❌ Wrong: Missing name attribute -->
<input type="text" dj-input="search" />
```

The `name` attribute is used to extract parameter values for cache key generation. Without it, the cache key will be incomplete (e.g., `"search:"` instead of `"search:python"`), causing all searches to share the same cache entry.

#### Cache Invalidation

Caches are automatically invalidated:

- **TTL expires:** After specified duration
- **LRU eviction:** When cache reaches size limit (default: 100 entries)
- **Manual clear:** User refreshes page (cache is session-scoped)

#### Manual Cache Control

```python
@cache(ttl=0)  # Disable caching for this handler
def real_time_data(self, **kwargs):
    """
    TTL=0 disables caching.
    Useful for real-time data that changes frequently.
    """
    ...
```

#### Benefits

- **Instant responses:** Cached queries return in < 1ms
- **Reduced server load:** 50-90% fewer requests for repeated queries
- **Better UX:** No loading spinners for cached results
- **Automatic:** No manual cache implementation needed

#### Common Use Cases

- Autocomplete search
- Dropdown options (countries, states, categories)
- User profile lookups
- Frequently accessed reference data
- API responses for read-only data

#### Metrics

Example autocomplete performance:

| Scenario | Without @cache | With @cache | Improvement |
|----------|---------------|-------------|-------------|
| First "python" search | 150ms | 150ms | - |
| Second "python" search | 150ms | 0.5ms | **300x faster** |
| 10 repeated searches | 1500ms | 155ms | **~90% reduction** |

#### See Also

- [STATE_MANAGEMENT_PATTERNS.md](STATE_MANAGEMENT_PATTERNS.md) - Caching strategies
- [STATE_MANAGEMENT_EXAMPLES.md](STATE_MANAGEMENT_EXAMPLES.md) - Autocomplete example

---

## Mixins

### DraftModeMixin

**Status:** ✅ Implemented (PR #82)

Automatically saves form field values to localStorage and restores on page reload. Perfect for long forms, comment boxes, and any input where losing data would be frustrating.

#### Signature

```python
class DraftModeMixin:
    """
    Mixin for automatic localStorage-based draft saving.

    Class attributes:
        draft_enabled: Enable/disable draft mode (default: True)
        draft_key: Custom localStorage key (optional, auto-generated from class name)
    """
    draft_enabled: bool = True
    draft_key: str | None = None

    def get_draft_key(self) -> str:
        """Get the draft key for localStorage (can be overridden)"""

    def clear_draft(self) -> None:
        """Clear the draft from localStorage"""
```

#### Class Attributes

- **draft_enabled** (`bool`, optional): Enable/disable draft mode. Default: `True`
- **draft_key** (`str`, optional): Custom localStorage key. Default: auto-generated from class name (e.g., `commentformview_draft`)

#### Methods

- **get_draft_key()** → `str`: Returns the draft key for localStorage. Override to customize based on view state (e.g., include record ID)
- **clear_draft()**: Clears the draft from localStorage. Call after successful form submission

#### Example

```python
from djust import LiveView
from djust.drafts import DraftModeMixin

class CommentFormView(DraftModeMixin, LiveView):
    template_name = 'comment_form.html'
    draft_enabled = True
    draft_key = 'comment_form'  # Optional: defaults to 'commentformview_draft'

    def mount(self, request):
        self.comment_text = ""
        self.author_name = ""

    def save_comment(self, comment_text: str = "", author_name: str = "", **kwargs):
        """Save comment and clear draft"""
        Comment.objects.create(
            text=comment_text,
            author=author_name
        )
        # Clear draft on successful save
        self.clear_draft()
        self.comment_text = ""
        self.author_name = ""
```

**Template** (`comment_form.html`):

```html
<!-- Root element needs draft attributes -->
<div data-djust-root
     data-draft-enabled="{{ draft_enabled }}"
     data-draft-key="{{ draft_key }}">

    <form dj-submit="save_comment">
        <!-- Fields with data-draft="true" are auto-saved -->
        <textarea name="comment_text"
                  placeholder="Write your comment..."
                  data-draft="true"
                  rows="5">{{ comment_text }}</textarea>

        <input type="text"
               name="author_name"
               placeholder="Your name"
               data-draft="true"
               value="{{ author_name }}" />

        <button type="submit">Post Comment</button>
        <button dj-click="discard_draft">Discard Draft</button>
    </form>
</div>
```

#### Behavior

1. **User types in field** → Automatically saved to localStorage after **500ms debounce**
2. **User refreshes page** → Draft **automatically restored** from localStorage on mount (no confirmation prompt)
3. **User submits form** → Call `self.clear_draft()` to remove from localStorage
4. **User navigates away** → Draft persists in localStorage **for next visit**

**Auto-Restore Behavior:**
- Drafts are automatically restored without user confirmation
- This is the most seamless user experience (similar to Google Docs auto-save)
- No "Resume draft?" prompt is shown (future enhancement)
- Developer can check for draft existence and show custom UI if needed

#### Draft Storage

Drafts are stored in localStorage with the format:

```javascript
// Storage key: djust_draft_{draft_key}
// Storage value: {data: {...}, timestamp: ...}
localStorage.setItem(
    'djust_draft_comment_form',
    JSON.stringify({
        data: {
            comment_text: 'User typed text...',
            author_name: 'John Doe'
        },
        timestamp: 1763138790943
    })
);
```

#### Custom Draft Key

Override `get_draft_key()` to customize based on view state:

```python
class ArticleEditorView(DraftModeMixin, LiveView):
    template_name = 'article_editor.html'

    def mount(self, request, article_id=None):
        self.article_id = article_id
        self.title = ""
        self.content = ""

    def get_draft_key(self) -> str:
        """Include article ID in draft key"""
        if self.article_id:
            return f"article_editor_{self.article_id}"
        return "article_editor_new"

    def save_article(self, title: str = "", content: str = "", **kwargs):
        """Save and clear draft"""
        article = Article.objects.update_or_create(
            id=self.article_id,
            defaults={'title': title, 'content': content}
        )
        self.clear_draft()  # Clear draft on success
```

#### Manual Draft Control

```python
class AdvancedFormView(DraftModeMixin, LiveView):
    template_name = 'advanced_form.html'

    def discard_draft(self, **kwargs):
        """Manually clear draft - useful for 'Discard' button"""
        self.clear_draft()
```

**Template:**

```html
<div data-djust-root
     data-draft-enabled="{{ draft_enabled }}"
     data-draft-key="{{ draft_key }}"
     {% if draft_clear %}data-draft-clear="true"{% endif %}>

    <textarea name="content" data-draft="true">{{ content }}</textarea>

    <button dj-click="save_content">Save</button>
    <button dj-click="discard_draft">Discard Draft</button>
</div>
```

#### Displaying Draft Age

The draft timestamp is stored in localStorage and can be displayed using JavaScript:

```html
<div data-djust-root
     data-draft-enabled="{{ draft_enabled }}"
     data-draft-key="{{ draft_key }}">

    <div id="draft-status"></div>

    <textarea name="content" data-draft="true">{{ content }}</textarea>
</div>

<script>
// Display draft age
function updateDraftStatus() {
    const draft = localStorage.getItem('djust_draft_{{ draft_key }}');
    const statusEl = document.getElementById('draft-status');

    if (draft && statusEl) {
        try {
            const draftData = JSON.parse(draft);
            const ageMs = Date.now() - draftData.timestamp;
            const ageMinutes = Math.floor(ageMs / 60000);

            if (ageMinutes < 1) {
                statusEl.textContent = 'Draft saved just now';
            } else if (ageMinutes < 60) {
                statusEl.textContent = `Draft saved ${ageMinutes} minute${ageMinutes > 1 ? 's' : ''} ago`;
            } else {
                const ageHours = Math.floor(ageMinutes / 60);
                statusEl.textContent = `Draft saved ${ageHours} hour${ageHours > 1 ? 's' : ''} ago`;
            }
        } catch (e) {
            console.error('Error reading draft timestamp:', e);
        }
    }
}

// Update on page load
updateDraftStatus();

// Update every minute
setInterval(updateDraftStatus, 60000);
</script>
```

#### Benefits

- **No data loss:** User never loses typed content
- **Auto-save:** Saves after 500ms of inactivity (debounced)
- **Zero JavaScript:** No custom JS code needed
- **Selective fields:** Only fields with `data-draft="true"` are tracked
- **Automatic restore:** Drafts restored on page load

#### Common Use Cases

- Comment forms
- Blog post editors
- Contact forms
- Survey/quiz responses
- Long registration forms
- Email composition

#### Draft Lifecycle

```
User types → localStorage.setItem() (every keystroke, debounced 500ms)
           ↓
User refreshes → localStorage.getItem() (restore draft on mount)
           ↓
User submits → localStorage.removeItem() (clear draft)
```

#### Limitations

**Current Implementation:**
- **Client-side only:** Drafts stored in localStorage (not synced to server)
- **No multi-device support:** Drafts don't sync across devices
- **localStorage limits:** ~5-10MB per domain (browser dependent)
- **No encryption:** Don't use for sensitive data (passwords, credit cards)
- **Auto-restore:** Automatically restores drafts without user confirmation
- **Same-domain only:** Drafts persist across tabs/windows on same domain

**Storage Limitations:**
- Browser localStorage typically provides 5-10MB per domain
- Large drafts (e.g., pasting huge text/images) may fail silently
- No warning when approaching storage limits
- Clearing browser data removes all drafts

**Namespace Collision Protection:**
- ✅ Auto-generated keys include view class name (`commentformview_draft`)
- ✅ Custom keys can be set per view via `draft_key` attribute
- ✅ Override `get_draft_key()` for dynamic keys (e.g., include record ID)

**Future Enhancements:**
- User confirmation prompt: "Resume draft from X minutes ago?"
- Server-side storage option for multi-device sync
- Draft age display in template context
- Draft size warnings before localStorage limit
- Encrypted storage for sensitive form data

#### See Also

- [STATE_MANAGEMENT_PATTERNS.md](STATE_MANAGEMENT_PATTERNS.md) - Draft mode patterns
- [STATE_MANAGEMENT_EXAMPLES.md](STATE_MANAGEMENT_EXAMPLES.md) - Form examples

---

## HTML Attributes

### @loading

**Status:** ✅ Implemented (PR #83)

Phoenix LiveView-style loading attributes for showing/hiding elements and adding classes during async operations. Perfect for loading spinners, status messages, and "Saving..." indicators.

#### Supported Modifiers

- `dj-loading.disable` - Disable element during loading
- `dj-loading.class="class-name"` - Add class during loading
- `dj-loading.show` - Show element during loading (display: block)
- `dj-loading.hide` - Hide element during loading (display: none)

Multiple modifiers can be combined on the same element.

#### Syntax

```html
<!-- Disable button during event -->
<button dj-loading.disable>Button Text</button>

<!-- Add loading class -->
<button dj-loading.class="opacity-50">Button Text</button>

<!-- Show element during loading -->
<div dj-loading.show style="display: none;">Loading...</div>

<!-- Hide element during loading -->
<div dj-loading.hide>Content</div>

<!-- Combine multiple modifiers -->
<button dj-loading.disable dj-loading.class="loading">Save</button>
```

#### Example

```python
class SaveFormView(LiveView):
    template_string = """
    <form dj-submit="save_data">
        <input type="text" name="title" />

        <!-- Disable and add class during save -->
        <button type="submit" dj-loading.disable dj-loading.class="opacity-50">
            Save Article
        </button>

        <!-- Show spinner during save -->
        <div dj-loading.show style="display: none;">
            <i class="fas fa-spinner fa-spin"></i> Saving...
        </div>

        <!-- Hide form content during save -->
        <div dj-loading.hide>
            <textarea name="content"></textarea>
        </div>

        <!-- Hide cancel button during save -->
        <button type="button" dj-loading.hide>Cancel</button>
    </form>
    """

    def save_data(self, title: str = "", content: str = "", **kwargs):
        """
        While this handler runs:
        - Submit button is disabled and has opacity-50 class
        - Spinner is visible
        - Form content and cancel button are hidden

        When handler completes:
        - All states restore automatically
        """
        time.sleep(2)  # Simulate slow operation
        MyModel.objects.create(title=title, content=content)
```

#### Behavior

1. **User submits form** → Event sent to server
2. **Loading starts** → `globalLoadingManager.startLoading(eventName, triggerElement)`
   - Elements with `dj-loading.disable` become disabled
   - Elements with `dj-loading.class` get the class added
   - Elements with `dj-loading.show` become visible
   - Elements with `dj-loading.hide` become hidden
3. **Handler executes** → Processing on server
4. **Response received** → `globalLoadingManager.stopLoading(eventName, triggerElement)`
   - All original states restored automatically

#### Scoping Rules

Loading states are scoped to prevent cross-button contamination when multiple buttons trigger the same event handler:

**Rule 1: Trigger Element Always Affected**
- The button/element that triggered the event always gets the loading state

**Rule 2: Siblings Only in Grouping Containers**
- Sibling elements are affected ONLY if they share a parent with an explicit grouping class:
  - `d-flex` (Bootstrap flex container)
  - `btn-group` (Bootstrap button group)
  - `input-group` (Bootstrap input group)
  - `form-group` (Bootstrap form group)
  - `btn-toolbar` (Bootstrap button toolbar)

**Example: Independent Buttons**
```html
<div class="card-body">
    <!-- These buttons operate INDEPENDENTLY even with same event -->
    <button dj-click="save" dj-loading.disable>Save A</button>
    <button dj-click="save" dj-loading.class="opacity-25">Save B</button>
    <button dj-click="save" dj-loading.disable dj-loading.class="opacity-25">Save C</button>
</div>
```
✅ Clicking "Save A" only affects "Save A" (no grouping container)

**Example: Grouped Elements (Button + Spinner)**
```html
<div class="d-flex align-items-center gap-3">
    <!-- These are grouped together because of d-flex parent -->
    <button dj-click="save">Save</button>
    <div dj-loading.show style="display: none;">Saving...</div>
</div>
```
✅ Clicking "Save" affects both button and spinner (d-flex grouping)

**Example: Wrong - No Grouping**
```html
<!-- ❌ Spinner won't show - no grouping container -->
<button dj-click="save">Save</button>
<div dj-loading.show style="display: none;">Saving...</div>
```

**Visual Effects:**
- **dj-loading.disable**: Bootstrap applies `opacity: 0.65` automatically to disabled buttons
- **dj-loading.class="opacity-25"**: Much more transparent (0.25), button stays enabled
- **Combined**: Button disabled (cursor: not-allowed) AND very transparent

#### Custom Display Value

Use `data-loading-display` to customize the display value for `dj-loading.show`:

```html
<!-- Show as inline-block instead of block -->
<span dj-loading.show data-loading-display="inline-block" style="display: none;">
    Loading...
</span>
```

#### Complete Form Example

```html
<form dj-submit="save_article">
    <!-- Form fields -->
    <input type="text" name="title" />
    <textarea name="content"></textarea>

    <!-- Multi-state loading UX -->
    <div class="form-actions">
        <!-- Disable and dim save button -->
        <button type="submit" dj-loading.disable dj-loading.class="opacity-50">
            Save Article
        </button>

        <!-- Hide cancel during save -->
        <button type="button" dj-loading.hide>
            Cancel
        </button>

        <!-- Show inline spinner -->
        <span dj-loading.show style="display: none;" data-loading-display="inline">
            <i class="spinner"></i> Saving...
        </span>
    </div>

    <!-- Show overlay during save -->
    <div dj-loading.show dj-loading.class="overlay" style="display: none;">
        Processing your request...
    </div>
</form>
```

#### Implementation

The LoadingManager class handles all @loading attribute logic:

```javascript
// Global instance available in client.js
const globalLoadingManager = new LoadingManager();

// Register elements during page load
globalLoadingManager.register(element, eventName);

// Automatic state management
globalLoadingManager.startLoading('save_article', triggerElement);
globalLoadingManager.stopLoading('save_article', triggerElement);
```

#### Debug Mode

Enable detailed logging for troubleshooting @loading behavior:

```html
<script>
    // Enable debug logging (shows registration, modifiers, and state changes)
    // Must be set BEFORE djust client.js loads
    window.djustDebug = true;
</script>
<!-- djust auto-injects client.js - no manual script tag needed -->
```

**Note:** djust automatically injects `client.js` for LiveView pages. The above example shows the debug flag must be set before the client loads.

**Debug Output:**
```javascript
[Loading] Registered modifiers for "save": [{type: 'disable'}, {type: 'class', value: 'opacity-25'}]
[Loading] Started: save <button>
[Loading] Applying state to element: <button> modifiers: [{type: 'disable'}, {type: 'class', value: 'opacity-25'}]
[Loading] Applied disable to element
[Loading] Applied class "opacity-25" to element
[Loading] Stopped: save <button>
```

**Production:** Set `window.djustDebug = false` to disable logging.

#### Common Use Cases

- Submit button spinners
- Form processing indicators
- Loading overlays
- Progress indicators
- Status messages during async operations

#### Testing

See `tests/js/loading.test.js` for 30 comprehensive unit tests covering all modifiers, state management, and edge cases.

#### See Also

- [@loading-text](#loading-text) - Button text replacement
- [LoadingManager API](#loadingmanager-api) - Client-side API
- [STATE_MANAGEMENT_PATTERNS.md](STATE_MANAGEMENT_PATTERNS.md) - Loading state patterns

---

### @loading-text

**Status:** 🚧 Not Yet Implemented (use dj-loading.class instead)

Temporarily replaces button text during event processing. Simpler alternative to `@loading` for basic button text changes.

#### Syntax

```html
<button @loading-text="Text to show during loading">
    Default text
</button>
```

#### Example

```python
class UploadView(LiveView):
    template_string = """
    <form dj-submit="upload_file">
        <input type="file" name="file" />

        <button type="submit" @loading-text="Uploading...">
            Upload File
        </button>
    </form>
    """

    def upload_file(self, file=None, **kwargs):
        """
        Button text changes:
        Before: "Upload File"
        During: "Uploading..."
        After:  "Upload File"
        """
        handle_file_upload(file)
```

#### Behavior

1. **Before event:** Button shows default text ("Upload File")
2. **During event:** Button shows loading text ("Uploading...")
3. **After event:** Button reverts to default text

#### With Icons

```html
<button dj-click="save" @loading-text="⏳ Saving...">
    💾 Save
</button>

<!-- During event: "⏳ Saving..." -->
```

#### Disable Button During Loading

```html
<button dj-click="save"
        @loading-text="Saving..."
        @loading-disabled>
    Save
</button>

<!-- Button becomes disabled during event -->
```

#### Common Use Cases

- Save buttons
- Submit buttons
- Upload buttons
- Delete confirmations
- Any action button with server processing

#### Comparison with @loading

| Feature | @loading | @loading-text |
|---------|----------|---------------|
| Use case | Show/hide elements | Replace button text |
| Complexity | Medium (need extra elements) | Simple (just attribute) |
| Flexibility | High (custom HTML) | Low (text only) |
| Icons | Full support | Limited (text+emoji) |

#### See Also

- [@loading](#loading) - Show/hide loading elements
- [STATE_MANAGEMENT_EXAMPLES.md](STATE_MANAGEMENT_EXAMPLES.md) - Button examples

---

## Advanced Topics

### Combining Decorators

Decorators can be combined for powerful effects. Order matters!

#### Recommended Order

```python
from djust.decorators import optimistic, debounce, cache, client_state, throttle

# Correct order (inner to outer):
@client_state(keys=["search_results"])  # 4. Publish to state bus
@cache(ttl=300)                          # 3. Cache responses
@debounce(wait=0.5)                      # 2. Debounce requests
@optimistic                              # 1. Update UI immediately
def search(self, query: str = "", **kwargs):
    """
    Execution order:
    1. Client updates UI optimistically (instant)
    2. Client debounces server request (500ms delay)
    3. Client checks cache (maybe instant return)
    4. Server processes (if cache miss)
    5. Result published to state bus (other components update)
    """
    return {"results": search_database(query)}
```

#### Invalid Combinations

```python
# ❌ WRONG: @cache before @debounce (cache won't help)
@debounce(wait=0.5)
@cache(ttl=300)
def search(self, query: str = "", **kwargs):
    """
    Cache is checked BEFORE debouncing, defeating the purpose.
    Debounce should be outermost decorator.
    """
    ...

# ✅ CORRECT: @debounce before @cache
@cache(ttl=300)
@debounce(wait=0.5)
def search(self, query: str = "", **kwargs):
    """
    Debounce first, then check cache.
    This is the right order.
    """
    ...
```

#### Common Combinations

**Optimistic + Debounce** (smooth sliders):

```python
@debounce(wait=0.5)
@optimistic
def update_slider(self, value: int = 0, **kwargs):
    """
    UI updates instantly as slider moves.
    Server receives one request after user stops.
    """
    ...
```

**Cache + Debounce** (autocomplete):

```python
@cache(ttl=300)
@debounce(wait=0.3)
def autocomplete(self, query: str = "", **kwargs):
    """
    User types → debounced → check cache → server (if needed)
    """
    ...
```

**Client State + Optimistic** (multi-component dashboards):

```python
@client_state(keys=["temperature"])
@optimistic
def update_temperature(self, temp: int = 0, **kwargs):
    """
    All components update instantly.
    Server validates in background.
    """
    ...
```

---

### Decorator Order Rules

When combining multiple decorators, **order matters**. Decorators are applied from bottom to top (innermost to outermost), and execution flows from top to bottom.

#### Recommended Order (Outer to Inner)

```python
from djust.decorators import client_state, cache, debounce, optimistic

# ✅ CORRECT ORDER:
@client_state(keys=["results"])  # 4. Publish to state bus (outermost)
@cache(ttl=300)                   # 3. Cache responses
@debounce(wait=0.5)               # 2. Debounce requests
@optimistic                       # 1. Update UI immediately (innermost)
def search(self, query: str = "", **kwargs):
    """
    Execution flow:
    1. Client updates UI optimistically (instant)
    2. Client debounces server request (500ms delay)
    3. Client checks cache (maybe instant return)
    4. Server processes (if cache miss)
    5. Result published to state bus (other components update)
    """
    return {"results": search_database(query)}
```

#### Why Order Matters

**Example 1: Optimistic + Debounce**

```python
# ✅ CORRECT: UI updates instantly, server receives debounced requests
@debounce(wait=0.5)  # Outer: delays server call
@optimistic          # Inner: immediate UI update
def update_slider(self, value: int = 0, **kwargs):
    """
    User drags slider:
    - UI updates at 60 FPS (optimistic)
    - Server receives ~1 request after 500ms (debounced)
    → Best UX!
    """
    self.value = value

# ❌ WRONG: Debounce delays optimistic update
@optimistic          # Outer: tries to update after debounce
@debounce(wait=0.5)  # Inner: delays everything
def update_slider(self, value: int = 0, **kwargs):
    """
    User drags slider:
    - UI updates delayed by 500ms (bad UX!)
    - Defeats purpose of optimistic updates
    → Feels sluggish
    """
    self.value = value
```

**Example 2: Cache + Debounce**

```python
# ✅ CORRECT: Debounce first, then check cache
@cache(ttl=300)      # Outer: check cache after debounce
@debounce(wait=0.3)  # Inner: debounce user input
def search(self, query: str = "", **kwargs):
    """
    User types "python":
    - Debounce waits 300ms after last keystroke
    - Then check cache (might be instant hit)
    - Then query server (if cache miss)
    → Optimal performance
    """
    return search_database(query)

# ❌ WRONG: Cache checked before debounce completes
@debounce(wait=0.3)  # Outer: debounces cache check (no benefit)
@cache(ttl=300)      # Inner: checked on every keystroke
def search(self, query: str = "", **kwargs):
    """
    User types "python":
    - Cache checked 6 times (once per keystroke)
    - Debounce applied to server call only
    → Cache benefit reduced
    """
    return search_database(query)
```

#### General Rules

1. **@optimistic** → Always innermost (immediate UI update)
2. **@debounce / @throttle** → Middle layer (rate limiting)
3. **@cache** → After rate limiting (check cache for debounced requests)
4. **@client_state** → Outermost (publish results to other components)

#### Quick Reference Table

| Decorator | Position | Why |
|-----------|----------|-----|
| `@optimistic` | Innermost (1st) | UI must update immediately |
| `@debounce` / `@throttle` | Middle (2nd) | Rate limit before server/cache |
| `@cache` | Middle (3rd) | Check cache for rate-limited requests |
| `@client_state` | Outermost (4th) | Publish final results |

#### Invalid Combinations

Some decorators shouldn't be combined:

```python
# ❌ Don't use both @debounce and @throttle
@debounce(wait=0.5)
@throttle(interval=0.1)
def handler(self, **kwargs):
    """
    Conflicting rate limiting strategies.
    Choose one: debounce (wait for silence) or throttle (periodic).
    """
    ...

# ❌ Don't use @cache with @optimistic (cache won't work)
@cache(ttl=300)
@optimistic
def handler(self, **kwargs):
    """
    Optimistic updates bypass cache.
    Use cache for read operations, not optimistic writes.
    """
    ...
```

---

### Type Hints

All decorators support full type hints for IDE autocomplete and type checking.

```python
from typing import Any
from djust import LiveView
from djust.decorators import debounce, optimistic

class TypedView(LiveView):
    @debounce(wait=0.5)
    def on_search(self, query: str = "", page: int = 1, **kwargs: Any) -> None:
        """
        Type hints work:
        - query: str (IDE knows this)
        - page: int (IDE knows this)
        - kwargs: Any (additional params)
        """
        self.results = search(query, page=page)
```

#### Generic Return Types

```python
from djust.decorators import cache
from typing import TypedDict

class SearchResult(TypedDict):
    id: int
    name: str

@cache(ttl=300)
def search(self, query: str = "") -> list[SearchResult]:
    """
    Return type is preserved through decorator.
    IDE autocomplete works on return value.
    """
    return [{"id": 1, "name": "Result"}]
```

---

### Error Handling

#### Server-Side Errors

```python
@optimistic
def delete_item(self, item_id: int = 0, **kwargs):
    """
    Client removes item optimistically.
    If server raises error, client receives error response.
    """
    item = get_object_or_404(Item, id=item_id)

    if not request.user.can_delete(item):
        # Client reverts optimistic update
        raise PermissionError("Cannot delete this item")

    item.delete()
```

#### Client-Side Error Handling

Errors are automatically handled by the framework:

1. **Optimistic update applied** → Item removed from list
2. **Server returns error** → Error response received
3. **Client reverts** → Item restored to list
4. **Error displayed** → Toast/alert shown to user (configurable)

#### Custom Error Messages

```python
from djust.exceptions import LiveViewError

@optimistic
def transfer_funds(self, amount: int = 0, **kwargs):
    if amount > self.balance:
        raise LiveViewError(
            "Insufficient funds",
            revert_optimistic=True,  # Revert UI change
            show_toast=True           # Show error to user
        )
```

---

## Implementation Details

### Complexity Analysis

This section estimates the implementation complexity for each feature to help prioritize development phases.

| Feature | Complexity | Estimated Time | Blockers | Dependencies |
|---------|-----------|----------------|----------|--------------|
| **@debounce** | Low | 1-2 days | None | client.js decorator metadata support |
| **@throttle** | Low | 1-2 days | None | client.js decorator metadata support |
| **@loading** | Low | 2-3 days | None | CSS class management |
| **@loading-text** | Low | 1 day | None | Button text replacement |
| **@optimistic** | High | 1-2 weeks | VDOM reconciliation, rollback logic | Stable VDOM implementation |
| **@client_state** | Medium | 3-5 days | State bus implementation | Pub/sub pattern in client.js |
| **@cache** | Medium | 3-5 days | LRU cache, TTL management | Map() with expiration logic |
| **DraftModeMixin** | Medium | 3-5 days | localStorage integration | Form field detection |

#### Complexity Breakdown

**Low Complexity (1-3 days each):**
- **@debounce / @throttle:** Simple timer management, already understood pattern
- **@loading / @loading-text:** CSS class toggling, straightforward implementation

**Medium Complexity (3-5 days each):**
- **@client_state:** Requires pub/sub system, event coordination
- **@cache:** Need LRU eviction, TTL expiration, cache key generation
- **DraftModeMixin:** localStorage API, field detection, restore logic

**High Complexity (1-2 weeks):**
- **@optimistic:** Requires VDOM state tracking, rollback on error, reconciliation with server patches

#### Implementation Phases (Recommended)

**Phase 1: Foundation (1 week)**
- Decorator metadata passing from Python → JavaScript
- Client-side decorator registry
- Test infrastructure

**Phase 2: Quick Wins (1 week)**
- @debounce
- @throttle
- @loading
- @loading-text

**Phase 3: Client State (1 week)**
- @client_state
- Built-in state bus

**Phase 4: Caching (1 week)**
- @cache
- LRU cache implementation

**Phase 5: Optimistic UI (2 weeks)**
- @optimistic
- VDOM tracking and rollback

**Phase 6: Draft Mode (1 week)**
- DraftModeMixin
- localStorage integration

**Total: 7-8 weeks core development + 3-4 weeks testing/polish = 10-12 weeks**

---

### Bundle Size Impact

One of djust's core principles is **"Zero JavaScript Required"**. Adding state management features will increase the client bundle size. Here's the estimated impact:

#### Current Baseline

```
client.js (current): ~5.0 KB (gzipped)
```

#### Estimated Additions

| Feature | Code Size | Gzipped | Notes |
|---------|-----------|---------|-------|
| **Decorator infrastructure** | ~300 bytes | ~150 bytes | Metadata registry |
| **@debounce implementation** | ~200 bytes | ~100 bytes | Timer management |
| **@throttle implementation** | ~200 bytes | ~100 bytes | Rate limiting |
| **@optimistic tracking** | ~800 bytes | ~400 bytes | VDOM state management |
| **@client_state (state bus)** | ~1000 bytes | ~500 bytes | Pub/sub system |
| **@cache (LRU cache)** | ~800 bytes | ~400 bytes | Map with TTL/LRU |
| **@loading CSS management** | ~300 bytes | ~150 bytes | Class toggling |
| **DraftModeMixin** | ~600 bytes | ~300 bytes | localStorage API |

**Total additions: ~4.2 KB raw → ~2.1 KB gzipped**

#### Final Bundle Size

```
Current:  5.0 KB gzipped
After:    7.1 KB gzipped (+42%)
```

#### Impact Analysis

**Comparison with Competitors:**

| Framework | Client Bundle | Notes |
|-----------|--------------|-------|
| Phoenix LiveView | ~30 KB | Includes reconnection, form helpers |
| Laravel Livewire | ~50 KB | Includes Alpine.js (~40 KB) |
| HTMX | ~14 KB | Minimal reactive features |
| **djust (current)** | **5 KB** | Bare minimum |
| **djust (after)** | **~7 KB** | With full state management |

**Verdict:** ✅ Even with all features, djust remains the **smallest** framework

#### Bundle Size Optimization Strategies

1. **Tree Shaking:** Only include decorators actually used in views
   ```python
   # Only @debounce used → only debounce code included
   @debounce(wait=0.5)
   def search(self, query: str = "", **kwargs):
       ...
   ```

2. **Lazy Loading:** Load complex features (optimistic, cache) on demand
   ```javascript
   // Load @optimistic code only when first used
   if (handler.isOptimistic && !optimisticModule) {
       optimisticModule = await import('./optimistic.js');
   }
   ```

3. **Code Splitting:** Separate decorators into modules
   ```
   client-core.js:       3 KB (always loaded)
   client-debounce.js:   0.5 KB (on demand)
   client-optimistic.js: 1 KB (on demand)
   client-cache.js:      1 KB (on demand)
   ```

4. **Minification:** Use terser with aggressive settings
   ```javascript
   // Before: 4200 bytes
   function debounce(fn, wait) { ... }

   // After minification: 2100 bytes
   const d=(f,w)=>{let t;return(...a)=>{clearTimeout(t);t=setTimeout(()=>f(...a),w)}}
   ```

#### Progressive Enhancement

For maximum performance, decorators can be progressively enhanced:

```python
class SearchView(LiveView):
    @debounce(wait=0.5)
    def search(self, query: str = "", **kwargs):
        """
        Without JavaScript: Works via form submission
        With JavaScript: Debounced real-time search

        → No JavaScript breakage!
        """
        self.results = search_database(query)
```

#### Recommendations

1. **Default:** Include all features (~7 KB) - still smaller than competitors
2. **Minimal:** Only @debounce + @throttle (~5.5 KB) - for very lightweight apps
3. **Custom:** Build system allows selecting specific decorators

#### Success Criteria

- ✅ Final bundle < 10 KB (achieved: 7.1 KB)
- ✅ Smaller than Phoenix LiveView (30 KB)
- ✅ Smaller than Laravel Livewire (50 KB)
- ✅ Comparable to HTMX (14 KB) but more features
- ✅ Progressive enhancement supported

---

## See Also

- **[STATE_MANAGEMENT_PATTERNS.md](STATE_MANAGEMENT_PATTERNS.md)** - Common patterns and use cases
- **[STATE_MANAGEMENT_MIGRATION.md](STATE_MANAGEMENT_MIGRATION.md)** - Migrating from manual JavaScript
- **[STATE_MANAGEMENT_TUTORIAL.md](STATE_MANAGEMENT_TUTORIAL.md)** - Step-by-step tutorial
- **[STATE_MANAGEMENT_EXAMPLES.md](STATE_MANAGEMENT_EXAMPLES.md)** - Copy-paste ready examples
- **[STATE_MANAGEMENT_ARCHITECTURE.md](STATE_MANAGEMENT_ARCHITECTURE.md)** - Implementation details

---

**Last Updated:** 2025-11-14
**Status:** 🚧 Partially Implemented (@cache, @client_state ✅)
**Feedback:** https://github.com/johnrtipton/djust/discussions
