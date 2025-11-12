# State Management Migration Guide

**Status:** 🚧 Migration guide for proposed API - Not Yet Implemented

**Last Updated:** 2025-01-12

---

## Table of Contents

- [Introduction](#introduction)
- [Why Migrate?](#why-migrate)
- [Decision Tree: Should You Migrate?](#decision-tree-should-you-migrate)
- [Migration Patterns](#migration-patterns)
  - [Pattern 1: Debounced Search](#pattern-1-debounced-search)
  - [Pattern 2: Optimistic Updates](#pattern-2-optimistic-updates)
  - [Pattern 3: Client-Side State Bus](#pattern-3-client-side-state-bus)
  - [Pattern 4: Form Drafts](#pattern-4-form-drafts)
  - [Pattern 5: Response Caching](#pattern-5-response-caching)
  - [Pattern 6: Loading States](#pattern-6-loading-states)
- [Complete Example Migration](#complete-example-migration)
- [Testing Your Migration](#testing-your-migration)
- [Troubleshooting](#troubleshooting)
- [When to Keep Custom JavaScript](#when-to-keep-custom-javascript)

---

## Introduction

This guide helps you migrate from manual JavaScript implementations to djust's Python-only state management decorators. Each pattern shows:

1. **Before:** Manual JavaScript implementation
2. **After:** Python decorator implementation
3. **Benefits:** What you gain from migration
4. **Lines saved:** Concrete reduction in code

---

## Why Migrate?

### Current Pain Points

**Example from existing codebase:**

```javascript
// 85 lines of manual JavaScript for a simple slider
let debounceTimer = null;
let clientUpdateCount = 0;
let serverUpdateCount = 0;

slider.addEventListener('input', (e) => {
    const value = parseInt(e.target.value);

    // Manual optimistic update
    updateDisplay(value);
    clientUpdateCount++;
    updateMetrics();

    // Manual debouncing
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
        syncToServer(value);
    }, 500);
});

function updateDisplay(value) {
    document.querySelector('.value-display').textContent = value;
}

function syncToServer(value) {
    if (typeof window.sendEvent === 'function') {
        window.sendEvent('update_value', { value: value });
        serverUpdateCount++;
        updateMetrics();
    }
}

function updateMetrics() {
    document.getElementById('client-updates').textContent = clientUpdateCount;
    // ... more metrics code
}
```

### After Migration

```python
from djust import LiveView
from djust.decorators import optimistic, debounce

class SliderView(LiveView):
    template_string = """
    <input type="range" min="0" max="100"
           value="{{ value }}" @input="update_value" />
    <div>{{ value }}</div>
    """

    def mount(self, request):
        self.value = 50

    @debounce(wait=0.5)
    @optimistic
    def update_value(self, value: int = 0, **kwargs):
        self.value = max(0, min(100, value))
```

**Result:**
- ❌ Before: 85 lines of JavaScript
- ✅ After: 0 lines of JavaScript, 8 lines of Python
- **93% code reduction**

---

## Decision Tree: Should You Migrate?

Use this decision tree to determine if your JavaScript code should be migrated to Python decorators:

```
Should I migrate this JavaScript to Python decorators?
│
├─ Is it debouncing user input?
│  └─ ✅ YES → Use @debounce(wait=0.5)
│     Example: Search input, slider, text area
│
├─ Is it throttling rapid events?
│  └─ ✅ YES → Use @throttle(interval=0.1)
│     Example: Scroll handlers, resize handlers, mousemove
│
├─ Is it optimistic UI updates?
│  └─ ✅ YES → Use @optimistic
│     Example: Counter, toggle, checkbox, instant feedback
│
├─ Is it client-side response caching?
│  └─ ✅ YES → Use @cache(ttl=60, key_params=["query"])
│     Example: Search results, autocomplete, API responses
│
├─ Is it coordinating state between components?
│  └─ ✅ YES → Use @client_state(keys=["filter", "sort"])
│     Example: Dashboard filters, multi-component views
│
├─ Is it auto-saving form drafts?
│  └─ ✅ YES → Use DraftModeMixin
│     Example: Long forms, comment boxes, email composer
│
├─ Is it loading indicators?
│  └─ ✅ YES → Use @loading HTML attribute
│     Example: Button states, spinner visibility
│
├─ Is it complex animations or canvas?
│  └─ ❌ NO → Keep custom JavaScript
│     Example: D3.js charts, WebGL, GSAP animations
│
├─ Is it third-party library integration?
│  └─ ❌ NO → Keep custom JavaScript
│     Example: Stripe.js, Google Maps, CodeMirror
│
├─ Is it low-level DOM manipulation?
│  └─ ❌ NO → Keep custom JavaScript
│     Example: Focus management, scroll positioning, selection
│
└─ Is it browser API usage?
   └─ ❌ NO → Keep custom JavaScript (or use Phoenix.LiveView.JS equivalent)
      Example: Clipboard API, Geolocation, Web Workers
```

### Quick Reference

| JavaScript Pattern | Python Solution | Migration Priority |
|-------------------|-----------------|-------------------|
| setTimeout with clearTimeout | `@debounce(wait)` | 🔥 High |
| setInterval with limits | `@throttle(interval)` | 🔥 High |
| Update DOM before server response | `@optimistic` | 🔥 High |
| localStorage caching | `@cache(ttl)` | 🟡 Medium |
| Event bus / PubSub | `@client_state(keys)` | 🟡 Medium |
| Auto-save to localStorage | `DraftModeMixin` | 🟡 Medium |
| Show/hide loading spinners | `@loading` attribute | 🟢 Low |
| Complex animations | Keep JavaScript | ⛔ Don't migrate |
| Third-party integrations | Keep JavaScript | ⛔ Don't migrate |

### Decision Examples

**✅ Migrate This:**

```javascript
// Debouncing search - MIGRATE
let timer = null;
input.addEventListener('input', (e) => {
    clearTimeout(timer);
    timer = setTimeout(() => {
        sendEvent('search', {query: e.target.value});
    }, 500);
});
```

```python
# After migration
@debounce(wait=0.5)
def search(self, query: str = "", **kwargs):
    self.results = Product.objects.filter(name__icontains=query)
```

**❌ Keep This:**

```javascript
// Complex D3.js chart - KEEP JAVASCRIPT
const svg = d3.select("svg");
const simulation = d3.forceSimulation(nodes)
    .force("link", d3.forceLink(links))
    .force("charge", d3.forceManyBody())
    .force("center", d3.forceCenter(width / 2, height / 2));

// This is domain-specific visualization code
// Don't try to move to Python decorators
```

---

## Migration Patterns

### Pattern 1: Debounced Search

#### Before: Manual JavaScript (35+ lines)

```python
# Python view
class SearchView(LiveView):
    def mount(self, request):
        self.query = ""
        self.results = []

    def search(self, query: str = "", **kwargs):
        self.query = query
        self.results = Product.objects.filter(name__icontains=query)[:20]

    def get_context_data(self):
        return {"query": self.query, "results": self.results}
```

```html
<!-- Template -->
<input type="text" id="search-input" value="{{ query }}" />
<div id="results">
    {% for product in results %}
        <div>{{ product.name }}</div>
    {% endfor %}
</div>

<script>
// 35 lines of manual debouncing
const searchInput = document.getElementById('search-input');
let debounceTimer = null;

searchInput.addEventListener('input', (e) => {
    const query = e.target.value;

    // Clear previous timer
    if (debounceTimer) {
        clearTimeout(debounceTimer);
    }

    // Set new timer (500ms)
    debounceTimer = setTimeout(() => {
        // Send to server
        if (typeof window.sendEvent === 'function') {
            window.sendEvent('search', { query: query });
        }
    }, 500);
});
</script>
```

#### After: Python Decorator (0 lines of JS)

```python
from djust import LiveView
from djust.decorators import debounce

class SearchView(LiveView):
    template_string = """
    <input type="text" @input="search" value="{{ query }}" />
    <div>
        {% for product in results %}
            <div>{{ product.name }}</div>
        {% endfor %}
    </div>
    """

    def mount(self, request):
        self.query = ""
        self.results = []

    @debounce(wait=0.5)  # That's it!
    def search(self, query: str = "", **kwargs):
        self.query = query
        self.results = Product.objects.filter(name__icontains=query)[:20]
```

#### Benefits

- ✅ **35 lines of JS → 0 lines**
- ✅ No timer management
- ✅ No manual `sendEvent` calls
- ✅ Type-safe Python API
- ✅ Testable server-side

---

### Pattern 2: Optimistic Updates

#### Before: Manual JavaScript (45+ lines)

```python
class CounterView(LiveView):
    def mount(self, request):
        self.count = 0

    def increment(self, **kwargs):
        self.count += 1
```

```html
<div>
    <span id="counter">{{ count }}</span>
    <button id="increment-btn">+1</button>
</div>

<script>
// 45 lines of manual optimistic updates
const counter = document.getElementById('counter');
const button = document.getElementById('increment-btn');

button.addEventListener('click', () => {
    // Optimistically update UI
    const currentValue = parseInt(counter.textContent);
    const newValue = currentValue + 1;
    counter.textContent = newValue;

    // Show loading state
    button.disabled = true;
    button.textContent = 'Updating...';

    // Send to server
    window.sendEvent('increment', {});

    // Wait for server response
    // (This requires custom event listener for response)
    document.addEventListener('liveview:update', () => {
        button.disabled = false;
        button.textContent = '+1';
    }, { once: true });
});

// Error handling
document.addEventListener('liveview:error', (e) => {
    // Revert optimistic update
    counter.textContent = currentValue;
    alert('Update failed');
});
</script>
```

#### After: Python Decorator (0 lines of JS)

```python
from djust import LiveView
from djust.decorators import optimistic

class CounterView(LiveView):
    template_string = """
    <div>
        <span>{{ count }}</span>
        <button @click="increment">+1</button>
    </div>
    """

    def mount(self, request):
        self.count = 0

    @optimistic  # Automatic optimistic updates!
    def increment(self, **kwargs):
        self.count += 1
```

#### Benefits

- ✅ **45 lines of JS → 0 lines**
- ✅ Automatic UI updates
- ✅ Automatic error handling
- ✅ Automatic rollback on failure
- ✅ No manual state tracking

---

### Pattern 3: Client-Side State Bus

#### Before: Manual ComponentStateBus (85+ lines)

```python
class DashboardView(LiveView):
    def mount(self, request):
        self.temperature = 72

    def update_temperature(self, temperature: int = 0, **kwargs):
        self.temperature = max(0, min(120, temperature))
```

```html
<input type="range" id="temp-slider" value="{{ temperature }}" />
<div id="display">{{ temperature }}°F</div>
<div id="gauge"></div>
<canvas id="chart"></canvas>

<script>
// 85+ lines of custom ComponentStateBus
class ComponentStateBus {
    constructor() {
        this.subscribers = {};
    }

    subscribe(key, callback) {
        if (!this.subscribers[key]) {
            this.subscribers[key] = [];
        }
        this.subscribers[key].push(callback);
    }

    publish(key, value) {
        if (this.subscribers[key]) {
            this.subscribers[key].forEach(cb => cb(value));
        }
    }
}

const bus = new ComponentStateBus();

// Display component
bus.subscribe('temperature', (temp) => {
    document.getElementById('display').textContent = temp + '°F';
});

// Gauge component
bus.subscribe('temperature', (temp) => {
    updateGauge(temp);
});

// Chart component
bus.subscribe('temperature', (temp) => {
    updateChart(temp);
});

// Slider handler
const slider = document.getElementById('temp-slider');
let debounceTimer;

slider.addEventListener('input', (e) => {
    const temp = parseInt(e.target.value);

    // Publish to bus
    bus.publish('temperature', temp);

    // Debounce server call
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
        window.sendEvent('update_temperature', { temperature: temp });
    }, 500);
});

function updateGauge(temp) { /* ... */ }
function updateChart(temp) { /* ... */ }
</script>
```

#### After: Python Decorator (0 lines for state bus)

```python
from djust import LiveView
from djust.decorators import client_state, debounce, optimistic

class DashboardView(LiveView):
    template_string = """
    <input type="range" @input="update_temperature" value="{{ temperature }}" />

    <!-- Components auto-subscribe to 'temperature' -->
    <div data-subscribe="temperature">{{ temperature }}°F</div>
    <div id="gauge" data-subscribe="temperature"></div>
    <canvas id="chart" data-subscribe="temperature"></canvas>

    <script>
    // Only custom chart/gauge rendering (unavoidable)
    window.StateBus.subscribe('temperature', updateGauge);
    window.StateBus.subscribe('temperature', updateChart);
    </script>
    """

    def mount(self, request):
        self.temperature = 72

    @client_state(keys=["temperature"])  # Auto-publishes to state bus
    @debounce(wait=0.5)
    @optimistic
    def update_temperature(self, temperature: int = 0, **kwargs):
        self.temperature = max(0, min(120, temperature))
```

#### Benefits

- ✅ **85 lines of JS → ~10 lines** (only custom rendering)
- ✅ Built-in state bus (no custom class)
- ✅ Automatic pub/sub
- ✅ Declarative subscriptions (`data-subscribe`)

---

### Pattern 4: Form Drafts

#### Before: Manual localStorage (65+ lines)

```python
class CommentFormView(LiveView):
    def mount(self, request):
        self.comment_text = ""

    def save_comment(self, comment_text: str = "", **kwargs):
        Comment.objects.create(text=comment_text, author=self.request.user)
        self.comment_text = ""
```

```html
<form @submit="save_comment">
    <textarea id="comment-text" name="comment_text">{{ comment_text }}</textarea>
    <button type="submit">Save</button>
</form>

<script>
// 65+ lines of manual localStorage
const textarea = document.getElementById('comment-text');
const DRAFT_KEY = 'comment-draft';

// Restore draft on load
const savedDraft = localStorage.getItem(DRAFT_KEY);
if (savedDraft && !textarea.value) {
    textarea.value = savedDraft;
}

// Save draft on every keystroke
let saveTimer;
textarea.addEventListener('input', (e) => {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
        localStorage.setItem(DRAFT_KEY, e.target.value);
    }, 500);
});

// Clear draft on successful save
document.addEventListener('liveview:update', (e) => {
    if (e.detail.event === 'save_comment' && e.detail.success) {
        localStorage.removeItem(DRAFT_KEY);
        textarea.value = '';
    }
});

// Show draft indicator
textarea.addEventListener('input', () => {
    document.getElementById('draft-status').textContent = 'Draft saved';
});
</script>
```

#### After: Python Mixin (0 lines of JS)

```python
from djust import LiveView
from djust.mixins import DraftModeMixin

class CommentFormView(DraftModeMixin, LiveView):
    draft_fields = ["comment_text"]  # Auto-saved to localStorage
    draft_restore = True

    template_string = """
    <form @submit="save_comment">
        <textarea name="comment_text">{{ comment_text }}</textarea>
        <button type="submit">Save</button>
    </form>
    """

    def mount(self, request):
        self.comment_text = ""

    def save_comment(self, comment_text: str = "", **kwargs):
        Comment.objects.create(text=comment_text, author=self.request.user)
        self.comment_text = ""  # Auto-clears draft
```

#### Benefits

- ✅ **65 lines of JS → 0 lines**
- ✅ Auto-save to localStorage
- ✅ Auto-restore on page load
- ✅ Auto-clear on success
- ✅ Configurable fields

---

### Pattern 5: Response Caching

#### Before: Manual Cache (55+ lines)

```python
class AutocompleteView(LiveView):
    def mount(self, request):
        self.query = ""
        self.results = []

    def search(self, query: str = "", **kwargs):
        self.query = query
        self.results = Language.objects.filter(name__istartswith=query)[:10]
```

```html
<input type="text" id="search" value="{{ query }}" />
<ul id="results"></ul>

<script>
// 55+ lines of manual caching
const cache = new Map();
const TTL = 300000; // 5 minutes
let debounceTimer;

document.getElementById('search').addEventListener('input', (e) => {
    const query = e.target.value;

    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
        // Check cache first
        const cached = cache.get(query);
        if (cached && Date.now() - cached.timestamp < TTL) {
            console.log('Cache hit!');
            displayResults(cached.results);
            return;
        }

        // Cache miss - query server
        console.log('Cache miss');
        window.sendEvent('search', { query: query });

        // Store in cache when response arrives
        document.addEventListener('liveview:update', (e) => {
            if (e.detail.results) {
                cache.set(query, {
                    results: e.detail.results,
                    timestamp: Date.now()
                });
            }
        }, { once: true });
    }, 300);
});

// LRU eviction
if (cache.size > 100) {
    const firstKey = cache.keys().next().value;
    cache.delete(firstKey);
}

function displayResults(results) { /* ... */ }
</script>
```

#### After: Python Decorator (0 lines of JS)

```python
from djust import LiveView
from djust.decorators import cache, debounce

class AutocompleteView(LiveView):
    template_string = """
    <input type="text" @input="search" value="{{ query }}" />
    <ul>
        {% for result in results %}
            <li>{{ result }}</li>
        {% endfor %}
    </ul>
    """

    def mount(self, request):
        self.query = ""
        self.results = []

    @cache(ttl=300)      # Auto-caching with 5-min TTL
    @debounce(wait=0.3)
    def search(self, query: str = "", **kwargs):
        self.query = query
        self.results = Language.objects.filter(name__istartswith=query)[:10]
```

#### Benefits

- ✅ **55 lines of JS → 0 lines**
- ✅ Automatic caching
- ✅ Automatic TTL management
- ✅ Automatic LRU eviction
- ✅ Cache hit/miss logging (debug mode)

---

### Pattern 6: Loading States

#### Before: Manual Loading Indicators (30+ lines)

```python
class FormView(LiveView):
    def save_data(self, title: str = "", **kwargs):
        time.sleep(2)  # Simulate slow operation
        MyModel.objects.create(title=title)
```

```html
<form @submit="save_data">
    <input type="text" name="title" />
    <button id="submit-btn">Save</button>
    <div id="loading" style="display: none;">Saving...</div>
</form>

<script>
// 30+ lines of manual loading states
const button = document.getElementById('submit-btn');
const loading = document.getElementById('loading');
const originalText = button.textContent;

// Show loading on submit
document.querySelector('form').addEventListener('submit', () => {
    button.disabled = true;
    button.textContent = 'Saving...';
    loading.style.display = 'block';
});

// Hide loading on response
document.addEventListener('liveview:update', () => {
    button.disabled = false;
    button.textContent = originalText;
    loading.style.display = 'none';
});

// Handle errors
document.addEventListener('liveview:error', () => {
    button.disabled = false;
    button.textContent = originalText;
    loading.style.display = 'none';
    alert('Save failed');
});
</script>
```

#### After: HTML Attribute (0 lines of JS)

```python
class FormView(LiveView):
    template_string = """
    <form @submit="save_data">
        <input type="text" name="title" />
        <button type="submit" @loading-text="Saving...">Save</button>
        <div @loading>Processing your request...</div>
    </form>
    """

    def save_data(self, title: str = "", **kwargs):
        time.sleep(2)  # Simulate slow operation
        MyModel.objects.create(title=title)
```

#### Benefits

- ✅ **30 lines of JS → 0 lines**
- ✅ Automatic show/hide
- ✅ Automatic button disabling
- ✅ Automatic error handling
- ✅ CSS classes applied automatically

---

## Complete Example Migration

### Before: Product Search (215 lines total)

**Python view (30 lines):**

```python
class ProductSearchView(LiveView):
    def mount(self, request):
        self.query = ""
        self.results = []
        self.filters = {"category": "", "min_price": 0}

    def search(self, query: str = "", **kwargs):
        self.query = query
        self.results = self._do_search()

    def apply_filters(self, category: str = "", min_price: int = 0, **kwargs):
        self.filters = {"category": category, "min_price": min_price}
        self.results = self._do_search()

    def _do_search(self):
        qs = Product.objects.all()
        if self.query:
            qs = qs.filter(name__icontains=self.query)
        if self.filters["category"]:
            qs = qs.filter(category=self.filters["category"])
        if self.filters["min_price"] > 0:
            qs = qs.filter(price__gte=self.filters["min_price"])
        return list(qs[:20])
```

**JavaScript (185 lines):**

```javascript
// Debouncing (20 lines)
let searchTimer, filterTimer;
const searchInput = document.getElementById('search');
searchInput.addEventListener('input', (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
        window.sendEvent('search', { query: e.target.value });
    }, 500);
});

// Caching (50 lines)
const cache = new Map();
// ... cache implementation

// Loading states (30 lines)
const loadingIndicator = document.getElementById('loading');
// ... loading state management

// Filter drafts (40 lines)
const categoryFilter = document.getElementById('category');
const priceFilter = document.getElementById('min-price');
// ... localStorage draft management

// Optimistic updates (25 lines)
// ... optimistic UI updates

// Error handling (20 lines)
// ... error handling and rollback
```

### After: Product Search (35 lines total)

```python
from djust import LiveView
from djust.decorators import cache, debounce, optimistic
from djust.mixins import DraftModeMixin

class ProductSearchView(DraftModeMixin, LiveView):
    draft_fields = ["query", "category", "min_price"]  # Auto-saved

    template_string = """
    <div>
        <input type="text" @input="search" value="{{ query }}"
               placeholder="Search products..." />

        <select @change="apply_filters" name="category">
            <option value="">All Categories</option>
            <option value="electronics">Electronics</option>
            <option value="clothing">Clothing</option>
        </select>

        <input type="number" @change="apply_filters"
               name="min_price" value="{{ min_price }}" />

        <div @loading>Searching...</div>

        <div>
            {% for product in results %}
                <div>{{ product.name }} - ${{ product.price }}</div>
            {% endfor %}
        </div>
    </div>
    """

    def mount(self, request):
        self.query = ""
        self.results = []
        self.category = ""
        self.min_price = 0

    @cache(ttl=300)
    @debounce(wait=0.5)
    @optimistic
    def search(self, query: str = "", **kwargs):
        self.query = query
        self.results = self._do_search()

    @cache(ttl=300)
    @optimistic
    def apply_filters(self, category: str = "", min_price: int = 0, **kwargs):
        self.category = category
        self.min_price = min_price
        self.results = self._do_search()

    def _do_search(self):
        qs = Product.objects.all()
        if self.query:
            qs = qs.filter(name__icontains=self.query)
        if self.category:
            qs = qs.filter(category=self.category)
        if self.min_price > 0:
            qs = qs.filter(price__gte=self.min_price)
        return list(qs[:20])
```

### Migration Results

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| **Total lines** | 215 | 35 | **84% reduction** |
| **JavaScript lines** | 185 | 0 | **100% elimination** |
| **Python lines** | 30 | 35 | 17% increase (more features) |
| **Features** | All manual | All automatic | Declarative API |
| **Maintainability** | Complex | Simple | Type-safe Python |

---

## Testing Your Migration

### Test Checklist

After migrating, verify:

**1. Debouncing:**
```python
def test_search_is_debounced():
    """Rapid typing should only trigger one server request"""
    view = SearchView()
    # Type "python" quickly
    for char in "python":
        view.search(query="p")  # Only last call should execute after 500ms
    assert view.server_call_count == 1  # Not 6
```

**2. Optimistic Updates:**
```python
def test_counter_updates_optimistically():
    """UI should update before server responds"""
    view = CounterView()
    view.mount(Mock(session={}))

    # Increment
    view.increment()
    assert view.count == 1  # Immediate update
```

**3. Caching:**
```python
def test_autocomplete_uses_cache():
    """Repeat queries should return from cache"""
    view = AutocompleteView()

    # First query
    view.search(query="python")
    first_time = time.time()

    # Second query (should be instant)
    view.search(query="python")
    second_time = time.time()

    assert second_time - first_time < 0.001  # Cache hit
```

**4. Draft Mode:**
```python
def test_drafts_are_restored():
    """Drafts should restore from localStorage"""
    view = CommentFormView()
    view.mount(Mock(session={}))

    # Type comment
    view.comment_text = "Draft text"

    # Refresh page (simulate)
    view2 = CommentFormView()
    view2.mount(Mock(session={}))

    # Draft restored
    assert view2.comment_text == "Draft text"
```

---

## Troubleshooting

### Common Issues

**Issue 1: Decorators not working**

```python
# ❌ Wrong: Decorator not imported
def search(self, query: str = "", **kwargs):
    ...

# ✅ Correct: Import decorator
from djust.decorators import debounce

@debounce(wait=0.5)
def search(self, query: str = "", **kwargs):
    ...
```

**Issue 2: Wrong decorator order**

```python
# ❌ Wrong: Debounce delays optimistic update
@optimistic
@debounce(wait=0.5)
def update(self, value: int = 0, **kwargs):
    ...

# ✅ Correct: Optimistic first, then debounce
@debounce(wait=0.5)
@optimistic
def update(self, value: int = 0, **kwargs):
    ...
```

**Issue 3: State not preserved**

```python
# ❌ Wrong: mount() resets state
def mount(self, request):
    self.count = 0  # Resets on every event!

# ✅ Correct: Use hasattr()
def mount(self, request):
    if not hasattr(self, 'count'):
        self.count = 0
```

---

## When to Keep Custom JavaScript

Some patterns still require custom JavaScript:

**1. Complex Canvas Rendering:**
```javascript
// Chart libraries (Chart.js, D3.js) need custom JS
function updateChart(data) {
    chart.update({ data: data });
}
```

**2. Third-Party Integrations:**
```javascript
// Color pickers, rich text editors, etc.
const picker = new ColorPicker('#picker');
picker.on('change', (color) => {
    window.sendEvent('update_color', { hex: color });
});
```

**3. Advanced Animations:**
```javascript
// CSS animations triggered by state changes
element.classList.add('fade-in');
```

**4. Browser APIs:**
```javascript
// Geolocation, Web Workers, Service Workers
navigator.geolocation.getCurrentPosition((pos) => {
    window.sendEvent('location_changed', { lat: pos.coords.latitude });
});
```

**Rule of thumb:** If it's **state management** (debouncing, caching, optimistic), use Python decorators. If it's **custom rendering** or **third-party integration**, use JavaScript.

---

## Related Documentation

### State Management Documentation
- **[STATE_MANAGEMENT_API.md](STATE_MANAGEMENT_API.md)** - Complete API reference
- **[STATE_MANAGEMENT_PATTERNS.md](STATE_MANAGEMENT_PATTERNS.md)** - Common patterns and best practices
- **[STATE_MANAGEMENT_TUTORIAL.md](STATE_MANAGEMENT_TUTORIAL.md)** - Step-by-step Product Search tutorial
- **[STATE_MANAGEMENT_EXAMPLES.md](STATE_MANAGEMENT_EXAMPLES.md)** - Copy-paste ready examples
- **[STATE_MANAGEMENT_ARCHITECTURE.md](STATE_MANAGEMENT_ARCHITECTURE.md)** - Implementation architecture
- **[STATE_MANAGEMENT_COMPARISON.md](STATE_MANAGEMENT_COMPARISON.md)** - vs Phoenix LiveView & Laravel Livewire

### Marketing & Competitive Analysis
- **[MARKETING.md](MARKETING.md)** - Feature highlights and positioning
- **[FRAMEWORK_COMPARISON.md](FRAMEWORK_COMPARISON.md)** - djust vs 13+ frameworks
- **[TECHNICAL_PITCH.md](TECHNICAL_PITCH.md)** - Technical selling points
- **[WHY_NOT_ALTERNATIVES.md](WHY_NOT_ALTERNATIVES.md)** - When to choose djust

---

**Last Updated:** 2025-01-12
**Status:** 🚧 Migration guide for proposed API - Implementation pending
