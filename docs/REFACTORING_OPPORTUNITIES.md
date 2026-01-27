# Refactoring Opportunities

This document outlines DRY (Don't Repeat Yourself) improvements identified during the 0.2.0 release code review. These are good candidates for future refactoring after the release stabilizes.

## Estimated Impact

| File | Improvement | Lines Saved | Priority |
|------|-------------|-------------|----------|
| client.js | Component ID lookup helper | ~32 | High |
| client.js | Form event params helper | ~60 | Medium |
| base.py | Rust instance creation | ~25 | High |
| base.py | Template rendering fallback | ~20 | High |
| websocket.py | Error response helper | ~50+ | High |
| websocket.py | Patch/HTML response helper | ~60 | High |
| decorators.py | Decorator wrapper pattern | ~30 | Medium |

**Total: ~275+ lines of duplicated code**

---

## 1. client.js - Component ID Lookup Pattern

### Problem

The code for walking up the DOM tree to find a parent component's `data-component-id` is duplicated across 8 event handlers.

### Locations

- Line ~1439 (dj-click handler)
- Line ~1465 (dj-click with modifiers)
- Line ~1510 (change event)
- Line ~1549 (input event)
- Line ~1584 (blur event)
- Line ~1609 (focus event)
- Line ~1649 (contenteditable handler)
- Line ~2136 (keyboard handler)

### Current Pattern

```javascript
let currentElement = e.target;
while (currentElement && currentElement !== document.body) {
    if (currentElement.dataset.componentId) {
        params.component_id = currentElement.dataset.componentId;
        break;
    }
    currentElement = currentElement.parentElement;
}
```

### Proposed Refactoring

```javascript
/**
 * Find the closest parent component ID by walking up the DOM tree.
 * @param {HTMLElement} element - Starting element
 * @returns {string|null} - Component ID or null if not found
 */
function getComponentId(element) {
    let currentElement = element;
    while (currentElement && currentElement !== document.body) {
        if (currentElement.dataset.componentId) {
            return currentElement.dataset.componentId;
        }
        currentElement = currentElement.parentElement;
    }
    return null;
}
```

Then replace all 8 instances with:

```javascript
const componentId = getComponentId(e.target);
if (componentId) {
    params.component_id = componentId;
}
```

---

## 2. client.js - Form Event Params Helper

### Problem

Multiple event handlers (change, input, blur, focus) follow an identical pattern for building params.

### Locations

- Lines ~1503-1516 (change event)
- Lines ~1542-1555 (input event)
- Lines ~1577-1590 (blur event)
- Lines ~1602-1615 (focus event)

### Current Pattern

```javascript
const params = {
    value: e.target.value,
    field: fieldName
};
let currentElement = e.target;
while (currentElement && currentElement !== document.body) {
    if (currentElement.dataset.componentId) {
        params.component_id = currentElement.dataset.componentId;
        break;
    }
    currentElement = currentElement.parentElement;
}
await handleEvent(inputHandler, params);
```

### Proposed Refactoring

```javascript
/**
 * Build standard form event params with component context.
 * @param {HTMLElement} element - Form element that triggered the event
 * @param {string} fieldName - Name of the form field
 * @param {any} value - Current value of the field
 * @returns {Object} - Params object with value, field, and optional component_id
 */
function buildFormEventParams(element, fieldName, value) {
    const params = { value, field: fieldName };
    const componentId = getComponentId(element);
    if (componentId) {
        params.component_id = componentId;
    }
    return params;
}
```

Usage:

```javascript
const params = buildFormEventParams(e.target, fieldName, e.target.value);
await handleEvent(inputHandler, params);
```

---

## 3. base.py - Duplicate Rust Instance Creation

### Problem

The logic for creating Rust component instances with framework parameter fallback is duplicated.

### Locations

- Lines ~118-136 (`__init__` method)
- Lines ~167-193 (`update` method)

### Current Pattern

```python
if self._rust_impl_class is not None:
    try:
        from djust.config import config
        framework = config.get("css_framework", "bootstrap5")
        try:
            self._rust_instance = self._rust_impl_class(**kwargs, framework=framework)
        except TypeError:
            self._rust_instance = self._rust_impl_class(**kwargs)
    except Exception:
        self._rust_instance = None
```

### Proposed Refactoring

```python
def _create_rust_instance(self, **props) -> None:
    """
    Create a Rust instance with fallback for missing framework parameter.

    Attempts to create a Rust component instance with the configured CSS
    framework. Falls back to creation without framework if the Rust
    component doesn't accept that parameter.
    """
    if self._rust_impl_class is None:
        return

    try:
        from djust.config import config
        framework = config.get("css_framework", "bootstrap5")
        try:
            self._rust_instance = self._rust_impl_class(**props, framework=framework)
        except TypeError:
            # Rust component doesn't accept framework parameter
            self._rust_instance = self._rust_impl_class(**props)
    except Exception:
        # Fall back to Python/hybrid implementation
        self._rust_instance = None
```

---

## 4. base.py - Duplicate Template Rendering Fallback

### Problem

Both `Component.render()` and `LiveComponent.render()` have similar Rust-to-Django template fallback logic.

### Locations

- `Component.render()` - Lines ~260-279
- `LiveComponent.render()` - Lines ~469-483

### Proposed Refactoring

```python
def _render_template_with_fallback(template_str: str, context: Dict[str, Any]) -> str:
    """
    Render template with Rust acceleration, falling back to Django templates.

    Args:
        template_str: Template string to render
        context: Context dictionary for template variables

    Returns:
        Rendered HTML string
    """
    try:
        from djust._rust import render_template
        return render_template(template_str, context)
    except (ImportError, AttributeError, RuntimeError):
        from django.template import Context, Template
        template = Template(template_str)
        django_context = Context(context)
        return template.render(django_context)
```

---

## 5. websocket.py - Duplicate Error Response Creation

### Problem

Error responses are created with similar structure in many places throughout the WebSocket consumer.

### Locations

Multiple locations throughout `websocket.py`:
- Lines ~149-154
- Lines ~159-164
- Lines ~192-197
- Lines ~208-214
- Lines ~226-231
- And many more...

### Current Pattern

```python
await self.send_json({
    "type": "error",
    "error": error_msg,
})
```

### Proposed Refactoring

```python
async def send_error(
    self,
    error: str,
    error_type: str = "default",
    **context
) -> None:
    """
    Send an error response to the client with consistent formatting.

    Args:
        error: Human-readable error message
        error_type: Category of error (validation, mount, event, etc.)
        **context: Additional context to include in the response
    """
    response = {
        "type": "error",
        "error": error,
        "error_type": error_type,
    }
    response.update(context)
    await self.send_json(response)
```

Usage:

```python
# Before
await self.send_json({"type": "error", "error": "Unknown message type"})

# After
await self.send_error("Unknown message type")

# With context
await self.send_error(
    "Validation failed",
    error_type="validation",
    expected_params=["id", "name"],
    provided_params=["id"],
)
```

---

## 6. websocket.py - Duplicate Patch/HTML Response Logic

### Problem

The logic for sending patch or full HTML updates is duplicated between actor mode and non-actor mode event handling.

### Locations

- Lines ~510-532 (actor mode)
- Lines ~730-788 (non-actor mode)

### Proposed Refactoring

```python
async def _send_update(
    self,
    patches: Optional[Any] = None,
    html: Optional[str] = None,
    version: int = 0,
    cache_request_id: Optional[str] = None,
    reset_form: bool = False,
) -> None:
    """
    Send patches or full HTML update to client with consistent response format.

    Args:
        patches: VDOM patches to apply (if available)
        html: Full HTML content (fallback when no patches)
        version: VDOM version for client sync
        cache_request_id: Optional cache request ID for client-side caching
        reset_form: Whether to reset form state after update
    """
    if patches:
        if self.use_binary:
            patches_data = msgpack.packb(
                patches if isinstance(patches, list) else json.loads(patches)
            )
            await self.send(bytes_data=patches_data)
        else:
            response = {
                "type": "patch",
                "patches": patches if isinstance(patches, list) else json.loads(patches),
                "version": version,
            }
            if cache_request_id:
                response["cache_request_id"] = cache_request_id
            await self.send_json(response)
    else:
        response = {
            "type": "html_update",
            "html": html,
            "version": version,
        }
        if reset_form:
            response["reset_form"] = True
        if cache_request_id:
            response["cache_request_id"] = cache_request_id
        await self.send_json(response)
```

---

## 7. decorators.py - Repeated Decorator Wrapper Pattern

### Problem

Multiple decorators (`@debounce`, `@throttle`, `@cache`, `@client_state`) follow an identical wrapper pattern that only adds metadata.

### Locations

- Lines ~293-310 (`debounce`)
- Lines ~343-361 (`throttle`)
- Lines ~428-444 (`cache`)
- Lines ~487-495 (`client_state`)

### Current Pattern

```python
def decorator(func: F) -> F:
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    _add_decorator_metadata(wrapper, "debounce", {...})
    return cast(F, wrapper)
```

### Proposed Refactoring

```python
def _make_metadata_decorator(key: str, value: Any) -> Callable[[F], F]:
    """
    Create a decorator that adds metadata without wrapping execution.

    Used by @debounce, @throttle, @cache, @client_state which only add
    metadata for client-side processing, not runtime behavior.

    Args:
        key: Metadata key to add to _djust_decorators
        value: Metadata value (typically a dict with config)

    Returns:
        Decorator function that adds metadata to the wrapped function
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        _add_decorator_metadata(wrapper, key, value)
        return cast(F, wrapper)

    return decorator
```

Simplified decorator implementations:

```python
def debounce(wait: float = 0.3, max_wait: Optional[float] = None) -> Callable[[F], F]:
    """Debounce event handler calls on the client side."""
    return _make_metadata_decorator("debounce", {
        "wait": wait,
        "max_wait": max_wait,
    })

def throttle(interval: float = 0.1, leading: bool = True, trailing: bool = True) -> Callable[[F], F]:
    """Throttle event handler calls on the client side."""
    return _make_metadata_decorator("throttle", {
        "interval": interval,
        "leading": leading,
        "trailing": trailing,
    })
```

---

## Implementation Notes

### Priority Order

1. **High Priority** (most impact, clearest benefit):
   - `websocket.py` error responses - Used everywhere, high duplication
   - `websocket.py` patch/HTML responses - Complex logic duplicated
   - `client.js` component ID lookup - 8 identical copies

2. **Medium Priority** (good improvement, moderate complexity):
   - `base.py` Rust instance creation - 2 locations, moderate complexity
   - `base.py` template rendering fallback - 2 locations
   - `client.js` form event params - 4 locations

3. **Lower Priority** (smaller impact):
   - `decorators.py` wrapper pattern - Works fine, mostly aesthetic

### Testing Strategy

When implementing these refactorings:

1. Write tests for the new helper functions first
2. Refactor one location at a time
3. Run full test suite after each change
4. Use `git bisect` capability to identify regressions

### Backward Compatibility

These are internal refactorings that should not affect the public API. However:

- Keep helper functions private (prefix with `_`) unless intentionally exposing
- Maintain identical behavior - these are pure refactorings
- Document any subtle behavior changes in commit messages
