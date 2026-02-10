# Event Handler Best Practices

This guide explains how to write effective event handlers in djust LiveView applications, including parameter naming conventions, type validation, and debugging tips.

## Table of Contents

- [Parameter Naming Convention](#parameter-naming-convention)
- [Inline Handler Arguments](#inline-handler-arguments)
- [The @event_handler Decorator](#the-event_handler-decorator)
- [Async Event Handlers](#async-event-handlers)
- [Type Hints and Validation](#type-hints-and-validation)
- [Public vs Private Variables](#public-vs-private-variables)
- [Error Handling](#error-handling)
- [Common Patterns](#common-patterns)
- [Debugging Tips](#debugging-tips)
- [Security](#security)

## Parameter Naming Convention

### Always use `value` for form input events

Form elements (`<input>`, `<select>`, `<textarea>`) send their value using the `value` parameter when using `dj-input` or `dj-change` events.

**✅ Correct:**
```python
@event_handler()
def search(self, value: str = "", **kwargs):
    """value matches what dj-input/dj-change sends"""
    self.search_query = value
    self._refresh_properties()

@event_handler()
def filter_by_status(self, value: str = "all", **kwargs):
    """value parameter for select/dropdown changes"""
    self.filter_status = value
    self._refresh_properties()
```

**❌ Wrong:**
```python
@event_handler()
def search(self, query: str = "", **kwargs):
    """Won't work! dj-input sends 'value', not 'query'"""
    self.search_query = query  # Will always be "" (default)
```

### Custom event parameters

For custom events (not form inputs), pass parameters via `data-dj-*` attributes.
The `data-dj-` prefix is stripped automatically, so `data-dj-property-id` maps to
the `property_id` Python parameter:

```python
@event_handler()
def delete_property(self, property_id: int, **kwargs):
    """property_id comes from data-dj-property-id attribute"""
    property = Property.objects.get(id=property_id)
    property.delete()
```

Template usage:
```html
<button dj-click="delete_property" data-dj-property-id="{{ property.id }}">
    Delete
</button>
```

> **Note:** Plain `data-*` attributes (without the `dj-` prefix) also work —
> `data-property-id` maps to `property_id` too. The `data-dj-*` convention is
> recommended for consistency with `dj-click`, `dj-change`, and other djust
> attributes, and to avoid collisions with non-djust libraries.

## Inline Handler Arguments

You can pass arguments directly in the handler attribute using function-call syntax. This provides a cleaner alternative to `data-*` attributes for simple values.

### Basic Usage

```html
<!-- Instead of using data-* attributes -->
<button dj-click="set_period" data-value="month">30 Days</button>

<!-- You can use inline arguments -->
<button dj-click="set_period('month')">30 Days</button>
```

Both approaches call the same handler:

```python
@event_handler()
def set_period(self, value: str, **kwargs):
    """Set the time period filter"""
    self.period = value
    self._refresh_data()
```

### Supported Argument Types

Inline arguments are automatically parsed into their appropriate types:

| Syntax | Parsed Value | Python Type |
|--------|--------------|-------------|
| `'string'` or `"string"` | `"string"` | `str` |
| `123` | `123` | `int` |
| `45.67` | `45.67` | `float` |
| `true` / `false` | `True` / `False` | `bool` |
| `null` | `None` | `NoneType` |

**Limitation**: Only primitive types are supported. Arrays and objects (e.g., `handler([1,2,3])` or `handler({key: 'value'})`) are not supported. For complex data, use `data-*` attributes with JSON strings and parse them in your handler.

### Multiple Arguments

Pass multiple arguments separated by commas:

```html
<button dj-click="sort_by('name', true)">Sort by Name (Ascending)</button>
<button dj-click="filter('status', 'active', 1)">Active Items</button>
```

```python
@event_handler()
def sort_by(self, field: str, ascending: bool = True, **kwargs):
    """Sort items by field"""
    self.sort_field = field
    self.sort_ascending = ascending

@event_handler()
def filter(self, field: str, value: str, page: int = 1, **kwargs):
    """Filter items"""
    self.filters[field] = value
    self.current_page = page
```

### Combining with data-dj-* Attributes

You can combine inline arguments with `data-dj-*` attributes. Inline arguments map to parameters by position, while `data-dj-*` attributes map by name:

```html
<button dj-click="update_item('delete')"
        data-dj-item-id="{{ item.id }}"
        data-dj-confirm="true">
    Delete Item
</button>
```

```python
@event_handler()
def update_item(self, action: str, item_id: int = 0, confirm: bool = False, **kwargs):
    """
    action comes from inline arg ('delete')
    item_id comes from data-dj-item-id
    confirm comes from data-dj-confirm
    """
    if action == 'delete' and confirm:
        Item.objects.filter(id=item_id).delete()
```

**Note**: If both an inline argument and a `data-*` attribute provide the same parameter, the inline argument takes precedence.

### When to Use Each Approach

| Approach | Best For |
|----------|----------|
| **Inline arguments** | Static values known at template render time |
| **data-* attributes** | Dynamic values from template context (e.g., `{{ item.id }}`) |
| **Combined** | Action type as inline arg + entity ID from context |

### Examples

#### Tab Selection
```html
<div class="tabs">
    <button dj-click="select_tab(0)">Overview</button>
    <button dj-click="select_tab(1)">Details</button>
    <button dj-click="select_tab(2)">Settings</button>
</div>
```

#### Period Selector
```html
<div class="period-selector">
    <button dj-click="set_period('day')">Today</button>
    <button dj-click="set_period('week')">This Week</button>
    <button dj-click="set_period('month')">This Month</button>
    <button dj-click="set_period('year')">This Year</button>
</div>
```

#### Action Buttons with Context
```html
{% for item in items %}
<div class="item-row">
    <span>{{ item.name }}</span>
    <button dj-click="item_action('edit')" data-dj-item-id="{{ item.id }}">Edit</button>
    <button dj-click="item_action('delete')" data-dj-item-id="{{ item.id }}">Delete</button>
</div>
{% endfor %}
```

## The @event_handler Decorator

### Basic Usage

Mark all event handlers with `@event_handler()` to enable:
- Parameter introspection
- Debug panel discovery
- IDE autocomplete support
- Automatic validation

```python
from djust.decorators import event_handler

@event_handler()
def my_handler(self, value: str = ""):
    """This description appears in the debug panel"""
    self.my_value = value
```

### With Other Decorators

Stack decorators in order (top to bottom):

```python
from djust.decorators import event_handler, debounce, optimistic

@event_handler()
@debounce(wait=0.5)
def search(self, value: str = "", **kwargs):
    """Debounced search - waits 500ms after user stops typing"""
    self.search_query = value
    self._refresh_results()

@event_handler()
@optimistic
def delete_item(self, item_id: int):
    """Optimistic delete - UI updates before server confirms"""
    Item.objects.filter(id=item_id).delete()
```

### Optional Description

```python
@event_handler(description="Sort properties by selected field")
def sort_properties(self, value: str = "name"):
    """Explicit description overrides docstring"""
    self.sort_by = value
    self._refresh_properties()
```

## Async Event Handlers

Event handlers can be defined as either sync or async functions. Use async handlers when you need to perform non-blocking I/O operations.

### Basic Async Handler

```python
@event_handler()
async def fetch_external_data(self, item_id: int):
    """Async handler for non-blocking external API calls"""
    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://api.example.com/items/{item_id}") as response:
            self.item_data = await response.json()
```

### When to Use Async Handlers

| Use Case | Sync or Async |
|----------|---------------|
| Database queries (Django ORM) | Sync (use `sync_to_async` inside if needed) |
| External API calls | Async |
| File I/O with `aiofiles` | Async |
| Simple state updates | Sync |

### Mixing Sync and Async

You can have both sync and async handlers in the same LiveView:

```python
class MyView(LiveView):
    @event_handler()
    def increment(self):
        """Sync handler for simple state update"""
        self.count += 1

    @event_handler()
    async def fetch_weather(self, city: str):
        """Async handler for external API"""
        self.weather = await get_weather_async(city)
```

## Type Hints and Validation

### Automatic Type Coercion

Template `data-dj-*` attributes always send values as strings. djust **automatically coerces** these strings to the expected types based on your type hints:

```python
@event_handler()
def update_quantity(self, item_id: int, quantity: int = 1):
    """
    item_id and quantity are automatically coerced from strings.
    Template sends: data-dj-item-id="123" data-dj-quantity="5"
    Handler receives: item_id=123, quantity=5 (as integers)
    """
    self.items[item_id].quantity = quantity
```

This means you can use proper type hints and djust handles the conversion automatically.

### Supported Type Coercions

| Type | String Input | Coerced Value |
|------|--------------|---------------|
| `int` | `"123"`, `"-45"` | `123`, `-45` |
| `float` | `"3.14"`, `"-273.15"` | `3.14`, `-273.15` |
| `bool` | `"true"`, `"1"`, `"yes"`, `"on"` | `True` |
| `bool` | `"false"`, `"0"`, `"no"`, `"off"`, `""` | `False` |
| `Decimal` | `"123.45"` | `Decimal("123.45")` |
| `UUID` | `"550e8400-e29b-..."` | `UUID("550e8400-...")` |
| `list` | `"a,b,c"` | `["a", "b", "c"]` |
| `List[int]` | `"1,2,3"` | `[1, 2, 3]` |
| `Optional[int]` | `"42"` | `42` |

### Example Usage

```python
@event_handler()
def example_types(
    self,
    name: str = "",           # String (no conversion needed)
    count: int = 0,           # Coerced from "123" to 123
    price: float = 0.0,       # Coerced from "9.99" to 9.99
    active: bool = False,     # Coerced from "true" to True
    tags: list = [],          # Coerced from "a,b,c" to ["a","b","c"]
    **kwargs                  # Accept any additional params
):
    pass
```

### Disabling Type Coercion

If you need raw string values, disable coercion with `coerce_types=False`:

```python
@event_handler(coerce_types=False)
def raw_handler(self, value: str = "", **kwargs):
    """Receives raw string values from template, no coercion"""
    # value is exactly what the template sent
    pass
```

### Optional Parameters

Use default values to make parameters optional:

```python
@event_handler()
def filter_items(
    self,
    category: str = "all",        # Optional with default
    min_price: float = 0.0,       # Optional with default
    max_price: float = 9999.99,   # Optional with default
    **kwargs
):
    """All parameters are optional with sensible defaults"""
    queryset = Item.objects.all()

    if category != "all":
        queryset = queryset.filter(category=category)

    queryset = queryset.filter(price__gte=min_price, price__lte=max_price)
    self.items = queryset
```

### Required Parameters

Omit default values to make parameters required:

```python
@event_handler()
def create_item(self, name: str, price: float):
    """
    name and price are REQUIRED.
    Calling without them will raise a validation error.
    """
    Item.objects.create(name=name, price=price)
```

## Public vs Private Variables

### Convention

djust uses a naming convention to distinguish public and private instance variables:

- **`_private`** - Not exposed to template context
- **`public`** - Auto-exposed to template context

### Why This Matters

The JIT Serialization Pattern (see `docs/JIT_SERIALIZATION_PATTERN.md`) relies on this convention:

```python
def mount(self, request):
    # Private - internal state, not in template
    self._properties = Property.objects.all()

    # Public - auto-exposed to template
    self.properties = None  # Set in get_context_data()
    self.search_query = ""
    self.filter_status = "all"

def _refresh_properties(self):
    """Private helper method - build QuerySet"""
    items = Property.objects.all()

    if self.search_query:
        items = items.filter(name__icontains=self.search_query)

    if self.filter_status != "all":
        items = items.filter(status=self.filter_status)

    self._properties = items  # Store in PRIVATE variable

def get_context_data(self, **kwargs):
    """Expose for JIT serialization"""
    self.properties = self._properties  # Assign to PUBLIC variable
    context = super().get_context_data(**kwargs)  # Triggers Rust JIT
    return context
```

### Benefits

1. **Clarity**: Easy to see what's exposed to templates
2. **Performance**: JIT serialization only processes public variables
3. **Debugging**: Debug panel only shows public variables
4. **Safety**: Private state can't be accidentally accessed in templates

## Error Handling

### Parameter Validation Errors

djust validates parameters **strictly by default**:

- **Missing required parameters** → Validation error
- **Unexpected parameters** → Validation error
- **Wrong types** → Validation error

Example validation error:

```
Handler 'search' received unexpected parameters: ['query']. Expected: ['value']
```

### Checking for Errors

#### In Browser Console

Open browser DevTools → Console:

```javascript
[LiveView] Parameter validation failed: Handler 'search' received unexpected parameters: ['query']. Expected: ['value']
```

#### In Debug Panel

1. Open debug panel (`Ctrl+Shift+D`)
2. Go to **Event History** tab
3. Error events are highlighted in red
4. Click to see validation details

#### In Django Logs

```python
ERROR    Handler 'search' missing required parameters: ['name']
```

### Using **kwargs for Flexibility

Accept any parameters with `**kwargs`:

```python
@event_handler()
def flexible_handler(self, value: str = "", **kwargs):
    """
    Accepts any parameters beyond 'value'.
    Useful when:
    - Parameters are optional/dynamic
    - Template might send data-* attributes
    - You want to future-proof the handler
    """
    self.value = value

    # Access optional parameters
    if 'extra_data' in kwargs:
        self.extra_data = kwargs['extra_data']
```

## Common Patterns

### Search with Debouncing

```python
@event_handler()
@debounce(wait=0.5)
def search(self, value: str = "", **kwargs):
    """
    Wait 500ms after user stops typing before searching.
    Reduces server load and improves UX.
    """
    self.search_query = value
    self._refresh_results()
```

Template:
```html
<input type="text"
       dj-input="search"
       value="{{ search_query }}"
       placeholder="Search...">
```

### Filtering with Dropdowns

```python
@event_handler()
def filter_by_status(self, value: str = "all", **kwargs):
    """Filter items by status dropdown selection"""
    self.filter_status = value
    self._refresh_results()
```

Template:
```html
<select dj-change="filter_by_status">
    <option value="all" {% if filter_status == "all" %}selected{% endif %}>All</option>
    <option value="active" {% if filter_status == "active" %}selected{% endif %}>Active</option>
    <option value="inactive" {% if filter_status == "inactive" %}selected{% endif %}>Inactive</option>
</select>
```

### Delete with Confirmation

```python
@event_handler()
def show_delete_confirmation(self, item_id: int):
    """Show confirmation dialog before deleting"""
    self.delete_item_id = item_id
    self.show_delete_modal = True

@event_handler()
@optimistic
def confirm_delete(self):
    """Actually delete the item after confirmation"""
    if self.delete_item_id:
        Item.objects.filter(id=self.delete_item_id).delete()
        self.delete_item_id = None
        self.show_delete_modal = False

@event_handler()
def cancel_delete(self):
    """Cancel deletion"""
    self.delete_item_id = None
    self.show_delete_modal = False
```

### Form Submission

```python
@event_handler()
def save_property(
    self,
    name: str = "",
    address: str = "",
    price: float = 0.0,
    **kwargs
):
    """Save property from form submission"""
    if self.edit_property_id:
        # Update existing
        property = Property.objects.get(id=self.edit_property_id)
        property.name = name
        property.address = address
        property.price = price
        property.save()
    else:
        # Create new
        Property.objects.create(
            name=name,
            address=address,
            price=price
        )

    self.edit_property_id = None
    self.show_form = False
    self._refresh_properties()
```

Template:
```html
<form dj-submit="save_property">
    <input type="text" name="name" value="{{ property.name }}">
    <input type="text" name="address" value="{{ property.address }}">
    <input type="number" name="price" value="{{ property.price }}">
    <button type="submit">Save</button>
</form>
```

## Debugging Tips

### Use the Debug Panel

1. **Open**: Press `Ctrl+Shift+D` or click the 🐞 button
2. **Check Event Handlers**: See all registered handlers with their expected parameters
3. **Monitor Events**: Watch events as they fire with actual parameters
4. **Inspect Errors**: Red-highlighted events show validation errors

### Common Issues

#### Issue: Handler not called

**Symptoms**: Click button, nothing happens

**Checklist**:
1. ✅ Is handler decorated with `@event_handler()`?
2. ✅ Is event name correct in template (`dj-click="handler_name"`)?
3. ✅ Check browser console for JavaScript errors
4. ✅ Check debug panel Event History for validation errors

#### Issue: Wrong parameter value

**Symptoms**: Handler receives default value instead of form value

**Solution**: Use `value` parameter for form inputs (dj-input, dj-change events):

```python
# ❌ Wrong
def search(self, query: str = ""):
    pass

# ✅ Correct
def search(self, value: str = ""):
    pass
```

#### Issue: Validation error "unexpected parameters"

**Symptoms**: Error in console: `received unexpected parameters: ['extra']`

**Solution**: Add `**kwargs` to accept any parameters:

```python
# ❌ Strict - rejects unexpected params
def handler(self, value: str = ""):
    pass

# ✅ Flexible - accepts extra params
def handler(self, value: str = "", **kwargs):
    pass
```

#### Issue: Type coercion fails

**Symptoms**: Error: `expected int, got str` with hint about coercion failure

**Cause**: The string value can't be converted to the expected type (e.g., `"abc"` can't become an `int`).

**Solution**: Ensure template sends valid values:

```python
# Template sends invalid string for int
<button dj-click="delete_item" data-dj-item-id="not_a_number">Delete</button>

# ❌ This will fail - "not_a_number" can't be coerced to int
@event_handler()
def delete_item(self, item_id: int):
    pass

# ✅ Correct - template sends valid integer string
<button dj-click="delete_item" data-dj-item-id="{{ item.id }}">Delete</button>

# Handler receives item_id as int (automatically coerced)
@event_handler()
def delete_item(self, item_id: int):
    Item.objects.filter(id=item_id).delete()  # Works!
```

**Note**: With automatic type coercion, you no longer need to manually convert types. Just use proper type hints and ensure templates send valid values.

### Best Practices Checklist

- [ ] All event handlers have `@event_handler` decorator
- [ ] Form input handlers use `value` parameter
- [ ] Type hints specified for all parameters
- [ ] Required vs optional parameters clearly distinguished
- [ ] Docstrings describe what the handler does
- [ ] Private variables start with `_`
- [ ] Public variables don't start with `_`
- [ ] Complex handlers split into helper methods
- [ ] Debug panel used to verify handler signatures
- [ ] Event history checked for validation errors

## Security

### Event Handler Access Control

By default, djust runs in **strict** security mode: only methods decorated with `@event_handler` are callable via WebSocket. Undecorated methods are blocked even if they pass the event name pattern check.

```python
from djust import LiveView
from djust.decorators import event_handler

class MyView(LiveView):
    @event_handler
    def increment(self):
        """Callable via WebSocket"""
        self.count += 1

    @event_handler(description="Search items")
    def search(self, value: str = "", **kwargs):
        """Also callable via WebSocket"""
        self.query = value

    def mount(self, request):
        """NOT callable via WebSocket (not decorated)"""
        self.count = 0

    def _internal_helper(self):
        """NOT callable — underscore prefix blocked by pattern guard"""
        pass
```

### Rate Limiting Expensive Handlers

Use `@rate_limit` to protect expensive operations from abuse:

```python
from djust.decorators import event_handler, rate_limit

class MyView(LiveView):
    @rate_limit(rate=2, burst=3)
    @event_handler
    def generate_report(self, **kwargs):
        """Limited to 2/sec sustained, 3 burst"""
        self.report = expensive_computation()
```

### Configuration

In `settings.py`:

```python
LIVEVIEW_CONFIG = {
    # "strict" (default), "warn", or "open"
    "event_security": "strict",

    # Global rate limit (token bucket) and per-IP connection limits
    "rate_limit": {
        "rate": 100,
        "burst": 20,
        "max_warnings": 3,
        "max_connections_per_ip": 10,
        "reconnect_cooldown": 5,
    },

    # Max WebSocket message size in bytes (0 = no limit)
    "max_message_size": 65536,
}
```

See [Security Guidelines](SECURITY_GUIDELINES.md) for full details.

## See Also

- **[Debug Panel User Guide](DEBUG_PANEL.md)** - Interactive debugging tools
- **[JIT Serialization Pattern](JIT_SERIALIZATION_PATTERN.md)** - Public/private variable usage
- **[State Management Decorators](STATE_MANAGEMENT_API.md)** - @debounce, @optimistic, @cache
