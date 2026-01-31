# JIT Serialization Pattern for djust LiveViews

## Overview

The **JIT (Just-In-Time) Serialization Pattern** is a best practice for djust LiveViews that optimizes QuerySet serialization by leveraging Rust-based automatic serialization. This pattern ensures optimal performance when passing Django QuerySets to templates.

## The Problem

When a LiveView renders a template, it needs to serialize Django model instances to pass to the template engine. Without proper organization, this can lead to:

1. **Premature serialization** - QuerySets serialized before filtering/sorting is complete
2. **Double serialization** - Data serialized both manually in Python and automatically by JIT
3. **N+1 queries** - Missing select_related/prefetch_related optimizations
4. **Performance issues** - Slow Python-based serialization instead of fast Rust serialization

## The Solution: Private/Public Variable Pattern

The JIT pattern uses a two-phase approach:

### Phase 1: Build QuerySet (Private Variable)
Store the QuerySet in a **private instance variable** (prefixed with `_`) while building/filtering it. This prevents premature JIT serialization.

### Phase 2: Expose for Serialization (Public Variable)
In `get_context_data()`, assign the private variable to a **public instance variable** just before calling `super().get_context_data()`. This triggers optimized Rust-based JIT serialization.

## Implementation Pattern

```python
from djust_shared.views import BaseViewWithNavbar

class MyListView(BaseViewWithNavbar):
    template_name = 'my_template.html'

    def mount(self, request, **kwargs):
        """Initialize view state"""
        # Initialize filter/search state
        self.search_query = ""
        self.filter_status = "all"

        # Load initial data
        self._refresh_items()

    def _refresh_items(self):
        """Refresh item list based on current filters"""
        # Start with base QuerySet
        items = MyModel.objects.all()

        # Apply search
        if self.search_query:
            items = items.filter(name__icontains=self.search_query)

        # Apply filters
        if self.filter_status != "all":
            items = items.filter(status=self.filter_status)

        # Apply sorting
        items = items.order_by('-created_at')

        # IMPORTANT: Store in PRIVATE variable to prevent premature JIT serialization
        self._items = items

    def get_context_data(self, **kwargs):
        """Add context for template rendering"""
        # STEP 1: Assign private QuerySet to public variable for JIT serialization
        # JIT will automatically:
        # - Extract ALL field paths used in template
        # - Apply optimal select_related/prefetch_related
        # - Serialize using fast Rust code (10-100x faster than Python)
        # - Auto-generate count variables (e.g., items_count)
        self.items = self._items

        # STEP 2: Call parent - triggers JIT serialization
        context = super().get_context_data(**kwargs)

        # STEP 3: Add non-model context (won't be JIT serialized)
        context.update({
            # NOTE: Don't add 'items' here - parent already serialized it!
            'search_query': self.search_query,
            'filter_status': self.filter_status,
            # Parent auto-generates 'items_count' from len(items)
        })

        return context
```

## Real-World Examples

### Example 1: Property List View

```python
# File: djust_rentals/views/properties.py

class PropertyListView(BaseViewWithNavbar):
    template_name = 'rentals/property_list.html'

    def mount(self, request, **kwargs):
        self.search_query = ""
        self.filter_status = "all"
        self.filter_type = "all"
        self.sort_by = "name"
        self._refresh_properties()

    def _refresh_properties(self):
        """Build QuerySet with filters"""
        properties = Property.objects.all()

        if self.search_query:
            properties = properties.filter(
                Q(name__icontains=self.search_query) |
                Q(address__icontains=self.search_query)
            )

        if self.filter_status != "all":
            properties = properties.filter(status=self.filter_status)

        if self.sort_by == "name":
            properties = properties.order_by('name')
        elif self.sort_by == "rent_high":
            properties = properties.order_by('-monthly_rent')

        # Store in private variable
        self._properties = properties

    @debounce(wait=0.5)
    def search(self, query: str = "", **kwargs):
        """Event handler for search"""
        self.search_query = query
        self._refresh_properties()

    def get_context_data(self, **kwargs):
        """Expose for JIT serialization"""
        # Assign to public variable
        self.properties = self._properties

        # Parent handles JIT serialization
        context = super().get_context_data(**kwargs)

        # Add non-model context
        context.update({
            'search_query': self.search_query,
            'filter_status': self.filter_status,
        })

        return context
```

### Example 2: Maintenance List View with Complex Sorting

```python
# File: djust_rentals/views/maintenance.py

class MaintenanceListView(BaseViewWithNavbar):
    template_name = 'rentals/maintenance_list.html'

    def _refresh_requests(self):
        """Build QuerySet with complex sorting"""
        requests = MaintenanceRequest.objects.select_related(
            'property', 'tenant__user'
        ).all()

        # Apply filters
        if self.filter_status == "pending":
            requests = requests.filter(status__in=['open', 'in_progress'])

        # Complex priority sorting using Case/When
        if self.sort_by == "priority":
            from django.db.models import Case, When, IntegerField
            requests = requests.annotate(
                priority_order=Case(
                    When(priority='urgent', then=0),
                    When(priority='high', then=1),
                    When(priority='medium', then=2),
                    When(priority='low', then=3),
                    default=4,
                    output_field=IntegerField(),
                )
            ).order_by('priority_order', '-created_at')

        # IMPORTANT: Keep as QuerySet, don't convert to list!
        # Store in private variable
        self._requests = requests

    def get_context_data(self, **kwargs):
        # Assign to public variable for JIT serialization
        self.requests = self._requests

        # Parent serializes with Rust
        context = super().get_context_data(**kwargs)

        # Count statistics (using Django aggregation)
        all_requests = MaintenanceRequest.objects.all()
        status_counts = {
            'open': all_requests.filter(status='open').count(),
            'completed': all_requests.filter(status='completed').count(),
        }

        context.update({
            'status_counts': status_counts,
            'filter_status': self.filter_status,
        })

        return context
```

## Custom Annotations with `_djust_annotations`

Models can declare computed annotations that JIT automatically applies during query optimization:

```python
from django.db.models import Count

class Author(models.Model):
    name = models.CharField(max_length=200)

    # Declare annotations JIT should apply when serializing this model
    _djust_annotations = {
        "book_count": Count("books"),
    }
```

```html
<!-- Template can reference the annotation directly -->
{{ author.book_count }} books published
```

JIT detects `_djust_annotations` on the model class and adds them to the query automatically. The annotation values are then available as regular attributes during serialization.

## How JIT Serialization Works

When you assign a QuerySet to a public instance variable and call `super().get_context_data()`:

1. **Template Analysis**: JIT analyzes the template (including `{% include %}` directives) to extract all field access paths
   - Example: `{{ request.property.name }}` → extracts `property.name`
   - Example: `{{ tenant.user.get_full_name }}` → extracts `tenant.user`
   - Included templates are inlined so their variables are also discovered

2. **Automatic Optimization**: JIT automatically applies `select_related()`, `prefetch_related()`, and model-declared annotations
   - Eliminates N+1 query problems
   - You don't need to manually optimize (though you can for complex cases)
   - `_djust_annotations` on the model class are applied automatically

3. **Rust Serialization with Python Fallback**: QuerySet serialized using Rust (10-100x faster than Python)
   - Sub-millisecond serialization for hundreds of objects
   - Automatic type conversion and JSON formatting
   - Falls back to Python codegen for `@property` attributes that Rust can't access

4. **Auto-Generated Variables**: JIT creates convenience variables
   - `items_count` automatically created from `len(items)`
   - Avoids redundant `COUNT(*)` queries

## Common Mistakes to Avoid

### ❌ Mistake 1: Assigning Directly to Public Variable

```python
def _refresh_items(self):
    items = MyModel.objects.all()
    self.items = items  # WRONG: May trigger premature serialization
```

**Fix**: Use private variable:
```python
def _refresh_items(self):
    items = MyModel.objects.all()
    self._items = items  # CORRECT: Prevents premature serialization
```

### ❌ Mistake 2: Converting QuerySet to List

```python
def _refresh_items(self):
    items = MyModel.objects.all()
    # WRONG: sorted() converts to list, breaking JIT
    items = sorted(items, key=lambda x: x.priority)
    self._items = items
```

**Fix**: Use Django's database-level sorting:
```python
def _refresh_items(self):
    items = MyModel.objects.all()
    # CORRECT: Keeps as QuerySet
    from django.db.models import Case, When, IntegerField
    items = items.annotate(
        priority_order=Case(
            When(priority='urgent', then=0),
            When(priority='high', then=1),
            default=2,
            output_field=IntegerField(),
        )
    ).order_by('priority_order')
    self._items = items
```

### ❌ Mistake 3: Adding QuerySet to Context Dict

```python
def get_context_data(self, **kwargs):
    self.items = self._items
    context = super().get_context_data(**kwargs)
    context.update({
        'items': self.items,  # WRONG: Overrides JIT serialization
    })
    return context
```

**Fix**: Let parent handle serialization:
```python
def get_context_data(self, **kwargs):
    self.items = self._items
    context = super().get_context_data(**kwargs)
    # CORRECT: Don't add 'items' to context
    context.update({
        'search_query': self.search_query,  # Only non-model context
    })
    return context
```

### ❌ Mistake 4: Forgetting to Assign to Public Variable

```python
def get_context_data(self, **kwargs):
    # WRONG: Forgot to assign self.items = self._items
    context = super().get_context_data(**kwargs)
    context.update({'items': self._items})  # Manual serialization (slow!)
    return context
```

**Fix**: Assign to public variable first:
```python
def get_context_data(self, **kwargs):
    self.items = self._items  # CORRECT: Triggers JIT serialization
    context = super().get_context_data(**kwargs)
    # Don't add 'items' to context - parent already did it
    return context
```

## Performance Benefits

Using the JIT pattern provides:

- **10-100x faster serialization** - Rust vs Python
- **Sub-millisecond VDOM diffing** - Fast template rendering
- **Automatic query optimization** - No N+1 queries
- **Smaller payloads** - Efficient serialization format
- **Auto-generated counts** - Avoids redundant COUNT(*) queries

### Benchmark Example

For a list of 100 maintenance requests with related properties and tenants:

| Approach | Serialization Time | Queries |
|----------|-------------------|---------|
| Manual Python serialization | ~50ms | 201 (N+1 problem) |
| JIT without select_related | ~5ms | 201 (N+1 problem) |
| **JIT with pattern** | **~2ms** | **1 query** |

## Checklist for Implementation

When refactoring a view to use JIT pattern:

- [ ] Store QuerySet in **private variable** (`self._items`) in refresh method
- [ ] Keep QuerySet as QuerySet (don't convert to list with `sorted()`, `list()`, etc.)
- [ ] Use Django's `order_by()` for sorting, not Python's `sorted()`
- [ ] Assign to **public variable** (`self.items = self._items`) in `get_context_data()`
- [ ] Call `super().get_context_data()` to trigger JIT serialization
- [ ] Don't add QuerySet to context dict (parent already did it)
- [ ] Only add non-model context to context dict
- [ ] Verify JIT is working by checking server logs for `[JIT] Serialized N objects`

## Debugging JIT Serialization

Enable debug mode to see detailed JIT logging:

```python
# In Django settings
LIVEVIEW_CONFIG = {
    'debug_vdom': True,  # Enable detailed VDOM and JIT logging
}
```

Or temporarily in code:

```python
from djust.config import config
config.set('debug_vdom', True)
```

You should see logs like:

```
[JIT] Serialized 7 MaintenanceRequest objects for 'requests' using Rust
[JIT] Extracted paths from template: ['property.name', 'tenant.user.get_full_name', 'created_at']
[JIT] Auto-applied select_related: ['property', 'tenant__user']
```

## JIT Serialization Behavior

This section documents exactly what gets serialized and common gotchas.

### What Gets Serialized

When a Django model is passed to a template, djust automatically serializes:

#### 1. All Model Fields
All concrete fields on the model are serialized:
- CharField, TextField, IntegerField, etc. → their values
- DateTimeField, DateField, TimeField → ISO 8601 strings (see [Datetime Gotcha](#datetime-fields-become-iso-strings))
- ForeignKey → `{id: ..., __str__: ...}` (if not prefetched)
- ForeignKey (prefetched) → full nested serialization

#### 2. `@property` Attributes
Python `@property` attributes on models are automatically serialized. The Rust serializer can't access these directly, so JIT falls back to Python codegen when it detects missing keys:

```python
class MyModel(models.Model):
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)

    @property
    def full_name(self):            # ✅ Auto-serialized via codegen fallback
        return f"{self.first_name} {self.last_name}"

    @property
    def is_active(self):            # ✅ Auto-serialized
        return self.status == "active"
```

#### 3. Methods Starting with `get_`
Methods following Django's convention are auto-called:

```python
class MyModel(models.Model):
    status = models.CharField(choices=STATUS_CHOICES)

    def get_status_display(self):   # ✅ Auto-called (Django convention)
        return dict(STATUS_CHOICES).get(self.status)

    def get_full_name(self):        # ✅ Auto-called (returns string)
        return f"{self.first_name} {self.last_name}"

    def get_related_items(self):    # ❌ NOT included (returns list)
        return list(self.items.all())
```

**Rules for `get_*` methods:**
- Must take no arguments (other than `self`)
- Must return simple types: `str`, `int`, `float`, `bool`, or `None`
- Complex return types (list, dict, queryset) are silently ignored

#### 4. Many-to-Many Relations via `.all()`
M2M relations accessed with `.all()` in templates are now serialized with iteration:

```html
<!-- Template -->
{% for tag in product.tags.all %}
    <span>{{ tag.name }}</span>
{% endfor %}
```

JIT generates codegen that iterates `.all()` and serializes each related object.

#### 5. Nested Dicts with Model Values
Dicts containing Model or QuerySet values are recursively deep-serialized:

```python
def get_context_data(self, **kwargs):
    self.dashboard = {
        "recent_order": Order.objects.latest("created_at"),
        "top_products": Product.objects.order_by("-sales")[:5],
    }
    return super().get_context_data(**kwargs)
```

#### 6. `list[Model]` (Not Just QuerySets)
Plain Python lists of model instances now receive full JIT optimization. JIT re-fetches the objects with optimized `select_related`/`prefetch_related` based on template analysis:

```python
def mount(self, request, **kwargs):
    # Even plain lists get JIT optimization
    self.featured = list(Product.objects.filter(featured=True)[:3])
```

#### 3. Skipped Methods (For Performance)
These methods are intentionally skipped:
- `get_next_by_*` / `get_previous_by_*` (Django auto-generated, trigger cursor queries)
- `get_all_permissions` / `get_user_permissions` / `get_group_permissions` (auth methods)
- `get_session_auth_hash` (auth method)
- `get_deferred_fields` (Django internal)

### Common Gotchas

#### Datetime Fields Become ISO Strings

**Problem:** Django template filters like `|date` don't work with ISO strings.

```python
# Model
class Event(models.Model):
    start_time = models.DateTimeField()
```

```html
<!-- ❌ This WON'T work as expected -->
{{ event.start_time|date:"M d, Y" }}
<!-- Outputs: "2024-01-15T09:30:00" (the raw string) -->
```

**Solution:** Use a `get_*` method to format dates:

```python
class Event(models.Model):
    start_time = models.DateTimeField()

    def get_start_time_formatted(self):
        """Return formatted date string for templates."""
        if self.start_time:
            return self.start_time.strftime("%b %d, %Y")
        return ""
```

```html
<!-- ✅ This works -->
{{ event.get_start_time_formatted }}
<!-- Outputs: "Jan 15, 2024" -->
```

#### Related Objects Without Prefetching

**Problem:** Accessing unprefetched relations only returns `{id, __str__}`.

```html
<!-- ❌ Without prefetching -->
{{ order.customer.email }}
<!-- May return empty or error - customer is only {id, __str__} -->
```

**Solution:** JIT automatically adds `select_related` based on template analysis, or explicitly prefetch:

```python
def _refresh_orders(self):
    self._orders = Order.objects.select_related('customer').all()
```

#### Complex Method Return Types Ignored

**Problem:** Methods returning lists or dicts are silently skipped.

```python
class Product(models.Model):
    def get_all_tags(self):
        return list(self.tags.all())  # ❌ Returns list - will be ignored

    def get_metadata(self):
        return {"weight": 100, "color": "red"}  # ❌ Returns dict - will be ignored
```

**Solution:** Return simple types or serialize in template:

```python
class Product(models.Model):
    def get_tags_display(self):
        return ", ".join(tag.name for tag in self.tags.all())  # ✅ Returns string

    def get_weight(self):
        return 100  # ✅ Returns int
```

### Debugging Serialization Issues

If data isn't appearing in your template, check:

1. **Enable JIT debug mode:**
   ```python
   # settings.py
   LIVEVIEW_CONFIG = {
       'jit_debug': True,  # Shows detailed serialization info
   }
   ```

2. **Check server logs for:**
   ```
   [JIT] Serialized 10 MyModel objects for 'items'
   [JIT] Extracted paths: ['name', 'status', 'get_status_display']
   ```

3. **Verify your method:**
   - Starts with `get_`
   - Takes no arguments
   - Returns str/int/float/bool/None

4. **For datetime issues:**
   - Create a `get_*_formatted()` method
   - Or use JavaScript to format in the client

### Cache Invalidation

JIT serializers are cached for performance. The cache key includes:
- Template content hash (SHA-256, changes when template changes)
- Variable name
- Model structure hash (changes when model fields change)

Template hashes are further cached by `id(template_content)` to avoid recomputing the SHA-256 for every variable in the same request. This is safe because the template string is kept alive by the caller for the duration of `get_context_data()`.

**In development**, the cache auto-clears when Django's autoreloader runs.

**To manually clear the cache:**
```python
from djust import clear_jit_cache

clear_jit_cache()  # Returns number of entries cleared
```

## References

- **Implementation Examples**:
  - `examples/demo_project/djust_rentals/views/properties.py` - PropertyListView
  - `examples/demo_project/djust_rentals/views/tenants.py` - TenantListView
  - `examples/demo_project/djust_rentals/views/maintenance.py` - MaintenanceListView
  - `examples/demo_project/djust_rentals/views/leases.py` - LeaseListView

- **Core Implementation**:
  - `python/djust/mixins/jit.py` - JIT serialization mixin (template resolution, Rust/codegen serialization)
  - `python/djust/mixins/context.py` - Context data management (JIT dispatch for QuerySets, Models, lists, dicts)
  - `python/djust/optimization/codegen.py` - Python serializer code generation
  - `python/djust/optimization/query_optimizer.py` - Automatic select_related/prefetch_related/annotation optimization
  - `python/djust/serialization.py` - DjangoJSONEncoder and @property serialization

- **Documentation**:
  - `CLAUDE.md` - Project overview and architecture
  - `README.md` - User-facing documentation
