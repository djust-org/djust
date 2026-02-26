# djust VDOM Testing

This document describes the test suite for the VDOM diffing algorithm, with a focus on whitespace text node handling.

## Test Suite Overview

### Unit Tests (`src/diff.rs`)

The unit tests verify the core diffing logic without HTML parsing:

1. **test_diff_text_change** - Basic text node changes
2. **test_diff_attr_change** - Attribute modifications
3. **test_diff_children_insert** - Inserting new children
4. **test_diff_replace_tag** - Replacing entire nodes when tags differ

#### Whitespace-Specific Tests

These tests address the bug where Rust's VDOM includes whitespace text nodes that the browser filters out:

5. **test_diff_with_whitespace_text_nodes**
   - Simulates html5ever's behavior: elements interspersed with whitespace text nodes
   - Old VDOM: 8 children (elements at indices 0,2,4,6 and whitespace at 1,3,5,7)
   - New VDOM: 6 children (removed 2 elements and their adjacent whitespace)
   - Verifies RemoveChild patches target correct indices (6 and 7)

6. **test_form_validation_error_removal**
   - Real-world scenario: form field with conditional validation error div
   - Old: `<div><input is-invalid><whitespace><error-div><whitespace></div>`
   - New: `<div><input><whitespace><whitespace></div>`
   - Verifies:
     - SetAttr patch removes "is-invalid" class
     - RemoveChild patch at index 3 (accounting for whitespace at index 2)

7. **test_multiple_conditional_divs_removal**
   - Multiple form fields with errors all cleared at once
   - Tests that patches correctly handle bulk removals
   - Verifies 2 RemoveChild patches for 2 validation error divs

8. **test_path_traversal_with_whitespace**
   - Ensures patch paths correctly account for whitespace nodes
   - Structure: `div > [span, whitespace, span, whitespace, span]`
   - When middle span changes, patch should use path `[5, 2, 0]` not `[5, 1, 0]`
   - The `2` accounts for whitespace at index 1

### Integration Tests (`tests/integration_test.rs`)

Integration tests use the real html5ever parser to create VDOMs from HTML strings:

1. **test_form_validation_errors_with_real_html**
   - Full form with 2 fields, both have validation errors
   - After clearing: 2 RemoveChild patches + 2+ SetAttr patches
   - Verifies the complete flow: HTML → VDOM → diff → patches

2. **test_conditional_div_with_whitespace**
   - Django {% if %} style conditional rendering
   - Tests adding/removing "d-none" class for show/hide

3. **test_deeply_nested_form_structure**
   - Full nesting: container > row > col > card > card-body > form > field
   - Verifies RemoveChild patches have deep paths (> 5 levels)
   - Simulates the exact structure from our bug report

4. **test_whitespace_preserved_in_vdom**
   - Documents that html5ever preserves whitespace as text nodes
   - This is the root cause of our bug

5. **test_patch_indices_account_for_whitespace**
   - Ensures indices in patches account for whitespace
   - Tests text changes in elements separated by whitespace

6. **test_multiple_fields_with_errors_cleared**
   - 4 form fields, all with errors, all cleared
   - Verifies exactly 4 RemoveChild patches generated

## The Whitespace Bug

### Root Cause

The html5ever parser creates text nodes for whitespace between HTML elements:

```html
<form>
    <div>Field 1</div>
    <div>Field 2</div>
</form>
```

Creates this VDOM structure:
- Index 0: `<div>Field 1</div>`
- Index 1: Text node `"\n    "`
- Index 2: `<div>Field 2</div>`
- Index 3: Text node `"\n"`

However, when the browser renders this HTML via `innerHTML`, it normalizes whitespace differently, resulting in only indices 0 and 2.

### The Fix

**Client-Side Solution** (implemented in `live_view.py` lines 434-445):

Changed the JavaScript DOM traversal to keep ALL text nodes (including whitespace):

```javascript
const children = Array.from(node.childNodes).filter(child => {
    if (child.nodeType === Node.ELEMENT_NODE) return true;
    if (child.nodeType === Node.TEXT_NODE) {
        return true;  // Keep ALL text nodes, even whitespace
    }
    return false;
});
```

This makes the client-side traversal match Rust's VDOM structure.

**Alternative Solutions Considered:**

1. ❌ **Fix Rust parser to filter whitespace** - The filtering code exists but doesn't work as expected
2. ❌ **Normalize HTML on server** - Would require complex HTML rewriting
3. ✅ **Match client to Rust** - Simple, works with existing VDOM structure

## Running Tests

```bash
# Run all VDOM tests
cargo test --package djust_vdom

# Run only unit tests
cargo test --package djust_vdom --lib

# Run only integration tests
cargo test --package djust_vdom --test integration_test

# Run specific test
cargo test --package djust_vdom test_diff_with_whitespace_text_nodes
```

## Test Coverage

Current coverage:
- ✅ Whitespace text node handling
- ✅ Form validation error removal
- ✅ Deep path traversal
- ✅ Conditional rendering (show/hide with classes)
- ✅ Conditional placeholder handling (`<!--dj-if-->` for issue #295)
- ✅ Multiple simultaneous removals
- ✅ Real HTML parsing with html5ever
- ✅ Attribute changes
- ✅ Text content changes
- ✅ Child insertion/removal
- ✅ Node replacement

## Conditional Rendering Fix (Issue #295)

### Problem
When `{% if %}` blocks evaluated to false, they removed elements from the DOM without leaving a placeholder. This caused sibling positions to shift, making the VDOM diff incorrectly match siblings and generate wrong patches.

### Solution
When `{% if condition %}` is false and has no `{% else %}`, the template engine now emits a `<!--dj-if-->` placeholder comment. This maintains consistent sibling positions in the VDOM tree.

### How It Works
1. **Template engine** (`crates/djust_templates/src/renderer.rs`): Emits `<!--dj-if-->` when condition is false
2. **Parser** (`crates/djust_vdom/src/parser.rs`): Preserves `<!--dj-if-->` comments as special comment vnodes
3. **Diff** (`crates/djust_vdom/src/diff.rs`): Detects placeholder-to-content transitions and generates `RemoveChild` + `InsertChild` patches instead of `Replace` patches

### Tests
See tests in `crates/djust_templates/src/renderer.rs`:
- `test_if_false_emits_placeholder` - Placeholder emission
- `test_if_true_no_placeholder` - No placeholder when true
- `test_if_with_else_no_placeholder` - No placeholder when else branch exists
- `test_if_siblings_with_placeholder` - Sibling position maintenance

## Future Test Ideas

- [ ] Test with actual Django template output (using Jinja2-style conditionals)
- [ ] Benchmark performance with large DOMs (1000+ nodes)
- [ ] Test keyed diffing with form fields (data-key attributes)
- [ ] Test move operations (reordering list items)
- [ ] Fuzz testing with random HTML mutations
