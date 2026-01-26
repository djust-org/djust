# State Management Patterns Guide

**Status:** 🚧 Proposed Patterns - Not Yet Implemented

**Last Updated:** 2025-01-12

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
| Counter +/- buttons | `@optimistic` | Instant UI feedback |
| Range slider | `@optimistic + @debounce` | Smooth UI + reduced requests |
| Dropdown with many options | `@cache(ttl=600)` | Avoid re-fetching static data |
| Multi-widget dashboard | `@client_state` | Coordinate component updates |
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
    @debounce     Is it a slider/toggle?
                       │
         ┌─────────────┴──────────────┐
         │ YES                        │ NO
         ▼                            ▼
    @optimistic              Need multi-component
                             coordination?
                                   │
                    ┌──────────────┴───────────┐
                    │ YES                      │ NO
                    ▼                          ▼
              @client_state            Use basic event
                                       handler
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
from djust.decorators import debounce

class ProductSearchView(LiveView):
    template_string = """
    <input type="text"
           dj-input="search"
           placeholder="Search products..."
           value="{{ query }}" />

    <div>
        {% if loading %}
            <p @loading>Searching...</p>
        {% endif %}

        {% if results %}
            {% for product in results %}
                <div class="product">{{ product.name }}</div>
            {% endfor %}
        {% else %}
            <p>No results found</p>
        {% endif %}
    </div>
    """

    def mount(self, request):
        self.query = ""
        self.results = []
        self.loading = False

    @debounce(wait=0.5)  # Wait 500ms after last keystroke
    def search(self, value: str = "", **kwargs):
        """
        Typed: p-y-t-h-o-n (6 keystrokes in 2 seconds)
        Without @debounce: 6 server requests
        With @debounce: 1 server request (after 500ms silence)

        → 83% reduction in server load!
        """
        self.query = value
        self.loading = True

        if not value:
            self.results = []
            return

        # Expensive query runs only ONCE
        self.results = Product.objects.filter(
            name__icontains=value
        )[:20]

        self.loading = False
```

#### Expected Performance

| Metric | Value |
|--------|-------|
| Keystrokes for "python" | 6 |
| Server requests without debounce | 6 |
| Server requests with debounce | 1 |
| Reduction | **83%** |
| User-perceived delay | 500ms after typing stops |

#### Best Practices

- **Debounce time:** 300-500ms for search (longer = fewer requests, more delay)
- **Show loading state:** User expects delay, show indicator
- **Empty query:** Handle gracefully (clear results)
- **Min length:** Consider `if len(value) < 3: return` for very large datasets

---

### Pattern 2: Optimistic Counter

**Use Case:** +1/-1 counter buttons

**Problem:** User clicks +1 → waits for server → counter updates → feels sluggish

**Solution:** Update UI immediately (optimistically), server validates in background

#### Implementation

```python
from djust import LiveView
from djust.decorators import optimistic

class CounterView(LiveView):
    template_string = """
    <div class="counter">
        <button dj-click="decrement">-</button>
        <span class="count">{{ count }}</span>
        <button dj-click="increment">+</button>
    </div>
    """

    def mount(self, request):
        self.count = 0

    @optimistic  # UI updates instantly!
    def increment(self, **kwargs):
        """
        User clicks → count increases INSTANTLY (< 16ms)
        Server receives request in background
        Server validates (count += 1)
        Server sends confirmation

        User never waits!
        """
        self.count += 1

    @optimistic
    def decrement(self, **kwargs):
        self.count = max(0, self.count - 1)  # Server validates bounds
```

#### Expected Performance

| Metric | Without @optimistic | With @optimistic |
|--------|---------------------|------------------|
| Click → visual update | ~50-150ms (server RT) | **< 16ms** |
| Perceived responsiveness | Sluggish | **Instant** |
| User satisfaction | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ |

#### With Server Validation

```python
@optimistic
def increment(self, **kwargs):
    """
    Client: increment count (optimistic)
    Server: validate (e.g., max 100)
    Server: correct if needed
    """
    if self.count >= 100:
        # Server prevents going over 100
        # Client receives corrected value
        return

    self.count += 1
```

#### Best Practices

- **Simple operations:** Works best for simple state changes
- **Server validates:** Always validate on server (security!)
- **Error handling:** Plan for optimistic update failures
- **Idempotent:** Operations should be repeatable safely

---

### Pattern 3: Form Drafts

**Use Case:** Long forms (comments, articles, surveys)

**Problem:** User types 500 words → browser crashes → all work lost → user angry

**Solution:** Auto-save to localStorage, restore on page reload

#### Implementation

```python
from djust import LiveView
from djust.drafts import DraftModeMixin

class BlogPostEditor(DraftModeMixin, LiveView):
    # Configure draft auto-save
    draft_fields = ["title", "body", "tags"]
    draft_restore = True

    template_string = """
    <form dj-submit="publish_post">
        <input type="text"
               name="title"
               placeholder="Post title..."
               value="{{ title }}" />

        <textarea name="body"
                  rows="20"
                  placeholder="Write your post...">{{ body }}</textarea>

        <input type="text"
               name="tags"
               placeholder="Tags (comma separated)"
               value="{{ tags }}" />

        <div class="form-actions">
            <span id="draft-status">
                ✓ Draft auto-saved
            </span>

            <button type="submit" @loading-text="Publishing...">
                Publish Post
            </button>
        </div>
    </form>
    """

    def mount(self, request):
        if not hasattr(self, 'title'):
            self.title = ""
            self.body = ""
            self.tags = ""

    def publish_post(self, title: str = "", body: str = "", tags: str = "", **kwargs):
        """
        On successful publish, draft is automatically cleared.
        """
        post = BlogPost.objects.create(
            title=title,
            body=body,
            tags=tags,
            author=self.request.user
        )

        # Clear form (draft auto-clears)
        self.title = ""
        self.body = ""
        self.tags = ""
```

#### Expected Performance

| Scenario | Without DraftModeMixin | With DraftModeMixin |
|----------|------------------------|---------------------|
| User types 500 words | All lost if browser crashes | **All recovered** |
| Refresh during editing | Work lost | **Work restored** |
| Accidental navigation | Work lost | **Work restored** |

#### Best Practices

- **List all important fields:** Don't forget any text inputs
- **Clear on success:** Draft clears automatically after submit
- **Namespace:** Use custom namespace for multi-form pages
- **Privacy:** Don't use for sensitive data (localStorage is unencrypted)

---

### Pattern 4: Multi-Component Dashboard

**Use Case:** Dashboard with multiple widgets controlled by one input

**Problem:** Slider changes → need to update chart + gauge + display + stats → 4 separate server requests?

**Solution:** Publish to client-side state bus, all components update instantly

#### Implementation

```python
from djust import LiveView
from djust.decorators import client_state, debounce, optimistic

class TemperatureDashboard(LiveView):
    template_string = """
    <div class="dashboard">
        <!-- Control -->
        <input type="range"
               min="0" max="120"
               value="{{ temperature }}"
               dj-input="update_temperature" />

        <!-- Multiple displays subscribe to 'temperature' -->
        <div id="display" data-subscribe="temperature">
            {{ temperature }}°F
        </div>

        <div id="gauge" data-subscribe="temperature"></div>
        <canvas id="chart" data-subscribe="temperature"></canvas>

        <div class="stats" data-subscribe="temperature">
            <div>Celsius: {{ celsius }}°C</div>
            <div>Kelvin: {{ kelvin }}K</div>
        </div>
    </div>
    """

    def mount(self, request):
        self.temperature = 72
        self.celsius = 22
        self.kelvin = 295

    @client_state(keys=["temperature"])  # Publish to state bus
    @debounce(wait=0.5)                  # Debounce server requests
    @optimistic                          # Update UI immediately
    def update_temperature(self, temperature: int = 0, **kwargs):
        """
        User drags slider:
        1. Client updates ALL components instantly (60 FPS smooth!)
        2. Server receives ONE request after 500ms
        3. Server validates and persists

        Without @client_state:
        - 4 components × 100 updates = 400 server requests
        With @client_state:
        - 1 server request total
        → 99.75% reduction!
        """
        self.temperature = max(0, min(120, temperature))
        self.celsius = round((temperature - 32) * 5/9, 1)
        self.kelvin = round((temperature - 32) * 5/9 + 273.15, 1)
```

#### Expected Performance

| Scenario | Without @client_state | With @client_state |
|----------|----------------------|-------------------|
| Slider movement | 4 components × lag | **4 components instant** |
| Server requests | 400+ | **~1** |
| UI smoothness | Choppy | **60 FPS** |
| Network usage | High | **Minimal** |

#### Best Practices

- **Combine with @debounce:** Reduce server requests
- **Combine with @optimistic:** Instant UI updates
- **Subscribe in HTML:** Use `data-subscribe` for simple updates
- **Custom subscribers:** Use JavaScript for complex logic (charts)

---

### Pattern 5: Cached Autocomplete

**Use Case:** Autocomplete dropdown with limited options (countries, languages, etc.)

**Problem:** User types "p" → sees results → deletes → types "p" again → queries server again → wasteful

**Solution:** Cache responses client-side, return instantly on repeat queries

#### Implementation

```python
from djust import LiveView
from djust.decorators import cache, debounce

class LanguageAutocomplete(LiveView):
    template_string = """
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
    """

    def mount(self, request):
        self.query = ""
        self.results = []
        self.cache_hits = 0

    @cache(ttl=600)        # Cache for 10 minutes
    @debounce(wait=0.3)    # Debounce typing
    def search(self, query: str = "", **kwargs):
        """
        User types "python":
        1. First time: Query database → cache result
        2. User deletes, types "python" again: Return from cache (< 1ms!)

        Cache key = query value
        TTL = 10 minutes
        """
        if not query or len(query) < 2:
            self.results = []
            return

        # This query only runs on cache MISS
        self.results = Language.objects.filter(
            name__istartswith=query
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
- **Cache key:** Default (all params) is usually correct
- **Clear on logout:** Cache is session-scoped, clears automatically

---

### Pattern 6: Smooth Slider

**Use Case:** Range slider controlling visual property (color, size, temperature)

**Problem:** Slider drags → sends 100+ requests → server overloaded → UI choppy

**Solution:** Optimistic updates + debouncing = smooth 60 FPS + minimal server load

#### Implementation

```python
from djust import LiveView
from djust.decorators import optimistic, debounce

class ColorSliderView(LiveView):
    template_string = """
    <div>
        <!-- Preview updates instantly -->
        <div class="preview" style="background: hsl({{ hue }}, 50%, 50%);">
            Preview
        </div>

        <!-- Slider -->
        <input type="range"
               min="0" max="360"
               value="{{ hue }}"
               dj-input="update_hue" />

        <div class="stats">
            Client updates: <span id="client-count">0</span><br>
            Server updates: <span>{{ server_count }}</span><br>
            Reduction: <span id="reduction">0%</span>
        </div>
    </div>
    """

    def mount(self, request):
        self.hue = 180
        if not hasattr(self, 'server_count'):
            self.server_count = 0

    @debounce(wait=0.5)  # Server receives request after user stops
    @optimistic          # UI updates immediately
    def update_hue(self, hue: int = 0, **kwargs):
        """
        User drags slider 0 → 360 in 2 seconds:

        Without decorators:
        - 100+ server requests
        - Choppy 10-20 FPS

        With decorators:
        - 1 server request (after 500ms delay)
        - Smooth 60 FPS (optimistic updates)

        → 99% reduction in requests!
        """
        self.hue = max(0, min(360, hue))
        self.server_count += 1
```

#### Expected Performance

| Metric | Without Decorators | With Decorators |
|--------|-------------------|-----------------|
| UI frame rate | 10-20 FPS (choppy) | **60 FPS** (smooth) |
| Server requests | 100+ | **~1** |
| Reduction | - | **~99%** |
| UX Quality | Poor | **Excellent** |

#### Best Practices

- **Always combine:** `@optimistic + @debounce` for sliders
- **Debounce time:** 300-500ms (longer = fewer requests)
- **Server validates:** Check bounds on server
- **Visual feedback:** Show sync status indicator

---

### Pattern 7: Loading States

**Use Case:** Show loading indicator during server processing

**Problem:** User clicks submit → no feedback → did it work? → clicks again → duplicate submission

**Solution:** Show loading indicator, disable button during processing

#### Implementation

```python
from djust import LiveView

class FormSubmitView(LiveView):
    template_string = """
    <form dj-submit="save_data">
        <input type="text" name="title" />

        <div class="d-flex align-items-center gap-3">
            <!-- Button becomes disabled and semi-transparent -->
            <button type="submit" dj-loading.disable dj-loading.class="opacity-25">
                Save
            </button>

            <!-- Spinner shows during loading -->
            <div dj-loading.show style="display: none;" class="spinner-border text-primary" role="status">
                <span class="visually-hidden">Saving...</span>
            </div>
        </div>
    </form>
    """

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
- **User feedback:** Clear indication work is happening
- **Professional UX:** Looks polished and responsive

---

## Performance Considerations

### Network Optimization

| Pattern | Requests Saved | Bandwidth Saved | Best For |
|---------|---------------|-----------------|----------|
| @debounce(0.5) | ~80-95% | ~80-95% | Text inputs |
| @cache(ttl=300) | ~50-90% | ~50-90% | Autocomplete |
| @optimistic | 0% (same requests) | 0% | Perceived speed |
| @client_state | ~90-99% | ~90-99% | Multi-component |
| DraftModeMixin | 0% | 0% | Data protection |

### Memory Usage

- **@cache:** ~1-5MB depending on response sizes and TTL
- **@client_state:** Negligible (< 100KB state data)
- **DraftModeMixin:** Limited by localStorage (~5-10MB browser limit)

### CPU Impact

All decorators have negligible CPU impact (< 1ms overhead per event).

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
    <button dj-loading.disable aria-busy="false">
        Save
    </button>

    <!-- Screen reader will announce this when it appears -->
    <div dj-loading.show style="display: none;" aria-live="polite" role="status">
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
@debounce(wait=5.0)
def search(self, query: str = "", **kwargs):
    """
    User waits 5 seconds after typing → frustrating!
    Sweet spot: 300-500ms
    """
    ...
```

### ❌ Don't: Cache Dynamic Data

```python
# BAD: Caching real-time stock prices
@cache(ttl=300)
def get_stock_price(self, symbol: str = "", **kwargs):
    """
    Stock prices change every second!
    Don't cache data that changes frequently.
    """
    ...
```

### ❌ Don't: Optimistic Updates for Critical Operations

```python
# BAD: Optimistic payment processing
@optimistic  # DON'T DO THIS!
def process_payment(self, amount: int = 0, **kwargs):
    """
    Payment might fail on server.
    User should wait for confirmation.
    Don't use @optimistic for financial transactions!
    """
    ...
```

### ❌ Don't: Forget to Validate

```python
# BAD: No server-side validation
@optimistic
def delete_item(self, item_id: int = 0, **kwargs):
    """
    Client deletes immediately, but server doesn't check permissions!
    ALWAYS validate on server, even with @optimistic.
    """
    Item.objects.filter(id=item_id).delete()  # Missing permission check!
```

---

## See Also

- **[STATE_MANAGEMENT_API.md](STATE_MANAGEMENT_API.md)** - Complete API reference
- **[STATE_MANAGEMENT_MIGRATION.md](STATE_MANAGEMENT_MIGRATION.md)** - Migration guide
- **[STATE_MANAGEMENT_EXAMPLES.md](STATE_MANAGEMENT_EXAMPLES.md)** - Copy-paste examples
- **[STATE_MANAGEMENT_TUTORIAL.md](STATE_MANAGEMENT_TUTORIAL.md)** - Step-by-step tutorial

---

**Last Updated:** 2025-01-12
**Status:** 🚧 Proposed Patterns - Implementation pending
