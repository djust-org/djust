# Navigation & URL State

djust provides Phoenix LiveView-inspired URL state management. Update browser URLs without page reloads, navigate between views over a single WebSocket, and respond to browser back/forward events.

## Core Concepts

- **`live_patch()`** — Update URL params without remounting the view
- **`live_redirect()`** — Navigate to a different view over the same WebSocket
- **`handle_params()`** — Callback when URL params change
- **`live_session()`** — Group views that share a WebSocket connection

## Server-Side API

### `live_patch(params=None, path=None, replace=False)`

Update the browser URL without remounting the view. The view re-renders with new state, but `mount()` is **not** called again.

```python
from djust import LiveView
from djust.decorators import event_handler

class ProductListView(LiveView):
    template_name = "products.html"

    def mount(self, request, **kwargs):
        self.category = request.GET.get("category", "all")
        self.page = int(request.GET.get("page", 1))
        self.load_products()

    @event_handler
    def filter_category(self, category="all", **kwargs):
        self.category = category
        self.page = 1
        self.load_products()
        # URL updates to ?category=electronics&page=1 — no remount
        self.live_patch(params={"category": category, "page": 1})

    @event_handler
    def next_page(self, **kwargs):
        self.page += 1
        self.load_products()
        # Use replace=True to not add history entry
        self.live_patch(params={"page": self.page}, replace=True)
```

**Arguments:**

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `params` | `dict` | `None` | Query params to set. `None` keeps current, `{}` clears |
| `path` | `str` | `None` | New URL path. Defaults to current path |
| `replace` | `bool` | `False` | Use `replaceState` instead of `pushState` |

### `live_redirect(path, params=None, replace=False)`

Navigate to a different LiveView over the existing WebSocket connection. The current view unmounts and the new view mounts — no page reload.

```python
class ProductListView(LiveView):
    @event_handler
    def view_details(self, product_id, **kwargs):
        # Navigate to detail view over same WebSocket
        self.live_redirect(f"/products/{product_id}/")

class ProductDetailView(LiveView):
    @event_handler
    def back_to_list(self, **kwargs):
        self.live_redirect("/products/", params={"category": self.category})
```

**Arguments:**

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `path` | `str` | **required** | URL path to navigate to |
| `params` | `dict` | `None` | Query parameters for the new URL |
| `replace` | `bool` | `False` | Use `replaceState` instead of `pushState` |

### `handle_params(params, uri)`

Called when URL params change via `live_patch` or browser back/forward. Override this to update view state based on URL.

```python
class SearchView(LiveView):
    def mount(self, request, **kwargs):
        self.query = request.GET.get("q", "")
        self.page = int(request.GET.get("page", 1))
        self.search()

    def handle_params(self, params, uri):
        """Called on browser back/forward and live_patch"""
        self.query = params.get("q", "")
        self.page = int(params.get("page", 1))
        self.search()

    @event_handler
    def do_search(self, query, **kwargs):
        self.query = query
        self.page = 1
        self.search()
        self.live_patch(params={"q": query, "page": 1})
```

## Template Directives

### `dj-patch`

Update URL params without remount when clicked. Equivalent to calling `live_patch()`.

```html
<!-- Filter links -->
<a dj-patch="?category=all">All</a>
<a dj-patch="?category=electronics">Electronics</a>
<a dj-patch="?category=books&page=1">Books</a>

<!-- Pagination -->
<a dj-patch="?page={{ page|add:1 }}">Next</a>

<!-- With path change -->
<a dj-patch="/products/?sort=price">Sort by Price</a>
```

### `dj-navigate`

Navigate to a different view. Equivalent to calling `live_redirect()`.

```html
<!-- Navigate to detail view -->
<a dj-navigate="/products/{{ product.id }}/">View Details</a>

<!-- Back to list -->
<a dj-navigate="/products/">Back to List</a>
```

## URL Routing with `live_session()`

Group views into a "live session" — they share a WebSocket connection and can navigate between each other without reconnecting.

```python
# urls.py
from django.urls import path
from djust.routing import live_session
from .views import DashboardView, SettingsView, ProfileView

urlpatterns = [
    # Standard Django URLs
    path("login/", LoginView.as_view()),
    
    # Live session: views share WebSocket connection
    *live_session("/app", [
        path("", DashboardView.as_view(), name="dashboard"),
        path("settings/", SettingsView.as_view(), name="settings"),
        path("profile/", ProfileView.as_view(), name="profile"),
        path("users/<int:user_id>/", UserDetailView.as_view(), name="user_detail"),
    ]),
]
```

### Client-Side Route Map

For `live_redirect` to work, the client needs to know which views handle which URLs. Include the route map in your template:

```html
<!-- base.html -->
{% load djust_tags %}
<!DOCTYPE html>
<html>
<head>
    {% djust_route_map %}  <!-- Emits route map for live_redirect -->
</head>
<body>
    {% block content %}{% endblock %}
    {% djust_scripts %}
</body>
</html>
```

Or manually call `get_route_map_script()`:

```python
from djust.routing import get_route_map_script

def my_view(request):
    return render(request, "my_template.html", {
        "route_map_script": get_route_map_script(),
    })
```

## Browser Back/Forward Support

djust automatically handles `popstate` events. When the user navigates with browser buttons:

1. For `live_patch` URLs (same view, different params): `handle_params()` is called
2. For `live_redirect` URLs (different view): the new view is mounted

```python
class SearchView(LiveView):
    def handle_params(self, params, uri):
        """Handles browser back/forward for URL param changes"""
        self.query = params.get("q", "")
        self.page = int(params.get("page", 1))
        self.search()
        # View re-renders automatically
```

## Complete Example

```python
# views.py
from djust import LiveView
from djust.decorators import event_handler
from .models import Product

class ProductCatalogView(LiveView):
    template_name = "catalog.html"

    def mount(self, request, **kwargs):
        self.category = request.GET.get("category", "all")
        self.sort = request.GET.get("sort", "name")
        self.page = int(request.GET.get("page", 1))
        self.per_page = 20
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
        
        start = (self.page - 1) * self.per_page
        self.products = list(qs[start:start + self.per_page])
        self.has_more = qs.count() > start + self.per_page

    @event_handler
    def filter(self, category, **kwargs):
        self.category = category
        self.page = 1
        self.load_products()
        self.live_patch(params={
            "category": category,
            "sort": self.sort,
            "page": 1
        })

    @event_handler
    def sort_by(self, field, **kwargs):
        self.sort = field
        self.load_products()
        self.live_patch(params={
            "category": self.category,
            "sort": field,
            "page": self.page
        })

    @event_handler
    def view_product(self, product_id, **kwargs):
        self.live_redirect(f"/products/{product_id}/")
```

```html
<!-- catalog.html -->
<div id="catalog">
    <!-- Category filters -->
    <nav class="filters">
        <a dj-patch="?category=all" 
           class="{% if category == 'all' %}active{% endif %}">All</a>
        <a dj-patch="?category=electronics"
           class="{% if category == 'electronics' %}active{% endif %}">Electronics</a>
        <a dj-patch="?category=books"
           class="{% if category == 'books' %}active{% endif %}">Books</a>
    </nav>

    <!-- Sort options -->
    <select dj-change="sort_by" name="field">
        <option value="name" {% if sort == 'name' %}selected{% endif %}>Name</option>
        <option value="price" {% if sort == 'price' %}selected{% endif %}>Price</option>
        <option value="-created" {% if sort == '-created' %}selected{% endif %}>Newest</option>
    </select>

    <!-- Product grid -->
    <div class="products">
        {% for product in products %}
            <div class="product" dj-click="view_product" dj-value-product_id="{{ product.id }}">
                <h3>{{ product.name }}</h3>
                <p>${{ product.price }}</p>
            </div>
        {% endfor %}
    </div>

    <!-- Pagination -->
    {% if page > 1 %}
        <a dj-patch="?page={{ page|add:'-1' }}&category={{ category }}&sort={{ sort }}">
            Previous
        </a>
    {% endif %}
    {% if has_more %}
        <a dj-patch="?page={{ page|add:'1' }}&category={{ category }}&sort={{ sort }}">
            Next
        </a>
    {% endif %}
</div>
```

## API Reference

### NavigationMixin Methods

| Method | Description |
|--------|-------------|
| `live_patch(params, path, replace)` | Update URL without remount |
| `live_redirect(path, params, replace)` | Navigate to different view |
| `handle_params(params, uri)` | Override to handle URL changes |

### Template Directives

| Directive | Description |
|-----------|-------------|
| `dj-patch="url"` | Update URL on click (calls `live_patch`) |
| `dj-navigate="url"` | Navigate to view (calls `live_redirect`) |

### Routing Functions

| Function | Description |
|----------|-------------|
| `live_session(prefix, patterns)` | Group views sharing WebSocket |
| `get_route_map_script()` | Get `<script>` tag for route map |
