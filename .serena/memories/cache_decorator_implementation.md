# Cache Decorator Implementation

## Overview
The `@cache` decorator provides client-side response caching with TTL (time-to-live) and LRU eviction.

## Location
- **Python decorator**: `python/djust/decorators.py:310-351`
- **Client-side logic**: `python/djust/live_view.py` (embedded JavaScript in `_inject_client_script`)
- **Handler metadata check**: Line 3033 in live_view.py

## How It Works

### Python Side
1. Decorator adds `_djust_decorators["cache"]` metadata to handler functions
2. Metadata includes: `ttl` (seconds) and `key_params` (list of param names)
3. Metadata extracted in `LiveView._extract_handler_metadata()` (line 496)
4. Metadata injected into client HTML in `LiveView._inject_handler_metadata()` (line 589)

### Client Side (JavaScript)
1. `handleEvent()` checks for `metadata?.cache` before sending request
2. `cacheEvent()` function checks cache for existing entry
3. Cache key format: `handlerName-param1-param2-...` (based on key_params)
4. Cache hit: Returns cached response instantly (no server round-trip)
5. Cache miss: Calls server, caches response with TTL

### Execution Flow with Other Decorators
```
User input → handleEvent()
  → Optimistic update (immediate)
  → Debounce/Throttle check → delay if needed
  → sendEventImmediate() with _skipTimingDecorators
  → handleEvent() again (skip timing decorators)
  → Cache check → cacheEvent()
    → Cache hit: return immediately
    → Cache miss: call server with _skipDecorators
```

## Usage Examples

### With key_params
```python
@cache(ttl=300, key_params=["query"])
def search(self, query: str = "", **kwargs):
    self.results = Product.objects.filter(name__icontains=query)
```
Cache key: `search-laptop` (includes query parameter)

### Without key_params
```python
@cache(ttl=60)
def get_stats(self, **kwargs):
    self.stats = expensive_calculation()
```
Cache key: `get_stats` (handler name only)

## Test Files
- Unit tests: `python/djust/tests/test_decorators.py`
- Integration test: `examples/demo_project/demo_app/views/cache_test.py`
- Demo view: `examples/demo_project/demo_app/views/cache_demo.py`

## Recent Bug Fixes (2025-11-14)
1. Added missing cache handler in `handleEvent()` - decorator was defined but never invoked
2. Fixed interaction with debounce/throttle - cache now runs after timing decorators but before network send
3. Introduced `_skipTimingDecorators` flag for granular decorator control

## Documentation
- Guide: `docs/TESTING_PAGES.md`
- API reference: `docs/STATE_MANAGEMENT_API.md`
- Examples: `docs/STATE_MANAGEMENT_EXAMPLES.md`
