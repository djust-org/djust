# Handler Exception Detection in LiveViewSmokeTest

## Problem

`LiveViewTestClient.send_event()` catches all handler exceptions and returns `{"success": False, "error": "..."}` instead of raising them. This means the fuzz test `test_fuzz_no_unhandled_crash()` only catches exceptions that **escape** `send_event()`, not those caught inside it.

Handler-level bugs pass silently because they're caught internally.

## Solution

Added a new test method `test_fuzz_handlers_succeed()` that checks the `success` field and `error` message in the response to detect handler exceptions.

## How It Works

### Before (test_fuzz_no_unhandled_crash)

```python
def test_fuzz_no_unhandled_crash(self):
    """Only catches exceptions that escape send_event()."""
    for view_class in views:
        for handler_name, handler_meta in handlers.items():
            for desc, fuzz_params in fuzz_params:
                try:
                    client = self._make_client(view_class)
                    client.send_event(handler_name, **fuzz_params)
                    # ✗ Handler exceptions caught by send_event() are MISSED
                except Exception as e:
                    # ✓ Only exceptions that escape are caught
                    crashes.append(...)
```

**Problem**: Handler exceptions are caught by `send_event()` on line 217-218:

```python
try:
    handler(**coerced_params)
except Exception as e:
    error = str(e)  # ← Exception caught here, returned as success=False
```

### After (test_fuzz_handlers_succeed)

```python
def test_fuzz_handlers_succeed(self):
    """Detects handler exceptions by inspecting the response."""
    for view_class in views:
        for handler_name, handler_meta in handlers.items():
            for desc, fuzz_params in fuzz_params:
                try:
                    client = self._make_client(view_class)
                    result = client.send_event(handler_name, **fuzz_params)

                    # ✓ Check if handler raised an exception
                    if not result["success"] and result.get("error"):
                        failures.append(f"{view_name}.{handler_name}: {result['error']}")

                except Exception:
                    # Exceptions that escape mount/send_event are caught by
                    # test_fuzz_no_unhandled_crash — we only care about handler
                    # errors here (success=False).
                    pass
```

**Solution**: Checks `result["success"]` and `result["error"]` to detect handler-level exceptions.

## Example

### Buggy Handler

```python
class SearchView(LiveView):
    def search(self, query: str = ""):
        results = query.split(",")  # ← Bug: crashes on None
        self.results = results
```

### Old Behavior (Missed)

```python
client = LiveViewTestClient(SearchView)
client.mount()

# Send None (type confusion fuzz payload)
result = client.send_event('search', query=None)

print(result)
# {'success': False, 'error': 'AttributeError: NoneType has no attribute split', ...}

# ✗ test_fuzz_no_unhandled_crash() doesn't detect this because
#    the exception didn't escape send_event()
```

### New Behavior (Detected)

```python
# ✓ test_fuzz_handlers_succeed() detects this by checking:
if not result['success'] and result.get('error'):
    # Handler exception detected!
    raise AssertionError(f"Handler failed: {result['error']}")
```

## When to Use Each Test

### test_fuzz_no_unhandled_crash

- Catches exceptions in **mount()**, **render()**, or **send_event()** itself
- Detects framework-level bugs (outside handler boundary)
- Example: Template loading errors, VDOM rendering crashes

### test_fuzz_handlers_succeed

- Catches exceptions **inside event handlers**
- Detects application-level bugs (validation, type errors, logic bugs)
- Example: Handler crashes on type confusion, missing validation

## Validation vs. Exception

The test distinguishes between graceful validation failures and handler exceptions:

```python
# ✓ Acceptable: Validation rejection (success=False, error=None)
result = client.send_event('update', value="not_an_int")
# {'success': False, 'error': None, ...}
# Validation rejected the input gracefully

# ✗ Bug: Handler exception (success=False, error="...")
result = client.send_event('update', value="not_an_int")
# {'success': False, 'error': 'ValueError: invalid literal for int()', ...}
# Handler raised an exception instead of validating
```

## Migration Guide

If you have custom smoke tests using `LiveViewSmokeTest`, no changes needed! The new test `test_fuzz_handlers_succeed()` is automatically included and runs alongside the existing tests.

To disable the new test:

```python
class TestMyViews(TestCase, LiveViewSmokeTest):
    fuzz = False  # Disables all fuzz tests
```

Or override the method to skip it:

```python
def test_fuzz_handlers_succeed(self):
    pass  # Skip this test
```

## Testing Recommendations

1. **Both tests are complementary** — keep both enabled
2. **Fix handler exceptions** detected by `test_fuzz_handlers_succeed()` by:
   - Adding type validation (use `@event_handler` decorator metadata)
   - Adding bounds checking (negative values, empty strings, etc.)
   - Handling edge cases gracefully (None, empty lists, etc.)

3. **Example fix**:

```python
# Before (buggy)
def increment(self, value: int = 1):
    self.count += value  # ← Crashes on negative values in some logic

# After (fixed)
@event_handler()
def increment(self, value: int = 1):
    if value < 0:
        return  # Gracefully ignore negative values
    self.count += value
```
