---
title: "Navigation & URL State"
slug: navigation
section: guides
order: 4
level: intermediate
description: "SPA-like navigation with live_patch, live_redirect, and dj-navigate directives"
---

# Navigation & URL State

djust provides `live_patch()` and `live_redirect()` for managing URL state without full page reloads, inspired by Phoenix LiveView. Bookmark-friendly URLs, browser back/forward, and deep linking all work out of the box.

## What You Get

- **`live_patch()`** -- Update URL query params without remounting the view
- **`live_redirect()`** -- Navigate to a different LiveView over the existing WebSocket
- **Template directives** -- `dj-patch` and `dj-navigate` for declarative navigation
- **Browser history** -- Full back/forward support via `popstate` handling

## Quick Start

### 1. Add NavigationMixin to Your View

```python
from djust import LiveView
from djust.mixins.navigation import NavigationMixin
from djust.decorators import event_handler

class ProductListView(NavigationMixin, LiveView):
    template_name = 'products/list.html'

    def mount(self, request, **kwargs):
        self.category = "all"
        self.page = 1
        self.products = []

    def handle_params(self, params, uri):
        """Called when URL params change (live_patch or browser back/forward)."""
        self.category = params.get("category", "all")
        self.page = int(params.get("page", 1))
        self.products = self.fetch_products()

    @event_handler()
    def filter_by_category(self, category="all", **kwargs):
        self.live_patch(params={"category": category, "page": 1})
```

### 2. Use Navigation Directives

```html
<!-- Update URL params without remount -->
<a dj-patch="?category=electronics&page=1">Electronics</a>
<a dj-patch="?category=books&page=1">Books</a>

<!-- Navigate to a different view -->
<a dj-navigate="/products/{{ product.id }}/">View Details</a>
```

## API Reference

### `live_patch(params=None, path=None, replace=False)`

Update the browser URL without remounting the view. Triggers `handle_params()` and a re-render. `mount()` is NOT called again.

```python
# Update query params only
self.live_patch(params={"page": 2})

# Change path and params
self.live_patch(path="/search/", params={"q": "django"})

# Replace current history entry (no back button entry)
self.live_patch(params={"sort": "price"}, replace=True)
```

### `live_redirect(path, params=None, replace=False)`

Navigate to a different LiveView over the existing WebSocket. The current view is unmounted and the new view is mounted fresh.

```python
self.live_redirect("/items/42/")
self.live_redirect("/search/", params={"q": "widgets"})
```

### `handle_params(params, uri)`

Callback invoked when URL params change. Override this to update view state based on the URL.

```python
def handle_params(self, params, uri):
    self.category = params.get("category", "all")
    self.page = int(params.get("page", 1))
    self.results = self.search(self.category, self.page)
```

## Template Directives

### `dj-patch`

Declarative `live_patch`. Updates the URL and sends `url_change` to the server without remounting.

```html
<a dj-patch="?sort=name&order=asc">Sort by Name</a>
<a dj-patch="/products/?category=new">New Products</a>
<a dj-patch="/">Home (root path)</a>
```

Patching to the root path `/` is supported and correctly updates the browser URL.

### `dj-navigate`

Declarative `live_redirect`. Navigates to a different view over the WebSocket.

```html
<a dj-navigate="/dashboard/">Go to Dashboard</a>
<a dj-navigate="/items/{{ item.id }}/">View Item</a>
```

## Example: Search with URL State

```python
class SearchView(NavigationMixin, LiveView):
    template_name = 'search.html'

    def mount(self, request, **kwargs):
        self.query = ""
        self.sort = "relevance"
        self.results = []

    def handle_params(self, params, uri):
        self.query = params.get("q", "")
        self.sort = params.get("sort", "relevance")
        if self.query:
            self.results = Product.objects.filter(
                name__icontains=self.query
            ).order_by(self.sort)

    @event_handler()
    def search(self, value="", **kwargs):
        self.live_patch(params={"q": value, "sort": self.sort})

    @event_handler()
    def change_sort(self, sort="relevance", **kwargs):
        self.live_patch(params={"q": self.query, "sort": sort})
```

```html
<input type="text" dj-change="search" value="{{ query }}">

<div class="sort-options">
    <a dj-patch="?q={{ query }}&sort=relevance">Relevance</a>
    <a dj-patch="?q={{ query }}&sort=price">Price</a>
    <a dj-patch="?q={{ query }}&sort=-created">Newest</a>
</div>

{% for product in results %}
    <div class="product">
        <h3><a dj-navigate="/products/{{ product.id }}/">{{ product.name }}</a></h3>
        <p>${{ product.price }}</p>
    </div>
{% endfor %}
```

## When to Use Patch vs Redirect

| Use `live_patch()` | Use `live_redirect()` |
|---|---|
| Filtering, sorting, paginating | Navigating to a different page |
| Changing tabs within the same view | Moving between list and detail views |
| Updating search parameters | Redirecting after form submission |
| You want `mount()` NOT called again | You need a fresh `mount()` call |

## Best Practices

### ⚠️ Anti-Pattern: Don't Use `dj-click` for Navigation

This is **the most common mistake** when building multi-view djust apps. Using `dj-click` to trigger a handler that immediately calls `live_redirect()` creates an unnecessary round-trip.

**❌ Wrong** — using `dj-click` to trigger a handler that calls `live_redirect()`:

```python
# Anti-pattern: Handler does nothing but navigate
@event_handler()
def go_to_item(self, item_id, **kwargs):
    self.live_redirect(f"/items/{item_id}/")  # Wasteful round-trip!
```

```html
<!-- Wrong: Forces WebSocket round-trip just to navigate -->
<button dj-click="go_to_item" dj-value-item_id="{{ item.id }}">View</button>
```

**✅ Right** — using `dj-navigate` directly:

```html
<!-- Right: Client navigates immediately, no server round-trip -->
<a dj-navigate="/items/{{ item.id }}/">View Item</a>
```

**Why it matters:** Direct navigation is 10-20x faster (~10ms vs 110-250ms), saves WebSocket bandwidth, and provides instant user feedback.

#### When to Use `live_redirect()` in Handlers

Use handlers for navigation only when navigation depends on **server-side logic**:

- **Conditional navigation** after form validation
- **Navigation based on** auth/permissions checks
- **Navigation after** async operations (creating records, API calls)
- **Multi-step wizard** logic with conditional flow

**Common theme:** The handler does **meaningful work** before navigating. If your handler only calls `live_redirect()`, use `dj-navigate` instead.

### URL Design Best Practices

- Use query params for filter/sort/page state that should be shareable and bookmarkable.
- Use `replace=True` for transient state changes (e.g., intermediate typing) to avoid polluting browser history.
- Always implement `handle_params()` to restore state from URL -- this ensures deep links and browser back/forward work correctly.
- Keep URL params flat and simple: `?category=books&page=2` rather than nested structures.
