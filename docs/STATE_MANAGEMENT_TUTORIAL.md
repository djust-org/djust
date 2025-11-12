# State Management Tutorial

**Level**: Intermediate
**Duration**: 30-45 minutes
**Goal**: Build a Product Search feature using djust state management decorators

This tutorial walks through building a real-world Product Search feature step-by-step, progressively adding decorators to improve user experience and reduce server load.

## Table of Contents

- [What You'll Build](#what-youll-build)
- [Prerequisites](#prerequisites)
- [Part 1: Basic LiveView](#part-1-basic-liveview)
- [Part 2: Add Debouncing](#part-2-add-debouncing)
- [Part 3: Add Optimistic Updates](#part-3-add-optimistic-updates)
- [Part 4: Add Response Caching](#part-4-add-response-caching)
- [Part 5: Add Loading States](#part-5-add-loading-states)
- [Part 6: Add Filters with StateBus](#part-6-add-filters-with-statebus)
- [Complete Example](#complete-example)
- [Next Steps](#next-steps)

## What You'll Build

A Product Search page with:

- **Real-time search** as user types
- **Debounced requests** (wait for user to stop typing)
- **Optimistic updates** (instant feedback)
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
- User types "l" → UI updates instantly (optimistic)
- User types "la" → Previous request cancelled (debounced)
- User types "laptop" → Wait 500ms, then search server
- Server returns → Update results (42 products)
- User types "laptop" again → Cached response (no server request)

## Prerequisites

1. **djust installed** (version 0.4.0+)
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
from .models import Product

class ProductSearchView(LiveView):
    template_name = 'products/search.html'

    def mount(self, request, **kwargs):
        """Initialize state on first load."""
        self.query = ""
        self.results = Product.objects.filter(in_stock=True)[:20]

    def search(self, query: str = "", **kwargs):
        """Handle search input."""
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
    {% load djust %}
    {% djust_head %}
</head>
<body>
    <div class="container">
        <h1>Product Search</h1>

        <div class="search-box">
            <input
                type="text"
                @input="search"
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

    {% djust_body %}
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
- ❌ Every keystroke sends server request (slow!)
- ❌ No feedback while loading
- ❌ Searches for "laptop" sent 6 times (l-a-p-t-o-p)

**Problems:**
1. **Too many requests**: Typing "laptop" sends 6 server requests
2. **No loading feedback**: User doesn't know if search is running
3. **Redundant searches**: Same query searched multiple times

Let's fix these issues step by step.

## Part 2: Add Debouncing

**Problem**: Every keystroke sends a server request.

**Solution**: Use `@debounce` to wait until user stops typing.

### Step 2.1: Add @debounce Decorator

```python
# views.py
from djust import LiveView
from djust.decorators import debounce  # ← Import decorator
from .models import Product

class ProductSearchView(LiveView):
    template_name = 'products/search.html'

    def mount(self, request, **kwargs):
        self.query = ""
        self.results = Product.objects.filter(in_stock=True)[:20]

    @debounce(wait=0.5)  # ← Wait 500ms after last keystroke
    def search(self, query: str = "", **kwargs):
        """Handle search input (debounced)."""
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
- ✅ Server load reduced by 83%
- ❌ UI doesn't update until server responds (feels slow)

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
500ms silence      → Client sends search("laptop")
```

## Part 3: Add Optimistic Updates

**Problem**: UI doesn't update until server responds (feels slow).

**Solution**: Use `@optimistic` to update UI instantly.

### Step 3.1: Add @optimistic Decorator

```python
# views.py
from djust import LiveView
from djust.decorators import debounce, optimistic  # ← Import optimistic
from .models import Product

class ProductSearchView(LiveView):
    template_name = 'products/search.html'

    def mount(self, request, **kwargs):
        self.query = ""
        self.results = Product.objects.filter(in_stock=True)[:20]

    @debounce(wait=0.5)
    @optimistic  # ← Apply optimistic updates
    def search(self, query: str = "", **kwargs):
        """Handle search input (debounced + optimistic)."""
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

### Step 3.2: Test Optimistic Updates

```bash
python manage.py runserver
```

**Result:**
- ✅ Input value updates **instantly** (no delay)
- ✅ Server request still debounced (500ms)
- ✅ Feels like native app

**What Changed:**
- `@optimistic` makes the client update the input field immediately
- Server still processes the search after 500ms
- If server response differs, UI corrects itself

**Flow:**

```
User types "l"
  → Client updates input to "l" instantly (optimistic)
  → Client starts 500ms timer

User types "laptop"
  → Client updates input to "laptop" instantly
  → Client resets timer

500ms silence
  → Client sends search("laptop")
  → Server processes search
  → Server sends results
  → Client updates results list
```

**Why This Works:**
- Optimistic updates are **heuristic-based** (client guesses)
- For input fields, guess is simple: `input.value = event.value`
- Server sends corrective patches if guess was wrong

## Part 4: Add Response Caching

**Problem**: Same query searched multiple times wastes server resources.

**Solution**: Use `@cache` to cache responses client-side.

### Step 4.1: Add @cache Decorator

```python
# views.py
from djust import LiveView
from djust.decorators import debounce, optimistic, cache  # ← Import cache
from .models import Product

class ProductSearchView(LiveView):
    template_name = 'products/search.html'

    def mount(self, request, **kwargs):
        self.query = ""
        self.results = Product.objects.filter(in_stock=True)[:20]

    @debounce(wait=0.5)
    @optimistic
    @cache(ttl=60, key_params=["query"])  # ← Cache for 60 seconds
    def search(self, query: str = "", **kwargs):
        """Handle search input (debounced + optimistic + cached)."""
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
- `key_params=["query"]` uses query value as cache key
- Cache key: `"search:laptop"`, `"search:mouse"`, etc.

**Cache Behavior:**

```
User searches "laptop"
  → Client checks cache for "search:laptop" → Miss
  → Client sends server request
  → Server responds with results
  → Client stores in cache (TTL: 60s)
  → Client displays results

User searches "mouse"
  → Client checks cache for "search:mouse" → Miss
  → Client sends server request
  → (cached separately)

User searches "laptop" again
  → Client checks cache for "search:laptop" → Hit!
  → Client displays cached results (no server request)
```

**Cache Invalidation:**
- Automatic after TTL (60 seconds)
- Manual: `window.responseCache.clear()`

## Part 5: Add Loading States

**Problem**: User doesn't know when search is running.

**Solution**: Use `@loading` attribute to show loading indicator.

### Step 5.1: Update Template

```html
<!-- templates/products/search.html -->
<!DOCTYPE html>
<html>
<head>
    <title>Product Search</title>
    {% load djust %}
    {% djust_head %}
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
    <div class="container">
        <h1>Product Search</h1>

        <!-- Add @loading attribute -->
        <div class="search-box" @loading>
            <input
                type="text"
                @input="search"
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

    {% djust_body %}
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
- `@loading` attribute automatically adds/removes `.loading` class
- CSS handles visual feedback
- No JavaScript required!

**Loading Flow:**

```
User types "laptop"
  → Client applies optimistic update (input changes)
  → 500ms debounce timer starts
  → Timer expires
  → Client adds .loading class to <div>
  → Client sends server request
  → Server processes
  → Server responds
  → Client removes .loading class
  → Client applies patches
```

## Part 6: Add Filters with StateBus

**Problem**: Category filter and search box need to work together.

**Solution**: Use `@client_state` to share state between handlers.

### Step 6.1: Add Category Filter

```python
# views.py
from djust import LiveView
from djust.decorators import debounce, optimistic, cache, client_state
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

    @debounce(wait=0.5)
    @optimistic
    @cache(ttl=60, key_params=["query", "category"])  # ← Cache by both params
    @client_state(keys=["query", "category"])  # ← Share state via StateBus
    def search(self, query: str = "", category: str = "", **kwargs):
        """Handle search input."""
        self.query = query
        self.category = category

        # Build filter
        filters = {'in_stock': True}
        if query:
            filters['name__icontains'] = query
        if category:
            filters['category'] = category

        self.results = Product.objects.filter(**filters)[:20]

    @client_state(keys=["category"])  # ← Publish category changes
    def update_category(self, category: str = "", **kwargs):
        """Handle category selection."""
        self.category = category

        # Trigger search with current query
        self.search(query=self.query, category=category)

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
    {% load djust %}
    {% djust_head %}
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
    <div class="container">
        <h1>Product Search</h1>

        <div class="search-box" @loading>
            <input
                type="text"
                @input="search"
                value="{{ query }}"
                placeholder="Search products..."
                class="form-control"
            />
            <span class="spinner">🔄</span>
        </div>

        <!-- Add category filter -->
        <div class="filters">
            <label>Category:</label>
            <select @change="update_category" class="form-select">
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

    {% djust_body %}
</body>
</html>
```

### Step 6.3: Test StateBus

```bash
python manage.py runserver
```

**Result:**
- ✅ Change category → Search updates automatically
- ✅ Type in search box → Results filtered by category
- ✅ Both filters work together
- ✅ Cached by both query + category

**What Changed:**
- `@client_state(keys=["query", "category"])` publishes state to StateBus
- When category changes, search handler receives update
- Cache key includes both parameters

**StateBus Flow:**

```
User selects "Electronics" category
  → update_category("electronics") called
  → Publishes category="electronics" to StateBus
  → StateBus notifies search handler
  → search(query=current, category="electronics") called
  → Results filtered

User types "laptop"
  → search(query="laptop", category="electronics") called
  → Results filtered by both
  → Cached as "search:laptop:electronics"
```

## Complete Example

Here's the final, fully-featured Product Search view:

```python
# views.py
from djust import LiveView
from djust.decorators import debounce, optimistic, cache, client_state
from .models import Product

class ProductSearchView(LiveView):
    """
    Product search with:
    - Debounced search (500ms delay)
    - Optimistic updates (instant feedback)
    - Response caching (60 second TTL)
    - Loading states (visual feedback)
    - Coordinated filters (StateBus)
    """
    template_name = 'products/search.html'

    CATEGORIES = [
        ('', 'All Categories'),
        ('electronics', 'Electronics'),
        ('books', 'Books'),
        ('clothing', 'Clothing'),
        ('home', 'Home & Garden'),
    ]

    def mount(self, request, **kwargs):
        """Initialize state."""
        self.query = ""
        self.category = ""
        self.sort = "name"
        self.results = Product.objects.filter(in_stock=True).order_by(self.sort)[:20]

    @debounce(wait=0.5)  # Wait 500ms after last keystroke
    @optimistic           # Update UI instantly
    @cache(ttl=60, key_params=["query", "category", "sort"])  # Cache responses
    @client_state(keys=["query", "category", "sort"])  # Share state
    def search(self, query: str = "", category: str = "", sort: str = "name", **kwargs):
        """
        Handle search with filters.

        Decorators apply automatically:
        - User types → optimistic update (instant)
        - 500ms silence → debounced send
        - Cache check → return cached if hit
        - Server processes → update results
        """
        self.query = query
        self.category = category
        self.sort = sort

        # Build filter
        filters = {'in_stock': True}
        if query:
            filters['name__icontains'] = query
        if category:
            filters['category'] = category

        # Apply sort
        self.results = Product.objects.filter(**filters).order_by(sort)[:20]

    @client_state(keys=["category"])
    def update_category(self, category: str = "", **kwargs):
        """Handle category selection."""
        self.category = category
        self.search(query=self.query, category=category, sort=self.sort)

    @client_state(keys=["sort"])
    def update_sort(self, sort: str = "name", **kwargs):
        """Handle sort selection."""
        self.sort = sort
        self.search(query=self.query, category=self.category, sort=sort)

    def clear_filters(self, **kwargs):
        """Clear all filters."""
        self.query = ""
        self.category = ""
        self.sort = "name"
        self.results = Product.objects.filter(in_stock=True).order_by('name')[:20]

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
    {% load djust %}
    {% djust_head %}
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
    <div class="container">
        <h1>Product Search</h1>

        <div class="search-box" @loading>
            <input
                type="text"
                @input="search"
                value="{{ query }}"
                placeholder="Search products..."
            />
            <span class="spinner">🔄</span>
        </div>

        <div class="filters">
            <div>
                <label>Category:</label>
                <select @change="update_category">
                    {% for value, label in categories %}
                    <option value="{{ value }}" {% if value == category %}selected{% endif %}>
                        {{ label }}
                    </option>
                    {% endfor %}
                </select>
            </div>

            <div>
                <label>Sort by:</label>
                <select @change="update_sort">
                    <option value="name" {% if sort == "name" %}selected{% endif %}>Name</option>
                    <option value="price" {% if sort == "price" %}selected{% endif %}>Price: Low to High</option>
                    <option value="-price" {% if sort == "-price" %}selected{% endif %}>Price: High to Low</option>
                </select>
            </div>

            <div>
                <button @click="clear_filters" class="clear-btn">Clear Filters</button>
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

    {% djust_body %}
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
- **Cache hit rate**: ~40% (typical for search)
- **Perceived latency**: 0ms (optimistic updates)
- **Bundle size**: 7.1 KB (gzipped client.js)

## Next Steps

### Congratulations!

You've built a fully-featured Product Search with:

- ✅ Debounced search (500ms delay)
- ✅ Optimistic updates (instant feedback)
- ✅ Response caching (60 second TTL)
- ✅ Loading indicators (visual feedback)
- ✅ Coordinated filters (StateBus)
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

2. **Add Real-Time Updates**
   ```python
   @optimistic
   def toggle_favorite(self, product_id: int = 0, **kwargs):
       # Instant UI update, server validates
       pass
   ```

3. **Add Autocomplete**
   ```python
   @debounce(wait=0.3)
   @cache(ttl=300)
   def autocomplete(self, query: str = "", **kwargs):
       self.suggestions = Product.objects.filter(
           name__icontains=query
       ).values_list('name', flat=True)[:5]
   ```

4. **Add Infinite Scroll**
   ```python
   @throttle(interval=1.0)
   def load_more(self, page: int = 1, **kwargs):
       self.page = page
       # Load next page
   ```

### Questions?

- **Discord**: [Join our community](https://discord.gg/djust)
- **GitHub**: [Open an issue](https://github.com/yourusername/djust/issues)
- **Docs**: [Read the full docs](https://djust.readthedocs.io)

---

**Happy coding!** 🚀
