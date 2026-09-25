# State Management Tutorial

> **Inert.** `@optimistic` and `@client_state` are INERT at 1.2.0rc10. `@optimistic` stamps metadata the shipped client never applies, so no optimistic update or revert happens (#2699). `@client_state` stamps metadata nothing in the shipped client reads (#2680), and the `StateBus` it named was deleted in #2680. A handler carrying either decorator behaves exactly like an undecorated one, so this tutorial does not use them.

**Level**: Intermediate
**Duration**: 30-45 minutes
**Goal**: Build a Product Search feature using djust state management decorators

This tutorial walks through building a real-world Product Search feature step-by-step, progressively adding decorators to improve user experience and reduce server load.

## Table of Contents

- [What You'll Build](#what-youll-build)
- [Prerequisites](#prerequisites)
- [Part 1: Basic LiveView](#part-1-basic-liveview)
- [Part 2: Add Debouncing](#part-2-add-debouncing)
- [Part 3: A Note on Optimistic Updates](#part-3-a-note-on-optimistic-updates)
- [Part 4: Add Response Caching](#part-4-add-response-caching)
- [Part 5: Add Loading States](#part-5-add-loading-states)
- [Part 6: Add Filters](#part-6-add-filters)
- [Complete Example](#complete-example)
- [Next Steps](#next-steps)

## What You'll Build

A Product Search page with:

- **Real-time search** as user types
- **Debounced requests** (wait for user to stop typing)
- **Response caching** (avoid redundant searches)
- **Loading indicators** (show progress)
- **Coordinated filters** (category + search work together)

**Final Result:**

```
┌─────────────────────────────────────────────┐
│ Product Search                    [Loading]│
├─────────────────────────────────────────────┤
│                                             │
│ Search: [laptop____________]  🔍           │
│ Category: [Electronics ▼]                   │
│                                             │
│ ┌─────────────────────────────────────────┐│
│ │ 🖥️  Dell XPS 15 Laptop                  ││
│ │     $1,299.99                            ││
│ └─────────────────────────────────────────┘│
│                                             │
│ ┌─────────────────────────────────────────┐│
│ │ 💻  MacBook Pro 14"                     ││
│ │     $1,999.99                            ││
│ └─────────────────────────────────────────┘│
│                                             │
│ Found 42 products                           │
└─────────────────────────────────────────────┘
```

**User Experience:**
- User types "l" → the input shows it natively; no request yet
- User types "la" → the pending send is postponed (debounced)
- User types "laptop" → Wait 500ms, then search server
- Server returns → Update results (42 products)
- User types "laptop" again → Cached response (no server request)

## Prerequisites

1. **djust installed** (1.2.0 or later; handler-level `@debounce`/`@throttle` need 1.2.0rc3+)
2. **Django project** with djust configured
3. **Product model** (or similar)

```python
# models.py
from django.db import models

class Product(models.Model):
    name = models.CharField(max_length=200)
    description = models.TextField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    category = models.CharField(max_length=100)
    in_stock = models.BooleanField(default=True)

    def __str__(self):
        return self.name
```

## Part 1: Basic LiveView

Let's start with a simple LiveView without any decorators.

### Step 1.1: Create the View

```python
# views.py
from djust import LiveView
from djust.decorators import event_handler
from .models import Product

class ProductSearchView(LiveView):
    template_name = 'products/search.html'

    def mount(self, request, **kwargs):
        """Initialize state on first load."""
        self.query = ""
        self.results = Product.objects.filter(in_stock=True)[:20]

    @event_handler  # Required: undecorated handlers are rejected by default
    def search(self, value: str = "", **kwargs):
        """Handle search input. dj-input sends the text as `value`."""
        query = value
        self.query = query

        if query:
            self.results = Product.objects.filter(
                name__icontains=query,
                in_stock=True
            )[:20]
        else:
            self.results = Product.objects.filter(in_stock=True)[:20]

    def get_context_data(self, **kwargs):
        """Return context for template rendering."""
        return {
            'query': self.query,
            'results': self.results,
            'count': self.results.count()
        }
```

### Step 1.2: Create the Template

```html
<!-- templates/products/search.html -->
<!DOCTYPE html>
<html>
<head>
    <title>Product Search</title>
    {% load live_tags %}
    {% djust_client_config %}
</head>
<body>
    <div class="container" dj-root>
        <h1>Product Search</h1>

        <div class="search-box">
            <input
                type="text"
                dj-input="search"
                value="{{ query }}"
                placeholder="Search products..."
                class="form-control"
            />
        </div>

        <div class="results">
            {% for product in results %}
            <div class="product-card">
                <h3>{{ product.name }}</h3>
                <p>{{ product.description }}</p>
                <p class="price">${{ product.price }}</p>
            </div>
            {% empty %}
            <p>No products found.</p>
            {% endfor %}
        </div>

        <div class="count">
            Found {{ count }} products
        </div>
    </div>

</body>
</html>
```

### Step 1.3: Add URL Route

```python
# urls.py
from django.urls import path
from .views import ProductSearchView

urlpatterns = [
    path('products/search/', ProductSearchView.as_view(), name='product_search'),
]
```

### Step 1.4: Test It

```bash
python manage.py runserver
# Visit: http://localhost:8000/products/search/
```

**Result:**
- ✅ Typing triggers search
- ⚠️ A request goes out whenever typing pauses for 300ms (text inputs are debounced by 300ms at the element level by default)
- ❌ No feedback while loading
- ❌ A slow typist can still send several searches for "laptop"

**Problems:**
1. **More requests than needed**: A 300ms pause is short, so partial words like "lap" get searched
2. **No loading feedback**: User doesn't know if search is running
3. **Redundant searches**: Same query searched multiple times

Let's fix these issues step by step.

## Part 2: Add Debouncing

**Problem**: The default 300ms element-level debounce fires on short pauses mid-word.

**Solution**: Use `@debounce` to wait longer before sending. (`dj-debounce="500"` on the input does the same per element; `@debounce(wait=0.5, max_wait=2)` also forces a send during continuous typing.)

### Step 2.1: Add @debounce Decorator

```python
# views.py
from djust import LiveView
from djust.decorators import event_handler, debounce  # ← Import decorator
from .models import Product

class ProductSearchView(LiveView):
    template_name = 'products/search.html'

    def mount(self, request, **kwargs):
        self.query = ""
        self.results = Product.objects.filter(in_stock=True)[:20]

    @event_handler
    @debounce(wait=0.5)  # ← Wait 500ms after last keystroke
    def search(self, value: str = "", **kwargs):
        """Handle search input (debounced)."""
        query = value
        self.query = query

        if query:
            self.results = Product.objects.filter(
                name__icontains=query,
                in_stock=True
            )[:20]
        else:
            self.results = Product.objects.filter(in_stock=True)[:20]

    def get_context_data(self, **kwargs):
        return {
            'query': self.query,
            'results': self.results,
            'count': self.results.count()
        }
```

### Step 2.2: Test Debouncing

```bash
python manage.py runserver
```

**Result:**
- ✅ Typing "laptop" sends **1 request** (after 500ms silence)
- ✅ Fewer searches for partial words
- ❌ Results don't update until the server responds

**What Changed:**
- `@debounce(wait=0.5)` automatically delays the server request
- Client waits 500ms after last keystroke before sending
- No JavaScript code required!

**How It Works:**

```
User types "l"     → Client starts 500ms timer
User types "la"    → Client resets timer (cancel previous)
User types "lap"   → Client resets timer
User types "lapt"  → Client resets timer
User types "lapto" → Client resets timer
User types "laptop" → Client resets timer
500ms silence      → Client sends search(value="laptop")
```

## Part 3: A Note on Optimistic Updates

**Problem**: Results don't update until the server responds.

Earlier versions of this tutorial added `@optimistic` here. At 1.2.0rc10 `@optimistic` is **inert**: it records metadata, but the shipped client never applies an optimistic update or a revert (#2699). Adding it changes nothing, so this tutorial leaves it out.

You don't need it for this page anyway:

- The input already shows what the user typed, natively, with no server round-trip.
- The results can only come from the server, so there is nothing to guess client-side. Part 5 adds a loading indicator so the wait is visible.

## Part 4: Add Response Caching

**Problem**: Same query searched multiple times wastes server resources.

**Solution**: Use `@cache` to cache responses client-side.

### Step 4.1: Add @cache Decorator

```python
# views.py
from djust import LiveView
from djust.decorators import event_handler, debounce, cache  # ← Import cache
from .models import Product

class ProductSearchView(LiveView):
    template_name = 'products/search.html'

    def mount(self, request, **kwargs):
        self.query = ""
        self.results = Product.objects.filter(in_stock=True)[:20]

    @event_handler
    @debounce(wait=0.5)
    @cache(ttl=60, key_params=["value"])  # ← Cache for 60 seconds, keyed on the text
    def search(self, value: str = "", **kwargs):
        """Handle search input (debounced + cached)."""
        query = value
        self.query = query

        if query:
            self.results = Product.objects.filter(
                name__icontains=query,
                in_stock=True
            )[:20]
        else:
            self.results = Product.objects.filter(in_stock=True)[:20]

    def get_context_data(self, **kwargs):
        return {
            'query': self.query,
            'results': self.results,
            'count': self.results.count()
        }
```

### Step 4.2: Test Caching

```bash
python manage.py runserver
```

**Result:**
- ✅ Search "laptop" → Server request sent
- ✅ Search "laptop" again → **Cached response** (no server request!)
- ✅ Cache expires after 60 seconds
- ✅ Different queries cached separately

**What Changed:**
- `@cache(ttl=60)` stores responses in client-side cache
- `key_params=["value"]` keys the cache on the typed text (the `value` param `dj-input` sends)
- Cache key: `search:value="laptop"`, `search:value="mouse"`, etc.

**Cache Behavior:**

```
User searches "laptop"
  → Client checks cache for search:value="laptop" → Miss
  → Client sends server request
  → Server responds with results
  → Client stores in cache (TTL: 60s)
  → Client displays results

User searches "mouse"
  → Client checks cache for search:value="mouse" → Miss
  → Client sends server request
  → (cached separately)

User searches "laptop" again
  → Client checks cache for search:value="laptop" → Hit!
  → Client replays the cached patches (no server request)
```

**On a cache hit the handler does NOT run.** Any `self.*` it sets (here `self.query` and `self.results`) keeps its previous value on the server. Use `@cache` only on handlers whose result depends solely on their parameters, and not on handlers whose state other handlers read. Part 6 runs into exactly this and drops `@cache` from `search`.

**Cache Invalidation:**
- Automatic after TTL (60 seconds)
- Cleared on every page mount or navigation (the cache lives in page memory only)
- Manual: `window.djust.clearCache()` or `window.djust.invalidateCache('search')`

## Part 5: Add Loading States

**Problem**: User doesn't know when search is running.

**Solution**: Use the `dj-loading.*` attributes to show a loading indicator.

### Step 5.1: Update Template

```html
<!-- templates/products/search.html -->
<!DOCTYPE html>
<html>
<head>
    <title>Product Search</title>
    {% load live_tags %}
    {% djust_client_config %}
    <style>
        .loading { opacity: 0.6; pointer-events: none; }
        .search-box { position: relative; }
        .spinner {
            display: none;
            position: absolute;
            right: 10px;
            top: 10px;
        }
        .loading .spinner { display: block; }
    </style>
</head>
<body>
    <div class="container" dj-root>
        <h1>Product Search</h1>

        <!-- Add the .loading class while a "search" event is in flight -->
        <div class="search-box" dj-loading.class="loading" dj-loading.for="search">
            <input
                type="text"
                dj-input="search"
                value="{{ query }}"
                placeholder="Search products..."
                class="form-control"
            />
            <span class="spinner">🔄</span>
        </div>

        <div class="results">
            {% for product in results %}
            <div class="product-card">
                <h3>{{ product.name }}</h3>
                <p>{{ product.description }}</p>
                <p class="price">${{ product.price }}</p>
            </div>
            {% empty %}
            <p>No products found.</p>
            {% endfor %}
        </div>

        <div class="count">
            Found {{ count }} products
        </div>
    </div>

</body>
</html>
```

### Step 5.2: Test Loading States

```bash
python manage.py runserver
```

**Result:**
- ✅ Search starts → `.loading` class added
- ✅ Spinner shows (🔄)
- ✅ Input slightly dimmed
- ✅ Search completes → `.loading` class removed

**What Changed:**
- `dj-loading.class="loading"` adds the `loading` class while the event named by `dj-loading.for="search"` is in flight, and removes it when the response arrives
- The input that fired the event also gets a `djust-loading` class automatically, which you can style directly
- CSS handles visual feedback
- No JavaScript required!

**Loading Flow:**

```
User types "laptop"
  → The input shows the text natively
  → 500ms debounce timer starts
  → Timer expires
  → Client adds .loading class to <div>
  → Client sends server request
  → Server processes
  → Server responds
  → Client removes .loading class
  → Client applies patches
```

## Part 6: Add Filters

**Problem**: Category filter and search box need to work together.

**Solution**: Keep both filters as view state and have each handler re-run one shared search. One server render then updates the results for both.

### Step 6.1: Add Category Filter

```python
# views.py
from djust import LiveView
from djust.decorators import event_handler, debounce
from .models import Product

class ProductSearchView(LiveView):
    template_name = 'products/search.html'

    CATEGORIES = [
        ('', 'All Categories'),
        ('electronics', 'Electronics'),
        ('books', 'Books'),
        ('clothing', 'Clothing'),
    ]

    def mount(self, request, **kwargs):
        self.query = ""
        self.category = ""
        self.results = Product.objects.filter(in_stock=True)[:20]

    def _run_search(self):
        """Filter by the current query AND category. Not an event handler."""
        filters = {'in_stock': True}
        if self.query:
            filters['name__icontains'] = self.query
        if self.category:
            filters['category'] = self.category

        self.results = Product.objects.filter(**filters)[:20]

    @event_handler
    @debounce(wait=0.5)  # No @cache: update_category reads self.query (see Part 4)
    def search(self, value: str = "", **kwargs):
        """Handle search input."""
        self.query = value
        self._run_search()

    @event_handler
    def update_category(self, value: str = "", **kwargs):
        """Handle category selection. dj-change sends the choice as `value`."""
        self.category = value
        self._run_search()

    def get_context_data(self, **kwargs):
        return {
            'query': self.query,
            'category': self.category,
            'categories': self.CATEGORIES,
            'results': self.results,
            'count': self.results.count()
        }
```

### Step 6.2: Update Template

```html
<!-- templates/products/search.html -->
<!DOCTYPE html>
<html>
<head>
    <title>Product Search</title>
    {% load live_tags %}
    {% djust_client_config %}
    <style>
        .loading { opacity: 0.6; pointer-events: none; }
        .search-box { position: relative; }
        .spinner {
            display: none;
            position: absolute;
            right: 10px;
            top: 10px;
        }
        .loading .spinner { display: block; }
        .filters { margin: 20px 0; }
    </style>
</head>
<body>
    <div class="container" dj-root>
        <h1>Product Search</h1>

        <div class="search-box" dj-loading.class="loading" dj-loading.for="search">
            <input
                type="text"
                dj-input="search"
                value="{{ query }}"
                placeholder="Search products..."
                class="form-control"
            />
            <span class="spinner">🔄</span>
        </div>

        <!-- Add category filter -->
        <div class="filters">
            <label>Category:</label>
            <select dj-change="update_category" class="form-select">
                {% for value, label in categories %}
                <option value="{{ value }}" {% if value == category %}selected{% endif %}>
                    {{ label }}
                </option>
                {% endfor %}
            </select>
        </div>

        <div class="results">
            {% for product in results %}
            <div class="product-card">
                <h3>{{ product.name }}</h3>
                <p>{{ product.description }}</p>
                <p class="price">${{ product.price }}</p>
                <span class="category">{{ product.category }}</span>
            </div>
            {% empty %}
            <p>No products found.</p>
            {% endfor %}
        </div>

        <div class="count">
            Found {{ count }} products
        </div>
    </div>

</body>
</html>
```

### Step 6.3: Test the Filters

```bash
python manage.py runserver
```

**Result:**
- ✅ Change category → Search updates automatically
- ✅ Type in search box → Results filtered by category
- ✅ Both filters work together

**What Changed:**
- `self.query` and `self.category` are ordinary view state, kept on the server between events
- Each handler updates its own filter, then calls `self._run_search()`, so one server render updates the results for both
- `search` no longer has `@cache`. A cache hit would skip the handler, leaving `self.query` stale for the next `update_category`, and a key on the text alone would replay results for the wrong category

**Flow:**

```
User selects "Electronics" category
  → update_category(value="electronics") called
  → self.category = "electronics"
  → _run_search() filters by self.query + self.category
  → Server re-renders; results patched

User types "laptop"
  → search(value="laptop") called (after the 500ms debounce)
  → self.query = "laptop"
  → _run_search() filters by both
```

## Complete Example

Here's the final, fully-featured Product Search view:

```python
# views.py
from djust import LiveView
from djust.decorators import event_handler, debounce
from .models import Product

class ProductSearchView(LiveView):
    """
    Product search with:
    - Debounced search (500ms delay)
    - Loading states (visual feedback)
    - Coordinated filters (shared view state)
    """
    template_name = 'products/search.html'

    CATEGORIES = [
        ('', 'All Categories'),
        ('electronics', 'Electronics'),
        ('books', 'Books'),
        ('clothing', 'Clothing'),
        ('home', 'Home & Garden'),
    ]
    SORTS = ('name', 'price', '-price')  # Allowlist: never pass raw input to order_by()

    def mount(self, request, **kwargs):
        """Initialize state."""
        self.query = ""
        self.category = ""
        self.sort = "name"
        self.results = Product.objects.filter(in_stock=True).order_by(self.sort)[:20]

    def _run_search(self):
        """Apply the current query, category and sort. Not an event handler."""
        filters = {'in_stock': True}
        if self.query:
            filters['name__icontains'] = self.query
        if self.category:
            filters['category'] = self.category

        self.results = Product.objects.filter(**filters).order_by(self.sort)[:20]

    @event_handler
    @debounce(wait=0.5)  # Wait 500ms after last keystroke
    def search(self, value: str = "", **kwargs):
        """Handle search input. dj-input sends the text as `value`."""
        self.query = value
        self._run_search()

    @event_handler
    def update_category(self, value: str = "", **kwargs):
        """Handle category selection."""
        self.category = value
        self._run_search()

    @event_handler
    def update_sort(self, value: str = "name", **kwargs):
        """Handle sort selection."""
        self.sort = value if value in self.SORTS else "name"
        self._run_search()

    @event_handler
    def clear_filters(self, **kwargs):
        """Clear all filters."""
        self.query = ""
        self.category = ""
        self.sort = "name"
        self._run_search()

    def get_context_data(self, **kwargs):
        """Return context for template."""
        return {
            'query': self.query,
            'category': self.category,
            'sort': self.sort,
            'categories': self.CATEGORIES,
            'results': self.results,
            'count': self.results.count()
        }
```

**Template:**

```html
<!-- templates/products/search.html -->
<!DOCTYPE html>
<html>
<head>
    <title>Product Search</title>
    {% load live_tags %}
    {% djust_client_config %}
    <style>
        .container { max-width: 1200px; margin: 0 auto; padding: 20px; }
        .search-box { position: relative; margin: 20px 0; }
        .search-box input { width: 100%; padding: 10px; font-size: 16px; }
        .spinner {
            display: none;
            position: absolute;
            right: 10px;
            top: 10px;
            font-size: 20px;
        }
        .loading .spinner { display: block; }
        .loading { opacity: 0.6; pointer-events: none; }

        .filters { display: flex; gap: 20px; margin: 20px 0; }
        .filters select { padding: 8px; font-size: 14px; }

        .results { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 20px; }
        .product-card {
            border: 1px solid #ddd;
            padding: 20px;
            border-radius: 8px;
        }
        .product-card h3 { margin: 0 0 10px 0; }
        .product-card .price { font-size: 20px; color: #27ae60; font-weight: bold; }
        .product-card .category { background: #3498db; color: white; padding: 4px 8px; border-radius: 4px; font-size: 12px; }

        .count { margin: 20px 0; font-size: 14px; color: #666; }
        .clear-btn { padding: 8px 16px; background: #e74c3c; color: white; border: none; border-radius: 4px; cursor: pointer; }
    </style>
</head>
<body>
    <div class="container" dj-root>
        <h1>Product Search</h1>

        <div class="search-box" dj-loading.class="loading" dj-loading.for="search">
            <input
                type="text"
                dj-input="search"
                value="{{ query }}"
                placeholder="Search products..."
            />
            <span class="spinner">🔄</span>
        </div>

        <div class="filters">
            <div>
                <label>Category:</label>
                <select dj-change="update_category">
                    {% for value, label in categories %}
                    <option value="{{ value }}" {% if value == category %}selected{% endif %}>
                        {{ label }}
                    </option>
                    {% endfor %}
                </select>
            </div>

            <div>
                <label>Sort by:</label>
                <select dj-change="update_sort">
                    <option value="name" {% if sort == "name" %}selected{% endif %}>Name</option>
                    <option value="price" {% if sort == "price" %}selected{% endif %}>Price: Low to High</option>
                    <option value="-price" {% if sort == "-price" %}selected{% endif %}>Price: High to Low</option>
                </select>
            </div>

            <div>
                <button dj-click="clear_filters" class="clear-btn">Clear Filters</button>
            </div>
        </div>

        <div class="count">
            Found {{ count }} products
        </div>

        <div class="results">
            {% for product in results %}
            <div class="product-card">
                <h3>{{ product.name }}</h3>
                <p>{{ product.description|truncatewords:20 }}</p>
                <p class="price">${{ product.price }}</p>
                <span class="category">{{ product.category }}</span>
            </div>
            {% empty %}
            <p>No products found. Try adjusting your filters.</p>
            {% endfor %}
        </div>
    </div>

</body>
</html>
```

**Lines of Code:**

| Component | Lines | Notes |
|-----------|-------|-------|
| Python view | ~70 | Includes all logic |
| HTML template | ~100 | Includes styling |
| **JavaScript** | **0** | Zero custom JS! |

**Performance:**

- **Server requests**: 1 per query (after 500ms silence)
- **Bundle size**: ~70 KB gzipped (`client.min.js`, served automatically)

## Next Steps

### Congratulations!

You've built a fully-featured Product Search with:

- ✅ Debounced search (500ms delay)
- ✅ Response caching (60 second TTL, Part 4) and when not to use it (Part 6)
- ✅ Loading indicators (visual feedback)
- ✅ Coordinated filters (shared view state)
- ✅ **Zero custom JavaScript**

### Learn More

**Advanced Patterns:**
- [STATE_MANAGEMENT_PATTERNS.md](STATE_MANAGEMENT_PATTERNS.md) - Common patterns and anti-patterns
- [STATE_MANAGEMENT_EXAMPLES.md](STATE_MANAGEMENT_EXAMPLES.md) - More real-world examples

**API Reference:**
- [STATE_MANAGEMENT_API.md](STATE_MANAGEMENT_API.md) - Complete decorator documentation

**Migration Guide:**
- [STATE_MANAGEMENT_MIGRATION.md](STATE_MANAGEMENT_MIGRATION.md) - Migrate existing JavaScript to decorators

**Architecture:**
- [STATE_MANAGEMENT_ARCHITECTURE.md](STATE_MANAGEMENT_ARCHITECTURE.md) - How it works under the hood

### Try These Next

1. **Add Form Validation**
   ```python
   from djust.forms import FormMixin

   class ProductCreateView(FormMixin, LiveView):
       form_class = ProductForm
   ```

2. **Add a Favorite Toggle**
   ```python
   @event_handler
   def toggle_favorite(self, product_id: int = 0, **kwargs):
       # Send the id from the template: dj-click="toggle_favorite" dj-value-product_id="{{ product.id }}"
       # (@optimistic would be inert here, #2699)
       pass
   ```

3. **Add Autocomplete**
   ```python
   @event_handler
   @debounce(wait=0.3)
   @cache(ttl=300, key_params=["value"])
   def autocomplete(self, value: str = "", **kwargs):
       self.suggestions = list(Product.objects.filter(
           name__icontains=value
       ).values_list('name', flat=True)[:5])
   ```

4. **Add Infinite Scroll**
   ```python
   from djust.decorators import event_handler, throttle

   @event_handler
   @throttle(interval=1.0)
   def load_more(self, **kwargs):
       self.page += 1  # Track the page on the server; initialise self.page in mount()
       # Load next page
   ```
   Trigger it from the template with `dj-viewport-top`/`dj-viewport-bottom` (see the infinite-scroll docs).

### Questions?

- **GitHub**: [Open an issue](https://github.com/djust-org/djust/issues)
- **Docs**: [Read the full docs](https://docs.djust.org)

---

**Happy coding!** 🚀
