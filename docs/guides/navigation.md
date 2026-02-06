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
