# djust Best Practices - AI Quick Reference

**Purpose**: Concise, code-first reference for AI assistants to quickly apply djust patterns correctly.

**Last Updated**: 2025-11-19

---

## 1. LiveView Lifecycle Pattern ✅

### Complete Pattern (Use This Template)

```python
from djust import LiveView
from djust.decorators import event_handler, debounce

class ProductListView(LiveView):
    """
    Standard LiveView lifecycle pattern.

    Pattern:
    1. mount() → initialize state (public variables)
    2. _refresh() → build QuerySet (private variable)
    3. get_context_data() → JIT serialization
    4. Event handlers → update state + refresh
    """
    template_name = 'products/list.html'

    def mount(self, request, **kwargs):
        """
        Phase 1: Initialize state

        Rules:
        - Set public variables only (exposed to template)
        - Initialize filters, search queries, sort options
        - Call _refresh() to load initial data
        - DON'T assign QuerySets directly to public variables
        """
        # Public: exposed to template
        self.search_query = ""
        self.filter_category = "all"
        self.sort_by = "name"

        # Load initial data
        self._refresh_products()

    def _refresh_products(self):
        """
        Phase 2: Build QuerySet (CRITICAL: private variable)

        Rules:
        - Build QuerySet with filters/sorts
        - Keep as QuerySet (don't convert to list)
        - Store in PRIVATE variable (self._products)
        - This enables Rust JIT serialization
        """
        # Start with base QuerySet
        products = Product.objects.all()

        # Apply filters
        if self.search_query:
            products = products.filter(
                Q(name__icontains=self.search_query) |
                Q(description__icontains=self.search_query)
            )

        if self.filter_category != "all":
            products = products.filter(category=self.filter_category)

        # Apply sorting (database-level)
        products = products.order_by(self.sort_by)

        # CRITICAL: Store in PRIVATE variable
        self._products = products

    @event_handler()
    @debounce(wait=0.5)
    def search(self, value: str = "", **kwargs):
        """
        Event handler for search input

        Rules:
        - Use @event_handler() decorator (REQUIRED)
        - Use 'value' parameter for @input/@change events
        - Provide default value
        - Accept **kwargs for flexibility
        - Update state → call refresh
        """
        self.search_query = value
        self._refresh_products()

    @event_handler()
    def filter_by_category(self, value: str = "all", **kwargs):
        """Event handler for category filter"""
        self.filter_category = value
        self._refresh_products()

    @event_handler()
    def sort_by_field(self, value: str = "name", **kwargs):
        """Event handler for sorting"""
        self.sort_by = value
        self._refresh_products()

    def get_context_data(self, **kwargs):
        """
        Phase 3: Expose data for rendering

        Rules:
        - Assign private QuerySet to public variable
        - Call super() to trigger Rust JIT serialization
        - Add non-model context only
        - DON'T manually serialize QuerySets
        """
        # JIT serialization: private → public
        self.products = self._products

        # CRITICAL: Call super() to trigger Rust serialization
        context = super().get_context_data(**kwargs)

        # Add non-model context
        context.update({
            'search_query': self.search_query,
            'filter_category': self.filter_category,
            'sort_by': self.sort_by,
            'categories': Category.objects.all(),  # OK: small queryset
        })

        return context
```

### Template Pattern

```html
<!-- products/list.html -->
<div>
    <!-- Search input with debouncing -->
    <input type="text"
           dj-input="search"
           value="{{ search_query }}"
           placeholder="Search products..." />

    <!-- Category filter -->
    <select dj-change="filter_by_category">
        <option value="all" {% if filter_category == 'all' %}selected{% endif %}>
            All Categories
        </option>
        {% for category in categories %}
        <option value="{{ category.id }}"
                {% if filter_category == category.id|stringformat:'s' %}selected{% endif %}>
            {{ category.name }}
        </option>
        {% endfor %}
    </select>

    <!-- Product list -->
    <div class="products">
        {% for product in products %}
        <div class="product-card">
            <h3>{{ product.name }}</h3>
            <p>{{ product.description }}</p>
            <p>${{ product.price }}</p>
        </div>
        {% endfor %}

        <!-- Auto-generated count -->
        <p>Showing {{ products_count }} products</p>
    </div>
</div>
```

---

## 2. JIT Serialization Pattern (CRITICAL) ⚡

### The Pattern (10-100x Performance Boost)

```python
# ✅ CORRECT: Private → Public in get_context_data
def _refresh_items(self):
    items = MyModel.objects.filter(...)
    self._items = items  # PRIVATE variable

def get_context_data(self, **kwargs):
    self.items = self._items  # PUBLIC ← PRIVATE
    context = super().get_context_data(**kwargs)  # Triggers Rust JIT
    return context

# ❌ WRONG: Direct assignment to public
def mount(self, request):
    self.items = MyModel.objects.all()  # NO! Bypasses JIT

# ❌ WRONG: Converting to list
def _refresh_items(self):
    self._items = list(MyModel.objects.all())  # NO! Must be QuerySet

# ❌ WRONG: Manual serialization
def get_context_data(self, **kwargs):
    context = super().get_context_data(**kwargs)
    context['items'] = [{'id': i.id, 'name': i.name} for i in self._items]  # NO!
    return context
```

### Why It Matters

```python
# Without JIT (Python serialization):
# - 50-200ms for 100 items
# - N+1 queries if not careful
# - Manual field selection

# With JIT (Rust serialization):
# - <1ms for 100 items
# - Automatic select_related detection
# - Optimal field selection
```

### Real Example from djust_rentals

```python
class PropertyListView(BaseViewWithNavbar):
    """From examples/demo_project/djust_rentals/views/properties.py"""

    def _refresh_properties(self):
        properties = Property.objects.all()

        if self.search_query:
            properties = properties.filter(
                Q(address__icontains=self.search_query) |
                Q(description__icontains=self.search_query)
            )

        if self.filter_status != "all":
            properties = properties.filter(status=self.filter_status)

        # CRITICAL: Store in private variable
        self._properties = properties

    def get_context_data(self, **kwargs):
        # JIT serialization happens here
        self.properties = self._properties

        context = super().get_context_data(**kwargs)
        # Note: Don't add 'properties' to context - parent did it!
        # Auto-generated: properties_count
        return context
```

---

## 3. Event Handlers 🎯

### Parameter Naming (CRITICAL)

```python
# ✅ CORRECT: Use 'value' for form inputs
@event_handler()
def search(self, value: str = "", **kwargs):
    """Receives value from @input/@change events"""
    self.search_query = value

# ❌ WRONG: Different parameter name
@event_handler()
def search(self, query: str = "", **kwargs):
    """Won't receive the input value!"""
    self.search_query = query  # Always "" (default)

# ✅ CORRECT: Form submission
@event_handler()
def submit_form(self, **form_data):
    """Receives all form fields as dict"""
    email = form_data.get('email')
    name = form_data.get('name')

# ✅ CORRECT: Button with data attributes
@event_handler()
def delete_item(self, item_id: int, **kwargs):
    """Type validation enforced"""
    Item.objects.filter(id=item_id).delete()
```

### Template Event Binding

```html
<!-- Text input -->
<input type="text" dj-input="search" value="{{ search_query }}" />

<!-- Select/dropdown -->
<select dj-change="filter_category">
    <option value="all">All</option>
</select>

<!-- Button with data -->
<button dj-click="delete_item" data-item-id="{{ item.id }}">
    Delete
</button>

<!-- Form submission -->
<form dj-submit="save_form">
    <input name="email" type="email" />
    <input name="name" type="text" />
    <button type="submit">Save</button>
</form>
```

### Decorator Usage

```python
# Debouncing (wait for user to stop typing)
@event_handler()
@debounce(wait=0.5)  # 500ms delay
def search(self, value: str = "", **kwargs):
    self.search_query = value
    self._refresh_results()

# Throttling (limit rate of calls)
@event_handler()
@throttle(wait=1.0)  # Max once per second
def track_scroll(self, position: int = 0, **kwargs):
    self.scroll_position = position

# Optimistic updates (UI updates before server confirms)
@event_handler()
@optimistic
def like_post(self, post_id: int, **kwargs):
    post = Post.objects.get(id=post_id)
    post.likes += 1
    post.save()

# Client-side caching
@event_handler()
@cache(ttl=300, key_params=["query"])  # 5 min cache
def autocomplete(self, query: str = "", **kwargs):
    return City.objects.filter(name__istartswith=query)[:10]

# Client state sync (multi-component coordination)
@event_handler()
@client_state(keys=["active_tab"])
def switch_tab(self, tab: str = "overview", **kwargs):
    self.active_tab = tab
```

---

## 4. Common Mistakes ⚠️

### Mistake 1: Converting QuerySets to Lists

```python
# ❌ WRONG
def _refresh_items(self):
    self._items = list(Item.objects.all())  # Disables JIT!

# ✅ CORRECT
def _refresh_items(self):
    self._items = Item.objects.all()  # Keep as QuerySet
```

### Mistake 2: Direct Public Assignment

```python
# ❌ WRONG
def mount(self, request):
    self.items = Item.objects.all()  # Bypasses JIT

# ✅ CORRECT
def mount(self, request):
    self._refresh_items()

def _refresh_items(self):
    self._items = Item.objects.all()

def get_context_data(self, **kwargs):
    self.items = self._items  # JIT happens here
    return super().get_context_data(**kwargs)
```

### Mistake 3: Wrong Parameter Names

```python
# ❌ WRONG
@event_handler()
def search(self, query: str = "", **kwargs):
    self.search_query = query  # Won't work!

# ✅ CORRECT
@event_handler()
def search(self, value: str = "", **kwargs):
    self.search_query = value  # Works!
```

### Mistake 4: Missing @event_handler Decorator

```python
# ❌ WRONG
def search(self, value: str = "", **kwargs):
    """Won't be discovered by debug panel"""
    self.search_query = value

# ✅ CORRECT
@event_handler()
def search(self, value: str = "", **kwargs):
    """Auto-discovered and validated"""
    self.search_query = value
```

### Mistake 5: Manual Serialization

```python
# ❌ WRONG
def get_context_data(self, **kwargs):
    context = super().get_context_data(**kwargs)
    context['items'] = [
        {'id': i.id, 'name': i.name}
        for i in self._items
    ]
    return context

# ✅ CORRECT
def get_context_data(self, **kwargs):
    self.items = self._items  # Let Rust do it
    return super().get_context_data(**kwargs)
```

### Mistake 6: Forgetting Default Values

```python
# ❌ WRONG
@event_handler()
def filter_status(self, value: str, **kwargs):
    """No default - will error if value missing"""
    self.status = value

# ✅ CORRECT
@event_handler()
def filter_status(self, value: str = "all", **kwargs):
    """Safe with default"""
    self.status = value
```

---

## 5. Form Integration with FormMixin 📝

### Complete Form Pattern

```python
from djust import LiveView
from djust.forms import FormMixin
from django.urls import reverse

class LeaseFormView(FormMixin, LiveView):
    """
    Complete form handling pattern.

    FormMixin provides:
    - Automatic form rendering
    - Real-time validation
    - Error display
    - Success handling
    """
    template_name = 'leases/form.html'
    form_class = LeaseForm

    def mount(self, request, pk=None, **kwargs):
        """
        CRITICAL: Set _model_instance BEFORE super().mount()

        For edits:
        1. Load model instance first
        2. Set self._model_instance
        3. Call super().mount()
        4. FormMixin uses _model_instance for form init
        """
        # For edits, load model BEFORE super()
        if pk:
            self._model_instance = Lease.objects.get(pk=pk)

        # CRITICAL: Call super() AFTER setting _model_instance
        super().mount(request, **kwargs)

    def form_valid(self, form):
        """
        Called when form validates successfully

        Rules:
        - Save the form
        - Set success message
        - Set redirect URL (optional)
        - Return None (FormMixin handles response)
        """
        lease = form.save()
        self.success_message = f"Lease saved successfully!"
        self.redirect_url = reverse('lease-detail', kwargs={'pk': lease.pk})
        # Don't return anything - FormMixin handles it

    def form_invalid(self, form):
        """
        Called when form has validation errors

        Rules:
        - FormMixin automatically displays errors
        - Optionally set error message
        - Optionally store form for re-display
        """
        self.error_message = "Please fix the errors below"
        self.form_instance = form  # Optional: store form
```

### Template Pattern for Forms

```html
<!-- leases/form.html -->
<div>
    {% if success_message %}
    <div class="alert alert-success">{{ success_message }}</div>
    {% endif %}

    {% if error_message %}
    <div class="alert alert-error">{{ error_message }}</div>
    {% endif %}

    <form dj-submit="submit_form">
        {% csrf_token %}

        <!-- FormMixin auto-renders with framework styling -->
        {{ form.as_p }}

        <!-- Or manually render fields with real-time validation -->
        <div>
            <label>Tenant</label>
            <select name="tenant" dj-change="validate_field">
                <option value="">Select tenant...</option>
                {% for tenant in tenants %}
                <option value="{{ tenant.id }}">{{ tenant.name }}</option>
                {% endfor %}
            </select>
            {% if form.tenant.errors %}
            <div class="error">{{ form.tenant.errors }}</div>
            {% endif %}
        </div>

        <button type="submit">Save</button>
    </form>
</div>
```

---

## 6. Security Best Practices 🔒

### Authorization Pattern

```python
from django.core.exceptions import PermissionDenied

class SecureLeaseView(LiveView):
    def mount(self, request, pk=None, **kwargs):
        """Always check authorization in mount()"""

        # 1. Authentication check
        if not request.user.is_authenticated:
            raise PermissionDenied("Must be logged in")

        # 2. Object-level permission for edits
        if pk:
            lease = Lease.objects.get(pk=pk)
            if lease.property.owner != request.user:
                raise PermissionDenied("Not your property")
            self._lease = lease

        super().mount(request, **kwargs)

    @event_handler()
    def delete_lease(self, lease_id: int, **kwargs):
        """Re-verify permissions in event handlers"""
        lease = Lease.objects.get(id=lease_id)

        # Check ownership
        if lease.property.owner != self.request.user:
            raise PermissionDenied("Not authorized")

        lease.delete()
```

### Input Validation Pattern

```python
from decimal import Decimal, InvalidOperation

@event_handler()
def update_rent(self, value: str = "", **kwargs):
    """Validate and sanitize all inputs"""

    # 1. Type validation (automatic with type hints)
    try:
        rent = Decimal(value)
    except (ValueError, InvalidOperation):
        self.error = "Invalid amount"
        return

    # 2. Business logic validation
    if rent <= 0:
        self.error = "Rent must be positive"
        return

    if rent > 100000:
        self.error = "Rent exceeds maximum"
        return

    # 3. Safe to use
    self.monthly_rent = rent
```

### CSRF Protection

```html
<!-- CSRF token required for all forms -->
<form dj-submit="submit_form">
    {% csrf_token %}
    <!-- form fields -->
</form>
```

### WebSocket auth

Auth runs at mount. An unauthorized or anonymous mount of a `login_required` /
`PermissionRequiredMixin` view is rejected and the socket is closed — events
never reach a view that failed auth.

- **Test WS auth with a raw client** (`WebsocketCommunicator` / the `websockets`
  library), not just a browser.
- For mid-session logout / permission loss, enable per-event re-check:
  `LIVEVIEW_CONFIG['reauth_on_event'] = True` (default OFF — costs one session
  read per event).
- Set `LIVEVIEW_ALLOWED_MODULES` to restrict which view classes a client may
  mount over the WebSocket.
- Gate **non-LiveView endpoints** (plain `django.views.View`, OAuth callbacks)
  with Django's own `LoginRequiredMixin` — `login_required` is LiveView-only.
- Don't let the login page extend a base whose `dj-view` root mounts a
  `login_required` view (redirect loop) — give it a standalone template.

You can use `{{ user }}`, `{% csrf_token %}`, and context-processor variables
directly inside `dj-view` templates; they render on WebSocket updates.

---

## 7. Performance Optimization 🚀

### QuerySet Optimization

```python
# ✅ GOOD: Let JIT detect relationships
def _refresh_items(self):
    # Template uses: {{ item.author.name }}
    # JIT automatically adds: select_related('author')
    self._items = Item.objects.all()

# ✅ BETTER: Manual optimization for complex cases
def _refresh_items(self):
    self._items = Item.objects.select_related(
        'author', 'category'
    ).prefetch_related(
        'tags'
    )

# ✅ BEST: Database-level aggregation
from django.db.models import Count, Sum

def _refresh_items(self):
    self._items = Item.objects.annotate(
        comment_count=Count('comments'),
        total_votes=Sum('votes')
    )
```

### Caching Pattern

```python
# Cache expensive queries
@event_handler()
@cache(ttl=300, key_params=["query"])  # 5 min cache
def autocomplete(self, query: str = "", **kwargs):
    """Client caches results for repeated queries"""
    return City.objects.filter(
        name__istartswith=query
    )[:10]

# Cache with StateBus for multi-component sync
@event_handler()
@client_state(keys=["filter"])
@cache(ttl=60)
def apply_filter(self, filter: str = "all", **kwargs):
    """Publishes to StateBus + caches response"""
    self.filter = filter
    self._refresh_results()
```

### List Reordering — Use `data-key`

```html
<!-- ❌ BAD: Unkeyed list — removing/reordering causes O(n) patches -->
{% for task in tasks %}
  <li>{{ task.name }}</li>
{% endfor %}

<!-- ✅ GOOD: Keyed list — only changed items generate patches -->
{% for task in tasks %}
  <li data-key="{{ task.id }}">{{ task.name }}</li>
{% endfor %}
```

Add `data-key` to list items when items can be sorted, filtered, removed from the middle, or reordered. Without keys, the positional diff rewrites every shifted item's text. With keys, only actual changes are sent.

**Skip keys** for append-only lists, small static lists (<10 items), or lists that always replace entirely.

See [List Reordering Performance Guide](guides/LIST_REORDERING_PERFORMANCE.md) for benchmark data and detailed examples.

### Payload Optimization

```python
# ✅ GOOD: Return only needed fields
def get_context_data(self, **kwargs):
    # JIT only serializes fields used in template
    self.items = self._items
    return super().get_context_data(**kwargs)

# ✅ BETTER: Limit queryset size
def _refresh_items(self):
    # Pagination
    self._items = Item.objects.all()[:50]

    # Or use Paginator
    from django.core.paginator import Paginator
    paginator = Paginator(Item.objects.all(), 50)
    self._items = paginator.page(self.page_number)
```

---

## 8. Testing Patterns 🧪

### Unit Testing LiveViews

```python
from django.test import TestCase, RequestFactory
from myapp.views import ProductListView

class ProductListViewTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user('test')

    def test_mount_initializes_state(self):
        """Test mount() sets up initial state"""
        request = self.factory.get('/')
        request.user = self.user

        view = ProductListView()
        view.mount(request)

        self.assertEqual(view.search_query, "")
        self.assertEqual(view.filter_category, "all")

    def test_search_filters_products(self):
        """Test search event handler"""
        Product.objects.create(name="Django Book")
        Product.objects.create(name="Rust Book")

        request = self.factory.get('/')
        request.user = self.user

        view = ProductListView()
        view.mount(request)
        view.search(value="Django")

        self.assertEqual(view.search_query, "Django")
        self.assertEqual(view._products.count(), 1)

    def test_jit_serialization(self):
        """Test JIT pattern"""
        request = self.factory.get('/')
        request.user = self.user

        view = ProductListView()
        view.mount(request)

        # Private variable set
        self.assertTrue(hasattr(view, '_products'))

        # Public variable not set until get_context_data
        self.assertFalse(hasattr(view, 'products'))

        context = view.get_context_data()

        # Now public variable set
        self.assertTrue(hasattr(view, 'products'))
        self.assertEqual(view.products, view._products)
```

### Testing Event Handlers

```python
def test_event_handler_validation(self):
    """Test parameter validation"""
    view = ProductListView()
    view.mount(self.factory.get('/'), user=self.user)

    # Valid integer
    view.delete_item(item_id=123)

    # Invalid type (should raise validation error)
    with self.assertRaises(ValidationError):
        view.delete_item(item_id="invalid")
```

---

## 9. Quick Decision Trees 🌳

### When to Use LiveView?

```
Does your page need real-time updates? ─┐
                                        │
                ┌──────── YES ──────────┤
                │                       │
                ▼                       │
    Is it primarily form submission? ──┤
                                        │
        ┌──── NO ─────┐                 │
        │             │                 │
        ▼             ▼                 │
    LiveView      FormView +        ┌── NO
                  LiveView          │
                  (hybrid)          ▼
                              Traditional
                              Django view
```

**Use LiveView when:**
- Real-time updates (search, filters, live data)
- Interactive UI (accordions, tabs, modals)
- Partial page updates (no full reload)
- Multi-step flows

**Don't use LiveView for:**
- Static content pages
- SEO-critical pages (use SSR)
- Simple CRUD without interactivity

### Component vs LiveComponent?

```
Does it need state? ─┐
                     │
    ┌─── NO ────────┤
    │               │
    ▼               ▼
Component      LiveComponent
(simple)       (stateful)

Examples:
- StatusBadge    - TodoList
- CodeBlock      - Tabs
- Button         - Modal
- Icon           - DataTable
```

### Which State Decorator?

```
What's the interaction pattern?

Search input?          → @debounce(wait=0.5)
Scroll tracking?       → @throttle(wait=1.0)
Like button?           → @optimistic
Autocomplete?          → @cache(ttl=300)
Multi-component sync?  → @client_state(keys=[...])
Draft saving?          → DraftModeMixin
```

---

## 10. Quick Reference Checklists ✓

### LiveView Implementation Checklist

- [ ] Use `@event_handler()` decorator on all handlers
- [ ] Use `value` parameter for @input/@change events
- [ ] Provide default values for all parameters
- [ ] Accept `**kwargs` for flexibility
- [ ] Build QuerySets in private methods (`_refresh_*`)
- [ ] Store QuerySets in private variables (`self._items`)
- [ ] Assign to public in `get_context_data()` only
- [ ] Call `super().get_context_data()` to trigger JIT
- [ ] Never convert QuerySets to lists
- [ ] Add CSRF token to all forms

### Performance Checklist

- [ ] Using JIT serialization pattern
- [ ] QuerySets stay as QuerySets (not lists)
- [ ] Database-level filtering/sorting
- [ ] Using @debounce for search inputs
- [ ] Using @cache for repeated queries
- [ ] Limiting queryset size (pagination)
- [ ] Using select_related/prefetch_related when needed

### Security Checklist

- [ ] Authentication check in `mount()`
- [ ] Authorization check for object access
- [ ] Re-verify permissions in event handlers
- [ ] Validate all input types
- [ ] Sanitize user input
- [ ] Use Django form validation
- [ ] CSRF token in all forms
- [ ] Never expose sensitive data to template
- [ ] No `|safe` filter on user-controlled template variables
- [ ] Fuzz tests pass (`LiveViewSmokeTest` mixin)

---

## 11. Real-World Example (Complete)

**From**: `examples/demo_project/djust_rentals/views/properties.py`

```python
from djust import LiveView
from djust.decorators import event_handler, debounce
from djust_rentals.models import Property
from django.db.models import Q

class PropertyListView(LiveView):
    """
    Complete working example from demo project.

    Features:
    - Search with debouncing
    - Status filtering
    - Sorting
    - JIT serialization
    - Proper lifecycle
    """
    template_name = 'rentals/property_list.html'

    def mount(self, request, **kwargs):
        # Initialize state
        self.search_query = ""
        self.filter_status = "all"
        self.sort_by = "address"

        # Load data
        self._refresh_properties()

    def _refresh_properties(self):
        # Build QuerySet
        properties = Property.objects.all()

        # Search
        if self.search_query:
            properties = properties.filter(
                Q(address__icontains=self.search_query) |
                Q(description__icontains=self.search_query)
            )

        # Filter
        if self.filter_status != "all":
            properties = properties.filter(status=self.filter_status)

        # Sort
        properties = properties.order_by(self.sort_by)

        # Store in private
        self._properties = properties

    @event_handler()
    @debounce(wait=0.5)
    def search(self, value: str = "", **kwargs):
        self.search_query = value
        self._refresh_properties()

    @event_handler()
    def filter_status(self, value: str = "all", **kwargs):
        self.filter_status = value
        self._refresh_properties()

    @event_handler()
    def sort_by_field(self, value: str = "address", **kwargs):
        self.sort_by = value
        self._refresh_properties()

    def get_context_data(self, **kwargs):
        # JIT serialization
        self.properties = self._properties

        context = super().get_context_data(**kwargs)

        # Non-model context
        context.update({
            'search_query': self.search_query,
            'filter_status': self.filter_status,
            'sort_by': self.sort_by,
        })

        return context
```

---

## 12. Deployment 🚀

The `djust deploy` platform **injects config via environment variables** and serves from a **read-only rootfs**. Settings that hardcode platform-provided values, or that write to local files, deploy "successfully" then 500 at runtime.

### Settings Pattern (CRITICAL for platform deploys)

```python
# ❌ WRONG — works in dev, breaks in prod
SECRET_KEY = "django-insecure-dev-key"               # dev key on a public host (security risk)
ALLOWED_HOSTS = ["localhost"]                          # → DisallowedHost (400) on the platform
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3",
                         "NAME": BASE_DIR / "db.sqlite3"}}  # read-only rootfs → 500 on first write

# ✅ CORRECT — read everything the platform injects from the env
import os, dj_database_url
SECRET_KEY = os.environ["SECRET_KEY"]
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "").split(",")
DATABASES = {"default": dj_database_url.config(default=os.environ["DATABASE_URL"])}
# (Individual os.environ["DB_NAME"], os.environ["DB_HOST"], … is equally fine.)
```

### Deploy Doctor

`djust deploy` runs a static preflight over your settings *source text* (never imports it) and **warns** — without blocking — on: hardcoded `SECRET_KEY`, literal `ALLOWED_HOSTS`, a `DATABASES` block that reads no env config, or a sqlite engine. **Treat every doctor warning as deploy-blocking** — they predict runtime 500s.

### Rollout Status

`djust deploy` prints `Status: rolling out …` (not `active`) while a blue/green rollout still serves the OLD version, and only shows the URL once the new version is live. Wait for the URL before re-testing — "rolling out" means you'd be testing old code.

**Deployment checklist:**
- [ ] `SECRET_KEY`, `ALLOWED_HOSTS`, `DATABASES` all read from `os.environ` (no hardcoded values, no sqlite on the rootfs)
- [ ] Zero `djust deploy` deploy-doctor warnings
- [ ] Re-test only after `djust deploy` reports the live URL (not "rolling out")

---

## Summary for AI Assistants

**When writing djust code, always:**

1. **Use the lifecycle pattern**: mount → _refresh → get_context_data
2. **Use JIT serialization**: Private variable → public in get_context_data
3. **Use @event_handler** decorator on all handlers
4. **Use 'value' parameter** for form inputs
5. **Keep QuerySets as QuerySets** (never convert to list)
6. **Call super().get_context_data()** to trigger Rust serialization
7. **Check authorization** in mount() and event handlers
8. **Use type hints** for automatic validation
9. **Trust auto-escaping** — `{{ var }}` is HTML-escaped by default; use `|safe` only for server-generated HTML
10. **Run fuzz tests** — `LiveViewSmokeTest` catches XSS and crash regressions automatically

**Common pitfalls to avoid:**

1. ❌ Converting QuerySets to lists
2. ❌ Assigning QuerySets to public variables outside get_context_data
3. ❌ Using wrong parameter names (query vs value)
4. ❌ Forgetting @event_handler decorator
5. ❌ Missing default parameter values
6. ❌ Skipping authorization checks
7. ❌ Using `|safe` filter on user input (bypasses auto-escaping)
8. ❌ Hardcoding `SECRET_KEY`/`ALLOWED_HOSTS`/`DATABASES` (or sqlite) for platform deploys instead of reading from `os.environ` — deploys "succeed" then 500 at runtime

**Quick pattern matching:**
- Search/filter → @debounce + private QuerySet + JIT
- Forms → FormMixin + validation
- Real-time → @optimistic + client state
- Autocomplete → @cache + @debounce
