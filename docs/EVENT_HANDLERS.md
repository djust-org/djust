# Event Handler Best Practices

This guide explains how to write effective event handlers in djust LiveView applications, including parameter naming conventions, type validation, and debugging tips.

## Table of Contents

- [The Signature Is the Contract](#the-signature-is-the-contract)
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

## The Signature Is the Contract

Under the strict policy (the only policy since djust 2.0), a handler receives exactly the parameters its signature declares. Declare each argument the binding sends, and nothing else: `**kwargs` is not required, and a handler that does not read it should not take it.

```python
@event_handler()
def increment(self):
    self.count += 1

@event_handler()
def search(self, value: str = ""):
    self.query = value

@event_handler()
def delete_item(self, item_id: int = 0):
    Item.objects.filter(id=item_id).delete()
```

Legacy `dj-input`/`dj-change` bindings also send `field` and `_target`, and `dj-submit` sends `_target` with the form fields: declare those names, or take a typed catch-all (`**fields: str`) that the body reads. See [Event arguments (`dj-value-*`)](website/core-concepts/events.md#event-arguments-dj-value) for the full rule.

## Parameter Naming Convention

### Always use `value` for form input events

Form elements (`<input>`, `<select>`, `<textarea>`) send their value using the `value` parameter when using `dj-input` or `dj-change` events.

**✅ Correct:**
```python
@event_handler()
def search(self, value: str = ""):
    """value matches what dj-input/dj-change sends"""
    self.search_query = value
    self._refresh_properties()

@event_handler()
def filter_by_status(self, value: str = "all"):
    """value parameter for select/dropdown changes"""
    self.filter_status = value
    self._refresh_properties()
```

**❌ Wrong:**
```python
@event_handler()
def search(self, query: str = ""):
    """Won't work! dj-input sends 'value', not 'query'"""
    self.search_query = query  # Will always be "" (default)
```

### `field` parameter for shared handlers

When multiple form fields share one `dj-change` or `dj-input` handler, declare `field` to learn which field triggered the event (the element's `name` attribute, falling back to `id`):

```python
@event_handler()
def validate(self, value: str, field: str):
    """field tells you which field triggered the event"""
    if field == "email":
        self.email_error = "" if "@" in value else "Invalid email"
    elif field == "username":
        self.username_error = "" if len(value) >= 3 else "Too short"
```

For `dj-submit`, `_target` is the submitter button's `name` (if available); declare `_target` to receive it. Matches Phoenix LiveView's `_target` convention.

### Custom event parameters

For custom events (not form inputs), pass parameters via `dj-value-*` attributes.
The `dj-value-` prefix is stripped and kebab-case becomes snake_case, so
`dj-value-property-id` maps to the `property_id` Python parameter:

```python
@event_handler()
def delete_property(self, property_id: int):
    """property_id comes from dj-value-property-id attribute"""
    property = Property.objects.get(id=property_id)
    property.delete()
```

Template usage:
```html
<button dj-click="delete_property" dj-value-property-id="{{ property.id }}">
    Delete
</button>
```

> **Note:** Plain `data-*` attributes are not event arguments: a strict handler never
> receives them. Use `dj-value-*`.

## Inline Handler Arguments

You can pass arguments directly in the handler attribute using function-call syntax. This provides a cleaner alternative to `dj-value-*` attributes for simple values.

### Basic Usage

```html
<!-- Instead of using dj-value-* attributes -->
<button dj-click="set_period" dj-value-value="month">30 Days</button>

<!-- You can use inline arguments -->
<button dj-click="set_period('month')">30 Days</button>
```

Both approaches call the same handler:

```python
@event_handler()
def set_period(self, value: str):
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

**Limitation**: Only primitive types are supported. Arrays and objects (e.g., `handler([1,2,3])` or `handler({key: 'value'})`) are not supported. For complex data, use a `dj-value-*` attribute with a JSON string and parse it in your handler.

### Multiple Arguments

Pass multiple arguments separated by commas:

```html
<button dj-click="sort_by('name', true)">Sort by Name (Ascending)</button>
<button dj-click="filter('status', 'active', 1)">Active Items</button>
```

```python
@event_handler()
def sort_by(self, field: str, ascending: bool = True):
    """Sort items by field"""
    self.sort_field = field
    self.sort_ascending = ascending

@event_handler()
def filter(self, field: str, value: str, page: int = 1):
    """Filter items"""
    self.filters[field] = value
    self.current_page = page
```

### Combining with dj-value-* Attributes

You can combine inline arguments with `dj-value-*` attributes. Inline arguments map to parameters by position, while `dj-value-*` attributes map by name:

```html
<button dj-click="update_item('delete')"
        dj-value-item-id="{{ item.id }}"
        dj-value-confirm="true">
    Delete Item
</button>
```

```python
@event_handler()
def update_item(self, action: str, item_id: int = 0, confirm: bool = False):
    """
    action comes from inline arg ('delete')
    item_id comes from dj-value-item-id
    confirm comes from dj-value-confirm
    """
    if action == 'delete' and confirm:
        Item.objects.filter(id=item_id).delete()
```

**Note**: If both an inline argument and a `dj-value-*` attribute provide the same parameter, the inline argument takes precedence.

### When to Use Each Approach

| Approach | Best For |
|----------|----------|
| **Inline arguments** | Static values known at template render time |
| **dj-value-* attributes** | Dynamic values from template context (e.g., `{{ item.id }}`) |
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
    <button dj-click="item_action('edit')" dj-value-item-id="{{ item.id }}">Edit</button>
    <button dj-click="item_action('delete')" dj-value-item-id="{{ item.id }}">Delete</button>
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
from djust.decorators import event_handler, debounce, throttle

@event_handler()
@debounce(wait=0.5)
def search(self, value: str = ""):
    """Debounced search - waits 500ms after user stops typing"""
    self.search_query = value
    self._refresh_results()

@event_handler()
@throttle(interval=1.0)
def delete_item(self, item_id: int):
    """At most one delete per second from a double-clicking user"""
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
    count = state(0)
    weather = state(None)

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

Template `dj-value-*` attributes always send values as strings. djust **automatically coerces** these strings to the expected types based on your type hints:

```python
@event_handler()
def update_quantity(self, item_id: int, quantity: int = 1):
    """
    item_id and quantity are automatically coerced from strings.
    Template sends: dj-value-item-id="123" dj-value-quantity="5"
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
):
    pass
```

### Disabling Type Coercion

If you need raw string values, disable coercion with `coerce_types=False`:

```python
@event_handler(coerce_types=False)
def raw_handler(self, value: str = ""):
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

The template gets the view's declared `state()` fields and what
`get_context_data()` returns ([explicit exposure](website/state/explicit-exposure.md),
the default since djust 2.0). Any other attribute a view sets on `self` stays on
the server:

- **`search_query = state("")`** on the class puts `{{ search_query }}` in the template
- **`self._properties = items`** stays on the view, for your handlers; `get_context_data()` hands the template a derived or render-only value such as this QuerySet
- **`_private`** is a naming convention for internal values such as QuerySets; it is not what keeps them out of the template (an undeclared public attribute stays out too)

### Why This Matters

The JIT Serialization Pattern (see `docs/JIT_SERIALIZATION_PATTERN.md`) hands the QuerySet to the template from `get_context_data()`:

```python
search_query = state("")
filter_status = state("all")

def mount(self, request):
    self._refresh_properties()

def _refresh_properties(self):
    """Private helper method - build QuerySet"""
    items = Property.objects.all()

    if self.search_query:
        items = items.filter(name__icontains=self.search_query)

    if self.filter_status != "all":
        items = items.filter(status=self.filter_status)

    self._properties = items

def get_context_data(self):
    """Derived and render-only values; the declared fields are already in it."""
    context = super().get_context_data(**kwargs)
    context["properties"] = self._properties  # serialized by the Rust JIT
    return context
```

### Benefits

1. **Clarity**: the class body and `get_context_data()` are the two places that say what the template sees
2. **Safety**: an attribute you did not declare or return cannot be rendered by accident

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

### Taking a Catch-All

Add a typed `**fields: str` only when the body reads it (for example, a form whose field names are not known ahead of time):

```python
@event_handler()
def save_form(self, **fields: str):
    for name, value in fields.items():
        setattr(self, name, value)
```

## Common Patterns

### Search with Debouncing

```python
@event_handler()
@debounce(wait=0.5)
def search(self, value: str = ""):
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
def filter_by_status(self, value: str = "all"):
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

**Solution**: Declare the parameter the binding sends (the signature is the contract), or remove the extra `dj-value-*` attribute:

```python
# Strict - rejects a parameter the signature does not declare
def handler(self, value: str = ""):
    pass

# Declare the extra name
def handler(self, value: str = "", extra: str = ""):
    pass
```

#### Issue: Type coercion fails

**Symptoms**: Error: `expected int, got str` with hint about coercion failure

**Cause**: The string value can't be converted to the expected type (e.g., `"abc"` can't become an `int`).

**Solution**: Ensure template sends valid values:

```python
# Template sends invalid string for int
<button dj-click="delete_item" dj-value-item-id="not_a_number">Delete</button>

# ❌ This will fail - "not_a_number" can't be coerced to int
@event_handler()
def delete_item(self, item_id: int):
    pass

# ✅ Correct - template sends valid integer string
<button dj-click="delete_item" dj-value-item-id="{{ item.id }}">Delete</button>

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
from djust import LiveView, state
from djust.decorators import event_handler

class MyView(LiveView):
    count = state(0, persist="server")
    query = state("")

    @event_handler
    def increment(self):
        """Callable via WebSocket"""
        self.count += 1

    @event_handler(description="Search items")
    def search(self, value: str = ""):
        """Also callable via WebSocket"""
        self.query = value

    def mount(self, request):
        """NOT callable via WebSocket (not decorated)"""
        self.query = ""

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
    def generate_report(self):
        """Limited to 2/sec sustained, 3 burst"""
        self.report = expensive_computation()
```

### Configuration

In `settings.py`:

```python
LIVEVIEW_CONFIG = {
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
- **[State Management Decorators](state-management/STATE_MANAGEMENT_API.md)** - @debounce, @throttle, @cache
