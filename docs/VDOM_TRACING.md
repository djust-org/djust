# VDOM Tracing Guide

This document explains how to enable and use VDOM tracing to debug diffing issues in djust.

## Quick Start

**Option 1: Django configuration (recommended)**

In your `settings.py`:

```python
LIVEVIEW_CONFIG = {
    'debug_vdom': True,
}
```

This automatically sets `DJUST_VDOM_TRACE=1` for the Rust VDOM layer.

**Option 2: Environment variable**

```bash
export DJUST_VDOM_TRACE=1
```

Then run your tests or application. Tracing output will be written to stderr.

## What Gets Traced

When `DJUST_VDOM_TRACE=1` is set, the following information is logged:

### 1. Diff Entry Point
```
[VDOM TRACE] ===== DIFF START =====
[VDOM TRACE] old_root: <div> id=Some("0") children=2
[VDOM TRACE] new_root: <div> id=Some("1H") children=2
```

### 2. Child Diffing Details
```
[VDOM TRACE] diff_children: path=[1, 0, 1] parent_id=Some("2A") old_children=4 new_children=4 has_keys=false
[VDOM TRACE] diff_indexed_children: old_len=4 new_len=4 common=4
```

### 4. Keyed Diffing
```
[VDOM TRACE] diff_keyed_children: old_keys=["msg-1", "msg-2"] new_keys=["msg-1", "msg-2", "msg-3"]
[VDOM TRACE]   INSERT key=msg-3 at new_idx=2
[VDOM TRACE]   DIFF unkeyed by relative position: old_idx=0 <-> new_idx=0
```

### 5. Mixed Keyed/Unkeyed Warning
```
[VDOM TRACE] WARNING: Mixed keyed and unkeyed children detected at path=[1, 0]. Consider adding key= to all children for predictable updates.
```

This warning appears when a parent element has both keyed and unkeyed children, which can lead to unexpected diffing behavior. Adding `key` attributes to all sibling elements is recommended.

### 6. Patch Generation
```
[VDOM TRACE]     InsertChild index=1 tag=<div> parent_id=Some("2B")
[VDOM TRACE]     RemoveChild index=2 parent_id=Some("2B")
[VDOM TRACE] ===== DIFF COMPLETE: 3 patches generated =====
```

## Using in Tests

```python
import os

@pytest.mark.django_db
def test_my_vdom_issue():
    os.environ['DJUST_VDOM_TRACE'] = '1'  # Enable tracing

    # Your test code here
    view.render_with_diff(request)

    # Check stderr output for trace
```

## Using in Development

```bash
# Run Django dev server with tracing
DJUST_VDOM_TRACE=1 python manage.py runserver

# Run pytest with tracing
DJUST_VDOM_TRACE=1 pytest path/to/test.py -v -s
```

## Common Debugging Scenarios

### 1. Patches are empty when they shouldn't be

**Symptom**: HTML changes but `patches=[]`

**Steps**:
1. Enable tracing
2. Look for the element that should change (e.g., "model-config")
3. Check if old and new child counts differ
4. If counts differ but no InsertChild/RemoveChild, check if parent uses keyed diffing (`has_keys=true`)
5. Keyed diffing matches unkeyed children by relative position among unkeyed siblings

### 2. Wrong elements being patched

**Symptom**: Patches target wrong DOM elements

**Steps**:
1. Enable tracing
2. Check the `path` values in trace output
3. Verify paths match expected DOM structure
4. Check `djust_id` (data-dj-id) values match between old/new VDOM

### 3. Keyed vs Indexed diffing issues

**Symptom**: List updates not working correctly

**Steps**:
1. Check trace for `has_keys=true/false`
2. If `has_keys=true`, verify all list items have `key` attribute
3. Check `old_keys` and `new_keys` in trace output
4. Unkeyed children in keyed parent are diffed by relative position (not absolute index) to avoid mismatches when keyed children shift positions

## Understanding Paths

Paths are arrays of indices representing the location in the DOM tree:

- `[]` - Root element
- `[0]` - First child of root
- `[1, 0, 1, 3]` - Second child of root → first child → second child → fourth child

Example structure:
```
<div>                     <!-- path=[] -->
  <aside>                 <!-- path=[0] -->
    ...
  </aside>
  <main>                  <!-- path=[1] -->
    <header>              <!-- path=[1, 0] -->
      <div class="controls">  <!-- path=[1, 0, 1] -->
        ...
        <div class="model-config">  <!-- path=[1, 0, 1, 3] -->
```

## Understanding IDs

- `djust_id` (displayed as `data-dj-id` in HTML) is assigned during parsing
- IDs are hex-encoded integers (0, 1, 2, ..., a, b, c, ...)
- First render uses `parse_html()` which resets counter to 0
- Subsequent renders use `parse_html_continue()` to avoid ID collisions
- Patches use the OLD node's ID for targeting (that's what exists in client DOM)

## Trace Output Location

Trace output goes to **stderr**, not stdout. To capture it:

```bash
# Redirect stderr to file
DJUST_VDOM_TRACE=1 pytest test.py 2> trace.log

# View both stdout and stderr
DJUST_VDOM_TRACE=1 pytest test.py -s 2>&1 | less

# Filter for specific patterns
DJUST_VDOM_TRACE=1 pytest test.py -s 2>&1 | grep "model-config"
```

## Performance Note

Tracing adds overhead. Only enable it during debugging, not in production.

```python
# In production
assert os.environ.get('DJUST_VDOM_TRACE') is None, "VDOM tracing should be disabled in production"
```

## Adding Custom Trace Points

To add custom tracing in Rust code:

```rust
// In crates/djust_vdom/src/diff.rs
vdom_trace!("Custom message: value={}", some_value);

```

The `vdom_trace!` macro only outputs when `DJUST_VDOM_TRACE` is set (or when `debug_vdom: True` is configured in Django's `LIVEVIEW_CONFIG`).
