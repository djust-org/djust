# State Management Patterns Guide

> **Inert.** `@optimistic` and `@client_state` are INERT at 1.2.0rc10. `@optimistic` stamps metadata the shipped client never applies, so no optimistic update or revert happens (#2699). `@client_state` stamps metadata nothing in the shipped client reads (#2680), and the `StateBus` it named was deleted in #2680. A handler carrying either decorator behaves exactly like an undecorated one. The patterns below do not rely on them.

**Status:** `@debounce`, `@throttle`, `@cache`, the `dj-loading.*` attributes and `DraftModeMixin` are implemented (1.2.0rc10). `@optimistic` and `@client_state` are inert markers (see above).

**Last Updated:** 2026-09-22

> **Every handler needs `@event_handler`.** The default `event_security = "strict"` policy rejects any event whose handler lacks it. `@debounce`, `@throttle` and `@cache` do not count. Every example below stacks `@event_handler` outermost.

---

## Table of Contents

- [Overview](#overview)
- [Decision Matrix](#decision-matrix)
- [Pattern Catalog](#pattern-catalog)
  - [Pattern 1: Debounced Search](#pattern-1-debounced-search)
  - [Pattern 2: Optimistic Counter](#pattern-2-optimistic-counter)
  - [Pattern 3: Form Drafts](#pattern-3-form-drafts)
  - [Pattern 4: Multi-Component Dashboard](#pattern-4-multi-component-dashboard)
  - [Pattern 5: Cached Autocomplete](#pattern-5-cached-autocomplete)
  - [Pattern 6: Smooth Slider](#pattern-6-smooth-slider)
  - [Pattern 7: Loading States](#pattern-7-loading-states)
- [Performance Considerations](#performance-considerations)
- [Accessibility](#accessibility)
- [Anti-Patterns](#anti-patterns)

---

## Overview

This guide provides battle-tested patterns for common djust use cases. Each pattern shows when to use specific decorators, how to combine them, and expected performance characteristics.

### Choosing the Right Pattern

Use this guide when:
- You're unsure which decorator to use
- You want to optimize performance
- You need a reference implementation
- You're migrating from manual JavaScript

---

## Decision Matrix

### Quick Reference: "Which decorator should I use?"

| Your Use Case | Use This | Why |
|---------------|----------|-----|
| Search input that queries server | `@debounce(0.5)` | Wait until user stops typing |
| Scroll position tracking | `@throttle(0.1)` | Limit update frequency |
| Counter +/- buttons | plain `@event_handler` | One round-trip per click; `@optimistic` is inert (#2699) |
| Range slider | `@debounce(0.3)` or `@throttle(0.1)` | Range inputs are already throttled to 150ms by default; tune it per handler |
| Dropdown with many options | `@cache(ttl=600)` | Avoid re-fetching static data |
| Multi-widget dashboard | One handler updates all derived state | One server render updates every widget; `@client_state` is inert (#2680) |
| Long form (comment, article) | `DraftModeMixin` | Prevent data loss |
| Submit button | `dj-loading.disable + dj-loading.class` | Disable button + visual feedback |
| AJAX loading spinner | `dj-loading.show` | Show/hide spinner during requests |

### Flow Chart

```
┌─────────────────────────────────────┐
│ Does user type/input frequently?   │
└────────┬──────────────┬─────────────┘
         │ YES          │ NO
         ▼              ▼
    @debounce     Is it a slider?
                       │
         ┌─────────────┴──────────────┐
         │ YES                        │ NO
         ▼                            ▼
    @throttle or             Need multi-component
    @debounce                coordination?
                                   │
                    ┌──────────────┴───────────┐
                    │ YES                      │ NO
                    ▼                          ▼
              One handler sets         Use basic event
              all derived state        handler
```

---

## Pattern Catalog

### Pattern 1: Debounced Search

**Use Case:** Search input that queries database/API

**Problem:** User types "python" (6 keystrokes) → 6 server requests → server overload, UI flicker

**Solution:** Debounce to wait until user stops typing

#### Implementation

```python
from djust import LiveView
from djust.decorators import event_handler, debounce

class ProductSearchView(LiveView):
    template = """
    <div dj-root>
    <input type="text"
           dj-input="search"
           placeholder="Search products..."
           value="{{ query }}" />

    <div>
        <!-- Hidden until a "search" event is in flight -->
        <p dj-loading="search">Searching...</p>

        {% if results %}
            {% for product in results %}
                <div class="product">{{ product.name }}</div>
            {% endfor %}
        {% else %}
            <p>No results found</p>
        {% endif %}
    </div>
    </div>
    """

    def mount(self, request):
        self.query = ""
        self.results = []

    @event_handler
    @debounce(wait=0.5)  # Wait 500ms after last keystroke
    def search(self, value: str = "", **kwargs):
        """
        Typed: p-y-t-h-o-n (6 keystrokes in 2 seconds)
        Default (300ms element debounce): one request per pause
        With @debounce(wait=0.5): 1 server request (after 500ms silence)
        """
        self.query = value

        if not value:
            self.results = []
            return

        # Expensive query runs only ONCE
        self.results = Product.objects.filter(
            name__icontains=value
        )[:20]
```

#### Expected Performance

| Metric | Value |
|--------|-------|
| Keystrokes for "python" | 6 |
| Server requests with no debounce at all | up to 6 |
| Server requests with the default 300ms element debounce | 1 per pause longer than 300ms |
| Server requests with `@debounce(wait=0.5)` | 1 |
| User-perceived delay | 500ms after typing stops |

Text inputs bound with `dj-input` are already debounced by 300ms at the element level. `@debounce` lengthens the wait; `max_wait` forces a send during continuous typing.

#### Best Practices

- **Debounce time:** 300-500ms for search (longer = fewer requests, more delay)
- **Show loading state:** User expects delay, show indicator
- **Empty query:** Handle gracefully (clear results)
- **Min length:** Consider `if len(value) < 3: return` for very large datasets

---

### Pattern 2: Optimistic Counter

**Use Case:** +1/-1 counter buttons

**Problem:** User clicks +1 → waits for server → counter updates

> **Inert at 1.2.0rc10.** This pattern used to rely on `@optimistic` to update the count before the server replied. `@optimistic` has no runtime effect (#2699): the count changes only after the server round-trip, with or without it. Use a plain handler, shown below.

#### Implementation

```python
from djust import LiveView
from djust.decorators import event_handler

class CounterView(LiveView):
    template = """
    <div dj-root class="counter">
        <button dj-click="decrement">-</button>
        <span class="count">{{ count }}</span>
        <button dj-click="increment">+</button>
    </div>
    """

    def mount(self, request):
        self.count = 0

    @event_handler
    def increment(self, **kwargs):
        if self.count >= 100:
            return  # Server enforces the upper bound
        self.count += 1

    @event_handler
    def decrement(self, **kwargs):
        self.count = max(0, self.count - 1)  # Server enforces the lower bound
```

#### Expected Performance

| Metric | Value |
|--------|-------|
| Click → visual update | One server round-trip (typically ~50-150ms) |

#### Best Practices

- **Simple operations:** A single small state change re-renders quickly
- **Server validates:** Always validate on the server (security!)
- **Idempotent:** Operations should be repeatable safely

---

### Pattern 3: Form Drafts

**Use Case:** Long forms (comments, articles, surveys)

**Problem:** User types 500 words → browser crashes → all work lost → user angry

**Solution:** Auto-save to localStorage, restore on page reload

#### Implementation

```python
from djust import LiveView
from djust.decorators import event_handler
from djust.drafts import DraftModeMixin

class BlogPostEditor(DraftModeMixin, LiveView):
    # localStorage key for this form's draft (defaults to "<classname>_draft")
    draft_key = "blog_post_editor"

    # The client activates draft mode only when it finds data-draft-enabled
    # and data-draft-key, and saves only fields marked data-draft="true".
    template = """
    <div dj-root data-draft-enabled data-draft-key="{{ draft_key }}" {% if draft_clear %}data-draft-clear{% endif %}>
    <form dj-submit="publish_post">
        <input type="text"
               name="title"
               data-draft="true"
               placeholder="Post title..."
               value="{{ title }}" />

        <textarea name="body"
                  data-draft="true"
                  rows="20"
                  placeholder="Write your post...">{{ body }}</textarea>

        <input type="text"
               name="tags"
               data-draft="true"
               placeholder="Tags (comma separated)"
               value="{{ tags }}" />

        <div class="form-actions">
            <button type="submit" dj-disable-with="Publishing...">
                Publish Post
            </button>
        </div>
    </form>
    </div>
    """

    def mount(self, request):
        self.title = ""
        self.body = ""
        self.tags = ""

    @event_handler
    def publish_post(self, title: str = "", body: str = "", tags: str = "", **kwargs):
        BlogPost.objects.create(
            title=title,
            body=body,
            tags=tags,
            author=self.request.user
        )

        # Clear the form, and ask the client to drop the saved draft
        self.title = ""
        self.body = ""
        self.tags = ""
        self.clear_draft()
```

`DraftModeMixin` puts `draft_enabled`, `draft_key` and (after `clear_draft()`) `draft_clear` into the template context. If you override `get_context_data`, call `super()` so they survive.

> **Known issue ([#2971](https://github.com/djust-org/djust/issues/2971), 1.2.0rc10):** the client checks `data-draft-clear` only when the page first initialises, so a `clear_draft()` called from a WebSocket event is not applied to the open page, and the draft can reappear on the next load. Until that is fixed, clear the draft from JavaScript after a successful publish (for example `localStorage.removeItem("djust_draft_blog_post_editor")`), or keep the published form on a page the user does not reload.

#### Expected Performance

| Scenario | Without DraftModeMixin | With DraftModeMixin |
|----------|------------------------|---------------------|
| User types 500 words | All lost if browser crashes | **All recovered** |
| Refresh during editing | Work lost | **Work restored** |
| Accidental navigation | Work lost | **Work restored** |

#### Best Practices

- **Mark every important field:** Only fields with `data-draft="true"` are saved
- **Clear on success:** Call `self.clear_draft()` after a successful submit (see the known issue above)
- **Unique keys:** Give each form its own `draft_key` (or override `get_draft_key()`) on multi-form pages
- **Privacy:** Don't use for sensitive data (localStorage is unencrypted)

---

### Pattern 4: Multi-Component Dashboard

**Use Case:** Dashboard with multiple widgets controlled by one input

**Problem:** Slider changes → need to update chart + gauge + display + stats → 4 separate server requests?

**Solution:** One handler updates all derived state. The server re-renders once and a single response patches every widget.

> This pattern previously used `@client_state` and `data-subscribe` to fan updates out on the client. `@client_state` is inert (#2680), the StateBus is gone, and `data-subscribe` was never implemented. None of them are needed: every `{{ temperature }}`, `{{ celsius }}` and `{{ kelvin }}` below is updated by the same render.

#### Implementation

```python
from djust import LiveView
from djust.decorators import event_handler, throttle

class TemperatureDashboard(LiveView):
    template = """
    <div dj-root class="dashboard">
        <!-- Control -->
        <input type="range"
               min="0" max="120"
               value="{{ temperature }}"
               dj-input="update_temperature" />

        <!-- Every widget reads the same view state -->
        <div id="display">
            {{ temperature }}°F
        </div>

        <div class="stats">
            <div>Celsius: {{ celsius }}°C</div>
            <div>Kelvin: {{ kelvin }}K</div>
        </div>
    </div>
    """

    def mount(self, request):
        self.temperature = 72
        self.celsius = 22.2
        self.kelvin = 295.4

    @event_handler
    @throttle(interval=0.1)  # At most ~10 updates/second while dragging
    def update_temperature(self, value: int = 0, **kwargs):
        """dj-input sends the slider position as `value`."""
        self.temperature = max(0, min(120, value))
        self.celsius = round((self.temperature - 32) * 5/9, 1)
        self.kelvin = round((self.temperature - 32) * 5/9 + 273.15, 1)
```

#### Expected Performance

| Scenario | Value |
|----------|-------|
| Requests per slider change | 1 (all widgets update from it) |
| Requests while dragging | At most one per throttle interval (range inputs default to 150ms) |

#### Best Practices

- **Derive in one place:** Compute every dependent value in the handler that changes the source value
- **Throttle, don't debounce, live controls:** `@throttle` keeps the display moving while dragging; `@debounce` waits until the user stops
- **Charts:** Use a `dj-hook` for widgets that need client-side drawing

---

### Pattern 5: Cached Autocomplete

**Use Case:** Autocomplete dropdown with limited options (countries, languages, etc.)

**Problem:** User types "p" → sees results → deletes → types "p" again → queries server again → wasteful

**Solution:** Cache responses client-side, return instantly on repeat queries

#### Implementation

```python
from djust import LiveView
from djust.decorators import event_handler, cache, debounce

class LanguageAutocomplete(LiveView):
    template = """
    <div dj-root>
    <input type="text"
           dj-input="search"
           placeholder="Search languages..."
           value="{{ query }}" />

    <ul class="dropdown">
        {% for lang in results %}
            <li>{{ lang.name }}</li>
        {% endfor %}
    </ul>

    <div class="stats">
        <small>
            Cache hits: <span id="cache-hits">{{ cache_hits }}</span>
        </small>
    </div>
    </div>
    """

    def mount(self, request):
        self.query = ""
        self.results = []
        self.cache_hits = 0

    @event_handler
    @cache(ttl=600, key_params=["value"])  # Cache for 10 minutes, keyed on the text
    @debounce(wait=0.3)                    # Debounce typing
    def search(self, value: str = "", **kwargs):
        """
        User types "python":
        1. First time: Query database → cache result
        2. User deletes, types "python" again: replayed from cache, no request

        Cache key = handler name + key_params, e.g. search:value="python"
        TTL = 10 minutes
        """
        if not value or len(value) < 2:
            self.results = []
            return

        # This query only runs on cache MISS
        self.results = Language.objects.filter(
            name__istartswith=value
        )[:10]
```

#### Expected Performance

| Scenario | First Query | Cached Query | Speedup |
|----------|-------------|--------------|---------|
| User types "python" | 150ms | **0.5ms** | **300x faster** |
| User types "py" 10 times | 1500ms total | **155ms total** | **~90% faster** |

#### Best Practices

- **TTL selection:**
  - Static data (countries): TTL=3600 (1 hour) or higher
  - Semi-static (products): TTL=300 (5 minutes)
  - Dynamic (live data): Don't cache or TTL=30
- **Combine with debounce:** Reduce requests before checking cache
- **Cache key:** Without `key_params`, the key is the handler name plus every non-internal param (for `dj-input`: `value` and `field`). Pass `key_params=["value"]` to key on the text alone
- **Cache hits skip the handler:** On a hit the server handler does not run, so any `self.*` it sets keeps its previous value. Cache only handlers whose output depends solely on their parameters
- **Lifetime:** The cache lives in page memory only. It is cleared on every page mount or navigation, and is not tied to the session. Call `window.djust.clearCache()` after an in-page logout or permission change

---

### Pattern 6: Smooth Slider

**Use Case:** Range slider controlling visual property (color, size, temperature)

**Problem:** Slider drags → many requests → server overloaded → UI choppy

**Solution:** Rate-limit the handler. The slider thumb itself moves natively in the browser; only the server-rendered preview waits for the round-trip.

> This pattern previously combined `@optimistic` with `@debounce` for "instant" previews. `@optimistic` is inert (#2699), so the preview updates only when the server responds.

#### Implementation

```python
from djust import LiveView
from djust.decorators import event_handler, throttle

class ColorSliderView(LiveView):
    template = """
    <div dj-root>
        <!-- Preview updates on each server response -->
        <div class="preview" style="background: hsl({{ hue }}, 50%, 50%);">
            Preview
        </div>

        <!-- Slider -->
        <input type="range"
               min="0" max="360"
               value="{{ hue }}"
               dj-input="update_hue" />

        <div class="stats">
            Server updates: <span>{{ server_count }}</span>
        </div>
    </div>
    """

    def mount(self, request):
        self.hue = 180
        self.server_count = 0

    @event_handler
    @throttle(interval=0.1)  # At most one request per 100ms while dragging
    def update_hue(self, value: int = 0, **kwargs):
        """dj-input sends the slider position as `value`."""
        self.hue = max(0, min(360, value))
        self.server_count += 1
```

#### Expected Performance

| Metric | Without a handler limit | With `@throttle(interval=0.1)` |
|--------|-------------------------|--------------------------------|
| Server requests while dragging | One per 150ms (default range throttle) | One per 100ms, with the final position always sent |
| Preview | Follows the server responses | Follows the server responses |

#### Best Practices

- **Pick the limiter by feel:** `@throttle` keeps the preview moving while dragging; `@debounce(wait=0.3)` sends only once the user stops
- **Server validates:** Check bounds on the server
- **Pure client visuals:** If the preview must track the thumb frame-by-frame, drive it from a `dj-hook` and send the value to the server throttled

---

### Pattern 7: Loading States

**Use Case:** Show loading indicator during server processing

**Problem:** User clicks submit → no feedback → did it work? → clicks again → duplicate submission

**Solution:** Show loading indicator, disable button during processing

#### Implementation

```python
import time

from djust import LiveView
from djust.decorators import event_handler

class FormSubmitView(LiveView):
    template = """
    <div dj-root>
    <form dj-submit="save_data">
        <input type="text" name="title" />

        <div class="d-flex align-items-center gap-3">
            <!-- Button becomes disabled and semi-transparent while save_data runs -->
            <button type="submit" dj-loading.for="save_data" dj-loading.disable dj-loading.class="opacity-25">
                Save
            </button>

            <!-- Spinner shows during loading -->
            <div dj-loading.for="save_data" dj-loading.show style="display: none;" class="spinner-border text-primary" role="status">
                <span class="visually-hidden">Saving...</span>
            </div>
        </div>
    </form>
    </div>
    """

    @event_handler
    def save_data(self, title: str = "", **kwargs):
        """
        During execution:
        - Button disabled and very transparent (opacity: 0.25)
        - Spinner visible

        After completion:
        - Button enabled and normal opacity
        - Spinner hidden
        """
        time.sleep(2)  # Simulate slow operation
        MyModel.objects.create(title=title)
```

#### Benefits

- **Prevents double-submit:** Button disabled during processing
- **Name the event:** A `dj-loading.*` element reacts only to the event named by `dj-loading.for` (or `dj-loading="event"`), or to its own `dj-*` event attribute. For `dj-submit` the trigger is the `<form>`, so the button and spinner need `dj-loading.for="save_data"`
- **User feedback:** Clear indication work is happening
- **Professional UX:** Looks polished and responsive

---

## Performance Considerations

### Network Optimization

| Pattern | Requests Saved | Bandwidth Saved | Best For |
|---------|---------------|-----------------|----------|
| @debounce(0.5) | ~80-95% | ~80-95% | Text inputs |
| @cache(ttl=300) | ~50-90% | ~50-90% | Autocomplete |
| DraftModeMixin | 0% | 0% | Data protection |

`@optimistic` and `@client_state` are inert at 1.2.0rc10 (#2699, #2680) and change neither requests nor rendering.

### Memory Usage

- **@cache:** ~1-5MB depending on response sizes and TTL
- **DraftModeMixin:** Limited by localStorage (~5-10MB browser limit)

### CPU Impact

`@debounce`, `@throttle` and `@cache` run in the browser, and their per-event overhead is small next to a network round-trip.

---

## Accessibility

### Keyboard Navigation

All patterns work with keyboard:
- Tab to focus inputs
- Enter to submit forms
- Arrow keys for sliders
- Escape to close dropdowns

### Screen Readers

Ensure loading states are announced:

```html
<div class="d-flex gap-2">
    <button dj-click="save" dj-loading.disable aria-busy="false">
        Save
    </button>

    <!-- Screen reader will announce this when it appears -->
    <div dj-loading.for="save" dj-loading.show style="display: none;" aria-live="polite" role="status">
        Saving...
    </div>
</div>
```

**Note:** Use `aria-live="polite"` on elements with `dj-loading.show` to announce state changes to screen reader users.

---

## Anti-Patterns

### ❌ Don't: Over-Debounce

```python
# BAD: 5 seconds is way too long!
@event_handler
@debounce(wait=5.0)
def search(self, value: str = "", **kwargs):
    """
    User waits 5 seconds after typing → frustrating!
    Sweet spot: 300-500ms
    """
    ...
```

### ❌ Don't: Cache Dynamic Data

```python
# BAD: Caching real-time stock prices
@event_handler
@cache(ttl=300)
def get_stock_price(self, symbol: str = "", **kwargs):
    """
    Stock prices change every second!
    Don't cache data that changes frequently.
    """
    ...
```

### ❌ Don't: Forget to Validate

```python
# BAD: No server-side permission check
@event_handler
def delete_item(self, item_id: int = 0, **kwargs):
    """
    item_id comes from the client and can be any value.
    ALWAYS validate on the server.
    """
    Item.objects.filter(id=item_id).delete()  # Missing permission check!
```

`@optimistic` is inert at 1.2.0rc10 (#2699), so it neither helps nor hurts here. If it is implemented later, the server check stays mandatory, and critical operations such as payments should still wait for server confirmation.

---

## See Also

- **[STATE_MANAGEMENT_API.md](STATE_MANAGEMENT_API.md)** - Complete API reference
- **[STATE_MANAGEMENT_MIGRATION.md](STATE_MANAGEMENT_MIGRATION.md)** - Migration guide
- **[STATE_MANAGEMENT_EXAMPLES.md](STATE_MANAGEMENT_EXAMPLES.md)** - Copy-paste examples
- **[STATE_MANAGEMENT_TUTORIAL.md](STATE_MANAGEMENT_TUTORIAL.md)** - Step-by-step tutorial

---

**Last Updated:** 2026-09-22
**Status:** Implemented at 1.2.0rc10, except the inert `@optimistic` and `@client_state` markers
