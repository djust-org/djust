# VDOM Patching Root Cause Analysis

## Test Results

Created test: `python/tests/test_vdom_patching_wrapper.py`

Running `test_vdom_patching_generates_patches` reveals:

### Debug Output
```
[LiveView] render_with_diff() called
[LiveView] _rust_view before init: <builtins.RustLiveView object at 0x1082ddc30>
[LiveView] _rust_view after init: <builtins.RustLiveView object at 0x1082ddc30>
[LiveView] Rust returned: version=1, patches=NO
[LiveView] NO PATCHES GENERATED!
```

### Test Failure
```python
AssertionError: Response should contain patches, not just HTML
assert 'patches' in {'html': '...', 'version': 1}
```

## Root Cause

The RustLiveView object IS being reused correctly (same instance), BUT the Rust side returns **no patches** with `version=1`.

### Why No Patches?

**GET Request Flow:**
```python
def get(self, request, *args, **kwargs):
    self._initialize_rust_view(request)  # Creates/retrieves RustLiveView
    self._sync_state_to_rust()           # Syncs initial state
    liveview_content = self.render(request)  # Calls _rust_view.render()
    # Returns HTML to browser
```

`render()` calls `_rust_view.render()` which:
- Renders HTML from current state
- **Does NOT store VDOM for future diffing**

**POST Request Flow:**
```python
def post(self, request, *args, **kwargs):
    self.mount(request)                  # Reset/recreate components
    # ... handle event, update state ...
    html, patches_json, version = self.render_with_diff(request)
    # Calls _rust_view.render_with_diff()
```

`render_with_diff()` calls `_rust_view.render_with_diff()` which:
- Renders HTML from new state
- Tries to diff against previous VDOM
- **BUT there is no previous VDOM** (because GET used `render()`, not `render_with_diff()`)
- Returns `version=1` (first diff attempt) with `patches=None`

## The Problem

**Two Different Rust Methods:**
1. `render()` - Just renders HTML, doesn't store VDOM
2. `render_with_diff()` - Renders HTML, diffs against stored VDOM, returns patches

**Current Code:**
- GET uses `render()` → no VDOM stored
- POST uses `render_with_diff()` → no previous VDOM to diff against → no patches!

## Solution Options

### Option 1: Always Use `render_with_diff()` (RECOMMENDED)
Use `render_with_diff()` for BOTH GET and POST requests. On first call (GET), it returns no patches. On subsequent calls (POST), it returns patches.

**Changes needed:**
```python
def get(self, request, *args, **kwargs):
    # ...
    self._initialize_rust_view(request)
    self._sync_state_to_rust()
    html, patches_json, version = self.render_with_diff(request)  # Changed
    # Ignore patches_json on GET (will be None for first render)
    liveview_content = html
    # ...
```

This establishes the baseline VDOM during GET, enabling patches on POST.

### Option 2: Add Rust Method to Store VDOM
Add a Rust method `store_vdom()` that stores the current VDOM without returning patches:

```python
def get(self, request, *args, **kwargs):
    # ...
    html = self.render(request)
    self._rust_view.store_vdom()  # New method to store VDOM for future diffs
    # ...
```

But this requires Rust changes.

### Option 3: Call `render_with_diff()` Twice
Call `render_with_diff()` once during GET to establish baseline, ignore the result:

```python
def get(self, request, *args, **kwargs):
    # ...
    html = self.render(request)
    # Establish baseline for future patches
    self._rust_view.render_with_diff()  # Ignore return value
    # ...
```

But this is wasteful (renders twice).

## Recommended Fix

**Option 1 is cleanest**: Change `get()` to use `render_with_diff()` and ignore patches_json:

```python
def get(self, request, *args, **kwargs):
    """Handle GET requests - initial page load"""
    self.mount(request, **kwargs)

    # ... session setup ...

    # Initialize and render with diff (establishes baseline VDOM)
    self._initialize_rust_view(request)
    self._sync_state_to_rust()
    html, _, _ = self.render_with_diff(request)  # Ignore patches_json, version

    # Wrap in Django template if needed
    if hasattr(self, 'wrapper_template') and self.wrapper_template:
        # ... wrapper logic ...
        liveview_content = html
    else:
        liveview_content = html

    # ... rest of GET handler ...
```

This ensures:
- ✅ First `render_with_diff()` during GET stores the VDOM
- ✅ Second `render_with_diff()` during POST can diff against it
- ✅ No extra Rust methods needed
- ✅ No wasteful double-rendering

## Implementation Status: FIXED ✅

**Date Fixed:** November 8, 2025

**Changes Made:**
1. Modified `python/djust/live_view.py:293-298` to use `render_with_diff()` in GET handler
2. Created comprehensive test suite in `python/tests/test_vdom_patching_wrapper.py`

**Test Results:**
```
[TEST] POST Response keys: dict_keys(['patches', 'html', 'version', 'reset_on_fallback'])
[TEST] Has patches? True
[TEST] Version: 2
[TEST] Patches: [{"type":"SetText","path":[0,1,1,0],"text":"1"}]

✅ All 3 tests pass:
  - test_vdom_patching_generates_patches
  - test_vdom_patching_multiple_updates
  - test_vdom_root_alignment
```

**Verification:**
- ✅ GET request (version=1): Establishes baseline VDOM, returns HTML only
- ✅ POST request (version=2): Generates patches against baseline VDOM
- ✅ Patches correctly update DOM (e.g., counter increment from 0 to 1)
- ✅ Multiple consecutive updates work correctly
- ✅ Server running successfully at http://localhost:8002

**Root Cause Confirmed:**
GET handler was using `render()` which doesn't store VDOM, so POST's `render_with_diff()` had no baseline to compare against. Changing GET to also use `render_with_diff()` fixed the issue completely.
