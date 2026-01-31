# VDOM Torture Test Report

**Date**: 2026-01-31
**Test Files**:
- `crates/djust_vdom/tests/torture_test.rs` (42 tests)
- `python/tests/test_vdom_torture.py` (16 tests)

## Summary

**All 58 torture tests pass.** The VDOM diff algorithm is robust across all tested scenarios. No bugs were found.

## Test Coverage

### Rust Tests (42 tests)

| Category | Tests | Result |
|----------|-------|--------|
| Deep nesting (30-50 levels) | 3 | PASS |
| Wide sibling lists (100-500 items) | 5 | PASS |
| Keyed diffing edge cases | 6 | PASS |
| Replace mode stress | 3 | PASS |
| Diff-apply-verify correctness | 7 | PASS |
| Text node edge cases | 5 | PASS |
| Attribute thrashing | 4 | PASS |
| Real HTML parsing round-trips | 3 | PASS |
| Mixed scenarios | 4 | PASS |
| Sequential diffs (rapid updates) | 2 | PASS |

### Python Tests (16 tests)

| Category | Tests | Result |
|----------|-------|--------|
| Deep nesting (10 levels) | 1 | PASS |
| Large lists (50 items) | 3 | PASS |
| Conditional show/hide | 2 | PASS |
| Multiple simultaneous changes | 1 | PASS |
| Replace container with siblings | 1 | PASS |
| Form validation (5 fields) | 1 | PASS |
| Rapid counter (20 increments) | 1 | PASS |
| Empty state transitions | 2 | PASS |
| Unicode/special characters | 1 | PASS |
| No-change (zero patches) | 1 | PASS |
| HTML tables | 2 | PASS |

## Notable Findings

### 1. Duplicate Keys Don't Crash (but produce suboptimal results)

The algorithm uses `HashMap<String, usize>` for key-to-index mapping, so duplicate keys silently result in last-one-wins. The diff doesn't panic and produces structurally valid patches, but the semantics may be unexpected. **Not a bug** — users should ensure unique keys.

### 2. Indexed Diff is O(n) for First-Element Removal

Removing the first element from a 100-item unkeyed list produces 99 SetText patches + 1 RemoveChild (morphing every item). With keyed children, the same operation produces 1 RemoveChild + ~99 MoveChild patches with 0 text changes. This confirms keyed lists are important for lists where items move/reorder.

### 3. Full Reversal of 100 Keyed Items Works Correctly

Reversing a 100-item keyed list produces only MoveChild patches (no inserts/removes), confirming the keyed algorithm handles extreme reordering well.

### 4. Replace Mode Ordering is Correct

All RemoveChild patches always precede InsertChild patches, and RemoveChild indices are always in descending order. This matches the client-side requirement for safe batch application.

### 5. 500 Siblings: Single Middle Change = 1 Patch

Changing one text node in the middle of 500 siblings produces exactly 1 SetText patch, confirming O(1) minimal diff for localized changes.

### 6. dj-* Event Handlers Are Never Removed

Even with 10 dj-* attributes on an element that loses all of them in the new tree, zero RemoveAttr patches are generated for dj-* attributes. This preservation is correct and intentional.

### 7. No-Change Re-renders Produce Zero Patches

When state doesn't change, the diff correctly produces 0 meaningful patches (confirmed through the full Python LiveView pipeline).

## Potential Improvements (Not Bugs)

1. **Duplicate key detection**: Could add a debug warning when duplicate keys are detected in children, similar to the mixed keyed/unkeyed warning.

2. **Large unkeyed list reorder**: Users reordering unkeyed lists will get O(n) patches instead of O(moves). Documentation should recommend `data-key` for lists that reorder.

## How to Run

```bash
# Rust torture tests
cargo test -p djust_vdom --test torture_test

# Python torture tests
source .venv/bin/activate
pytest python/tests/test_vdom_torture.py -v

# All VDOM tests
cargo test -p djust_vdom
```
