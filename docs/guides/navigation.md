# Navigation (URL State Management)

djust provides `live_patch()` and `live_redirect()` for managing URL state without full page reloads, inspired by Phoenix LiveView.

## Overview

- **live_patch()** - Update URL query params without remounting the view. The view re-renders but `mount()` is not called again.
- **live_redirect()** - Navigate to a different LiveView over the existing WebSocket. The current view is unmounted and the new view is mounted without a page reload or reconnection.
- **Template directives** - `dj-patch` and `dj-navigate` for declarative navigation in HTML.
- **Browser history** - Full back/forward support via `popstate` handling.

## Quick Start

### 1. Add NavigationMixin to your view

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
        self.category = category
        self.live_patch(params={"category": category, "page": 1})
```

### 2. Use navigation directives in templates

```html
<!-- dj-patch: Update URL params without remount -->
<a dj-patch="?category=electronics&page=1">Electronics</a>
<a dj-patch="?category=books&page=1">Books</a>

<!-- dj-navigate: Navigate to a different view -->
<a dj-navigate="/products/{{ product.id }}/">View Details</a>
```

### 3. Set up the route map for live_redirect

For `live_redirect()` and `dj-navigate` to resolve view paths, populate the client-side route map:

```html
<script>
  window.djust._routeMap = {
    "/products/": "myapp.views.ProductListView",
    "/products/:id/": "myapp.views.ProductDetailView",
  };
</script>
```

## API Reference

### NavigationMixin Methods

#### `live_patch(params=None, path=None, replace=False)`

Update the browser URL without remounting the view. Triggers `handle_params()` and a re-render.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `params` | `dict` or `None` | `None` | Query parameters to set. `None` keeps current params, `{}` clears them. |
| `path` | `str` or `None` | `None` | New URL path. Defaults to current path. |
| `replace` | `bool` | `False` | Use `replaceState` instead of `pushState`. |

```python
# Update query params only
self.live_patch(params={"page": 2})

# Change path and params
self.live_patch(path="/search/", params={"q": "django"})

# Replace current history entry (no back button entry)
self.live_patch(params={"sort": "price"}, replace=True)
```

#### `live_redirect(path, params=None, replace=False)`

Navigate to a different LiveView over the existing WebSocket connection. The current view is unmounted and the new view is mounted.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `path` | `str` | required | URL path to navigate to. |
| `params` | `dict` or `None` | `None` | Optional query parameters. |
| `replace` | `bool` | `False` | Use `replaceState` instead of `pushState`. |

```python
# Navigate to a different view
self.live_redirect("/items/42/")

# With query params
self.live_redirect("/search/", params={"q": "widgets"})
```

#### `handle_params(params, uri)`

Callback invoked when URL params change via `live_patch()` or browser back/forward navigation. Override this to update view state based on URL.

| Parameter | Type | Description |
|-----------|------|-------------|
| `params` | `dict` | URL query parameters as a dictionary. |
| `uri` | `str` | The full URI string. |

```python
def handle_params(self, params, uri):
    self.category = params.get("category", "all")
    self.page = int(params.get("page", 1))
    self.results = self.search(self.category, self.page)
```

### Template Directives

#### `dj-patch`

Declarative `live_patch`. On click, updates the URL and sends `url_change` to the server.

```html
<a dj-patch="?sort=name&order=asc">Sort by Name</a>
<a dj-patch="/products/?category=new">New Products</a>
```

#### `dj-navigate`

Declarative `live_redirect`. On click, navigates to a different view over the WebSocket.

```html
<a dj-navigate="/dashboard/">Go to Dashboard</a>
<a dj-navigate="/items/{{ item.id }}/">View Item</a>
```

## Examples

### Filtering with URL Params

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
        self.query = value
        self.live_patch(params={"q": value, "sort": self.sort})

    @event_handler()
    def change_sort(self, sort="relevance", **kwargs):
        self.sort = sort
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

### Multi-Page App Navigation

```python
class DashboardView(NavigationMixin, LiveView):
    template_name = 'dashboard.html'

    def mount(self, request, **kwargs):
        self.tab = "overview"

    def handle_params(self, params, uri):
        self.tab = params.get("tab", "overview")

    @event_handler()
    def switch_tab(self, tab="overview", **kwargs):
        self.tab = tab
        self.live_patch(params={"tab": tab})

    @event_handler()
    def go_to_settings(self, **kwargs):
        self.live_redirect("/settings/")
```

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

**What actually happens:**
1. User clicks button → Client sends WebSocket message (50-100ms)
2. Server receives message, processes handler (10-50ms)
3. Server responds with `live_redirect` command (50-100ms)
4. Client finally navigates to new view

**Total: 110-250ms** + handler processing time

**✅ Right** — using `dj-navigate` directly:

```html
<!-- Right: Client navigates immediately, no server round-trip -->
<a dj-navigate="/items/{{ item.id }}/">View Item</a>
```

**What happens:**
1. User clicks link → Client navigates directly

**Total: ~10ms** (just DOM updates)

**Why it matters:**
- **Performance:** 10-20x faster navigation
- **Network efficiency:** Saves WebSocket bandwidth
- **User experience:** Instant response, no loading indicators needed
- **Simplicity:** Less code, fewer moving parts

#### When to Use `live_redirect()` in Handlers

Use handlers for navigation only when navigation depends on **server-side logic or validation**:

**✅ Conditional navigation after form validation:**

```python
@event_handler()
def submit_form(self, **kwargs):
    if self.form.is_valid():
        self.form.save()
        self.live_redirect("/success/")  # OK: Conditional on validation
    else:
        # Stay on form to show errors
        pass
```

**✅ Navigation based on auth/permissions:**

```python
@event_handler()
def view_sensitive_data(self, **kwargs):
    if not self.request.user.has_perm('app.view_sensitive'):
        self.live_redirect("/access-denied/")  # OK: Auth check required
        return
    self.show_sensitive = True
```

**✅ Navigation after async operations:**

```python
@event_handler()
async def create_and_view_item(self, name, **kwargs):
    item = await Item.objects.acreate(name=name, owner=self.request.user)
    self.live_redirect(f"/items/{item.id}/")  # OK: Navigate to newly created item
```

**Common theme:** The handler does **meaningful work** before navigating. If your handler only calls `live_redirect()`, use `dj-navigate` instead.

### When to Use Patch vs Redirect

| Use `live_patch()` when... | Use `live_redirect()` when... |
|---|---|
| Filtering, sorting, paginating | Navigating to a completely different page |
| Changing tabs within the same view | Moving between list and detail views |
| Updating search parameters | Redirecting after a form submission |
| You want `mount()` to NOT be called again | You need a fresh `mount()` call |

### URL Design

- Use query params for filter/sort/page state that should be shareable and bookmarkable.
- Use `replace=True` for transient state changes (e.g., intermediate typing) to avoid polluting browser history.
- Always implement `handle_params()` to restore state from URL -- this ensures deep links and browser back/forward work correctly.
- Keep URL params flat and simple: `?category=books&page=2` rather than nested structures.
