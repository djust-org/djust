# Navigation & URL State

djust provides URL state management inspired by Phoenix LiveView. Update the browser URL without page reloads, handle browser back/forward navigation, and navigate between views over a single WebSocket connection.

## Core Concepts

| Method | Purpose |
|--------|---------|
| `live_patch()` | Update URL params without remounting the view |
| `live_redirect()` | Navigate to a different view over the same WebSocket |
| `handle_params()` | React to URL param changes |

## live_patch — Update URL Without Remount

`live_patch()` updates the browser URL (via `history.pushState`) without remounting the view. The view re-renders with the new params, but `mount()` is NOT called again.

Use this for filters, pagination, tabs, and other state that should be URL-shareable.

```python
from djust import LiveView, event_handler

class ProductListView(LiveView):
    template_name = "products/list.html"

    def mount(self, request, **kwargs):
        # Set defaults from URL params
        self.category = request.GET.get("category", "all")
        self.page = int(request.GET.get("page", 1))
        self.products = self.get_products()

    def handle_params(self, params, uri):
        """Called when URL params change (browser back/forward or live_patch)."""
        self.category = params.get("category", "all")
        self.page = int(params.get("page", 1))
        self.products = self.get_products()

    @event_handler
    def filter_category(self, category="all", **kwargs):
        self.category = category
        self.page = 1
        self.products = self.get_products()
        
        # Update URL without remount
        self.live_patch(params={"category": category, "page": 1})

    @event_handler
    def go_to_page(self, page=1, **kwargs):
        self.page = page
        self.products = self.get_products()
        self.live_patch(params={"category": self.category, "page": page})

    def get_products(self):
        qs = Product.objects.all()
        if self.category != "all":
            qs = qs.filter(category=self.category)
        # Paginate...
        return qs
```

### live_patch Options

```python
# Update query params (merged with current URL)
self.live_patch(params={"category": "electronics", "page": 2})

# Clear all params
self.live_patch(params={})

# Change the path too
self.live_patch(path="/products/", params={"sort": "price"})

# Use replaceState instead of pushState (doesn't add to history)
self.live_patch(params={"page": 2}, replace=True)
```

## Template Directives

### dj-patch — Declarative URL Updates

Use `dj-patch` on links to update URL params without a full page reload:

```html
<nav class="filters">
    <!-- Category filter tabs -->
    <a dj-patch="?category=all" class="{{ 'active' if category == 'all' }}">All</a>
    <a dj-patch="?category=electronics">Electronics</a>
    <a dj-patch="?category=clothing">Clothing</a>
</nav>

<div class="pagination">
    <a dj-patch="?page={{ page - 1 }}" {% if page == 1 %}disabled{% endif %}>Previous</a>
    <a dj-patch="?page={{ page + 1 }}">Next</a>
</div>
```

When clicked, `dj-patch` links:
1. Update the browser URL
2. Call `handle_params()` with the new params
3. Re-render the view

### dj-navigate — Navigate to Different Views

Use `dj-navigate` for navigation between different LiveViews:

```html
<div class="product-card">
    <h3>{{ product.name }}</h3>
    <a dj-navigate="/products/{{ product.id }}/">View Details</a>
</div>
```

## live_redirect — Navigate to Another View

`live_redirect()` navigates to a different LiveView over the same WebSocket connection. The current view is unmounted and the new view is mounted—no page reload.

```python
from djust import LiveView, event_handler

class CartView(LiveView):
    template_name = "cart.html"

    @event_handler
    def checkout(self):
        order = self.create_order()
        # Navigate to the order confirmation view
        self.live_redirect(f"/orders/{order.id}/confirmation/")

    @event_handler
    def go_to_product(self, product_id, **kwargs):
        self.live_redirect(f"/products/{product_id}/")
```

### live_redirect Options

```python
# Simple navigation
self.live_redirect("/dashboard/")

# With query params
self.live_redirect("/search/", params={"q": "laptop", "sort": "price"})

# Replace current history entry
self.live_redirect("/login/", replace=True)
```

## handle_params — React to URL Changes

Override `handle_params()` to react when URL params change. This is called:
- When browser back/forward is pressed
- When `live_patch()` is called
- When a `dj-patch` link is clicked

```python
class SearchView(LiveView):
    template_name = "search.html"

    def mount(self, request, **kwargs):
        self.query = request.GET.get("q", "")
        self.sort = request.GET.get("sort", "relevance")
        self.results = self.search()

    def handle_params(self, params, uri):
        """
        Called when URL params change.
        
        Args:
            params: Dict of new URL query parameters
            uri: The full URI string (path + query)
        """
        self.query = params.get("q", "")
        self.sort = params.get("sort", "relevance")
        self.results = self.search()

    @event_handler
    def update_search(self, query="", **kwargs):
        self.query = query
        self.results = self.search()
        self.live_patch(params={"q": query, "sort": self.sort})

    def search(self):
        return Product.objects.filter(name__icontains=self.query)
```

## live_session — Grouped Routing

Use `live_session()` to group related views. Views within a session share the WebSocket connection when using `live_redirect()`.

```python
# urls.py
from django.urls import path
from djust.routing import live_session

urlpatterns = [
    *live_session("/app", [
        path("", DashboardView.as_view(), name="dashboard"),
        path("settings/", SettingsView.as_view(), name="settings"),
        path("profile/", ProfileView.as_view(), name="profile"),
        path("items/<int:id>/", ItemDetailView.as_view(), name="item_detail"),
    ]),
]
```

With this setup, navigating between `/app/`, `/app/settings/`, and `/app/profile/` won't disconnect the WebSocket.

### Route Map for Client-Side Navigation

Include the route map script in your base template so `dj-navigate` can resolve paths:

```html
<!-- base.html -->
{% load djust_tags %}
<!DOCTYPE html>
<html>
<head>...</head>
<body>
    {% block content %}{% endblock %}
    
    {% djust_route_map %}  <!-- Emits route map for live_redirect -->
    <script src="{% static 'djust/client.js' %}"></script>
</body>
</html>
```

## Browser History

djust integrates with browser history automatically:

- **Back/Forward**: Triggers `handle_params()` with the previous/next URL params
- **live_patch**: Uses `pushState` by default (adds to history)
- **live_redirect**: Uses `pushState` by default
- **replace=True**: Uses `replaceState` (doesn't add to history)

## Full Example: Filterable Product List

```python
# views.py
from djust import LiveView, event_handler

class ProductListView(LiveView):
    template_name = "products/list.html"

    def mount(self, request, **kwargs):
        self.category = request.GET.get("category", "all")
        self.sort = request.GET.get("sort", "name")
        self.page = int(request.GET.get("page", 1))
        self.load_products()

    def handle_params(self, params, uri):
        self.category = params.get("category", "all")
        self.sort = params.get("sort", "name")
        self.page = int(params.get("page", 1))
        self.load_products()

    def load_products(self):
        qs = Product.objects.all()
        if self.category != "all":
            qs = qs.filter(category=self.category)
        qs = qs.order_by(self.sort)
        self.products = qs[(self.page-1)*20:self.page*20]
        self.total_pages = (qs.count() + 19) // 20

    @event_handler
    def set_sort(self, sort="name", **kwargs):
        self.sort = sort
        self.page = 1
        self.load_products()
        self.live_patch(params={
            "category": self.category,
            "sort": sort,
            "page": 1
        })
```

```html
<!-- templates/products/list.html -->
<div class="product-list">
    <nav class="filters">
        <a dj-patch="?category=all&sort={{ sort }}">All</a>
        <a dj-patch="?category=electronics&sort={{ sort }}">Electronics</a>
        <a dj-patch="?category=clothing&sort={{ sort }}">Clothing</a>
    </nav>

    <select dj-change="set_sort" name="sort">
        <option value="name" {% if sort == "name" %}selected{% endif %}>Name</option>
        <option value="price" {% if sort == "price" %}selected{% endif %}>Price</option>
        <option value="-created" {% if sort == "-created" %}selected{% endif %}>Newest</option>
    </select>

    <div class="products">
        {% for product in products %}
        <div class="product-card">
            <a dj-navigate="/products/{{ product.id }}/">
                <h3>{{ product.name }}</h3>
                <p>${{ product.price }}</p>
            </a>
        </div>
        {% endfor %}
    </div>

    <div class="pagination">
        {% if page > 1 %}
            <a dj-patch="?category={{ category }}&sort={{ sort }}&page={{ page|add:'-1' }}">Previous</a>
        {% endif %}
        <span>Page {{ page }} of {{ total_pages }}</span>
        {% if page < total_pages %}
            <a dj-patch="?category={{ category }}&sort={{ sort }}&page={{ page|add:'1' }}">Next</a>
        {% endif %}
    </div>
</div>
```

## Tips

1. **URL as single source of truth**: For shareable state (filters, pagination), let `handle_params()` update your view state. This ensures bookmarks and shared links work correctly.

2. **replace=True for transient state**: Use `replace=True` for state that shouldn't pollute browser history (like intermediate loading states).

3. **Combine with dj-debounce**: For search inputs, debounce the live_patch to avoid excessive history entries:
   ```html
   <input dj-input="search" dj-debounce="500">
   ```

4. **Use live_session for related views**: Group related views so navigation between them feels instant.
