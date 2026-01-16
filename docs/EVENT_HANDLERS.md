# Event Handler Best Practices

This guide explains how to write effective event handlers in djust LiveView applications, including parameter naming conventions, type validation, and debugging tips.

## Table of Contents

- [Parameter Naming Convention](#parameter-naming-convention)
- [The @event_handler Decorator](#the-event_handler-decorator)
- [Type Hints and Validation](#type-hints-and-validation)
- [Public vs Private Variables](#public-vs-private-variables)
- [Error Handling](#error-handling)
- [Common Patterns](#common-patterns)
- [Debugging Tips](#debugging-tips)

## Parameter Naming Convention

### Always use `value` for form input events

Form elements (`<input>`, `<select>`, `<textarea>`) send their value using the `value` parameter when using `@input` or `@change` events.

**✅ Correct:**
```python
@event_handler()
def search(self, value: str = "", **kwargs):
    """value matches what @input/@change sends"""
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
    """Won't work! @input sends 'value', not 'query'"""
    self.search_query = query  # Will always be "" (default)
```

### Custom event parameters

For custom events (not form inputs), you can use any parameter names:

```python
@event_handler()
def delete_property(self, property_id: int, **kwargs):
    """Custom parameter from data-property-id attribute"""
    property = Property.objects.get(id=property_id)
    property.delete()
```

Template usage:
```html
<button @click="delete_property" data-property-id="{{ property.id }}">
    Delete
</button>
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

## Type Hints and Validation

### Runtime Type Validation

djust validates parameter types using type hints at runtime:

```python
@event_handler()
def update_quantity(self, item_id: int, quantity: int = 1):
    """
    Types are validated at runtime.
    Passing a string for item_id will raise a validation error.
    """
    self.items[item_id].quantity = quantity
```

### Supported Types

```python
@event_handler()
def example_types(
    self,
    name: str = "",           # String
    count: int = 0,           # Integer
    price: float = 0.0,       # Float
    active: bool = False,     # Boolean
    **kwargs                  # Accept any additional params
):
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
       @input="search"
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
<select @change="filter_by_status">
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
<form @submit="save_property">
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
2. ✅ Is event name correct in template (`@click="handler_name"`)?
3. ✅ Check browser console for JavaScript errors
4. ✅ Check debug panel Event History for validation errors

#### Issue: Wrong parameter value

**Symptoms**: Handler receives default value instead of form value

**Solution**: Use `value` parameter for form inputs:

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

#### Issue: Type validation fails

**Symptoms**: Error: `expected int, got str`

**Solution**: Ensure type hints match what's sent:

```python
# Template sends string
<button @click="delete_item" data-item-id="123">Delete</button>

# Handler expects int
@event_handler()
def delete_item(self, item_id: int):
    pass

# ✅ Convert in template
<button @click="delete_item" data-item-id="{{ item.id }}">Delete</button>

# ✅ Or accept string and convert
@event_handler()
def delete_item(self, item_id: str):
    item_id = int(item_id)
```

### Best Practices Checklist

- [ ] All event handlers have `@event_handler()` decorator
- [ ] Form input handlers use `value` parameter
- [ ] Type hints specified for all parameters
- [ ] Required vs optional parameters clearly distinguished
- [ ] Docstrings describe what the handler does
- [ ] Private variables start with `_`
- [ ] Public variables don't start with `_`
- [ ] Complex handlers split into helper methods
- [ ] Debug panel used to verify handler signatures
- [ ] Event history checked for validation errors

## See Also

- **[Debug Panel User Guide](DEBUG_PANEL.md)** - Interactive debugging tools
- **[JIT Serialization Pattern](JIT_SERIALIZATION_PATTERN.md)** - Public/private variable usage
- **[State Management Decorators](STATE_MANAGEMENT_API.md)** - @debounce, @optimistic, @cache
