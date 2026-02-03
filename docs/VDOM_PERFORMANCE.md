# VDOM Performance Guide

This document describes the performance characteristics of the djust VDOM diffing algorithm, particularly for large lists (>1000 items).

## Benchmark Results

All benchmarks run on Apple Silicon (M-series), release build.

### Large List Performance

| Operation | 1000 items | 5000 items | 10000 items |
|-----------|------------|------------|-------------|
| Identity (no changes) | 415 µs | 2.01 ms | 4.18 ms |
| Append one item | 414 µs | 2.01 ms | 4.10 ms |
| Prepend one (keyed) | 470 µs | 2.29 ms | 4.77 ms |
| Reverse (keyed) | 474 µs | 2.29 ms | - |
| All text changed | 298 µs | 1.52 ms | - |
| Empty → N items | 160 µs | 800 µs | - |
| N items → Empty | 33 µs | 160 µs | - |
| Mixed operations (10% each) | 495 µs | 2.38 ms | - |

### Key Observations

1. **Scaling is linear**: Performance scales O(n) with list size, which is expected for a tree diffing algorithm.

2. **Empty transitions are very fast**:
   - Removing all children (n→empty): ~33µs/1000 items
   - Adding all children (empty→n): ~160µs/1000 items
   - The algorithm already optimizes these cases by avoiding unnecessary comparisons.

3. **Keyed lists are efficient**: Reversing a keyed list takes the same time as an identity diff, as keys allow O(1) lookup for matching nodes.

4. **Text-only changes are fast**: When structure is unchanged, only text comparisons are needed (~298µs/1000 items).

## Performance Optimization Tips

### 1. Use Keys for Dynamic Lists

Always use `data-key` attributes on list items that may be reordered, added, or removed:

```html
{% for item in items %}
<li data-key="{{ item.id }}">{{ item.name }}</li>
{% endfor %}
```

Without keys, prepending an item to a 1000-item list would generate 999 text changes. With keys, it generates 1 insert.

### 2. Use `data-djust-replace` for Complete Content Swaps

When a container's content changes completely (e.g., switching conversations in a chat app), add `data-djust-replace` to skip per-child diffing:

```html
<div class="messages" data-djust-replace>
  {% for message in messages %}
  <div class="message">{{ message.text }}</div>
  {% endfor %}
</div>
```

This is faster when >50% of children change, as it avoids comparing old/new children that won't match.

### 3. Structure Updates for Large Lists

For lists >1000 items, consider these patterns:

**Appending**: Most efficient - only 1 InsertChild patch needed.

**Prepending**: With keys, generates 1 InsertChild + 1000 MoveChild patches. Consider appending + CSS `flex-direction: column-reverse` for chat-like UIs.

**Removing First Item**: With keys, generates 1 RemoveChild + 999 MoveChild patches. For FIFO patterns, consider removing from the end.

### 4. Batch Updates

When making multiple changes to a list, batch them in a single render cycle:

```python
# Bad: Multiple renders
for item in new_items:
    self.items.append(item)
    self.force_render()  # Generates patches for each item

# Good: Single render
self.items.extend(new_items)
self.force_render()  # Generates one batch of patches
```

### 5. Pagination for Very Large Lists

For lists >5000 items, consider:
- Virtual scrolling (only render visible items)
- Pagination with "load more" buttons
- Server-side rendering of visible portion

## Algorithm Details

### Keyed Diffing

When children have keys, the algorithm:
1. Builds a HashMap of key→index for both old and new children
2. Identifies nodes to remove (in old but not in new)
3. Identifies nodes to insert (in new but not in old)
4. Identifies nodes to move (same key, different index)
5. Recursively diffs matching nodes

Time complexity: O(n) for comparison, O(n log n) for sorting moves.

### Indexed Diffing

When children lack keys, the algorithm:
1. Compares children pairwise by index
2. Handles excess children (insert or remove)

This is O(n) but may generate suboptimal patches for reorders.

### Identity Detection

The algorithm detects identical nodes by comparing:
- Tag name
- Text content
- Attributes
- Children (recursively)

Pointer equality (`old == new`) is not used since parsed HTML always creates new nodes.

## Benchmarking

Run benchmarks locally:

```bash
cd crates/djust_vdom
cargo bench diff_large_lists
```

For custom scenarios, add benchmarks to `benches/diff.rs`.

## Related Documentation

- [VDOM Architecture Comparison](./VDOM_ARCHITECTURE_COMPARISON.md)
- [VDOM Torture Test Report](./VDOM_TORTURE_TEST_REPORT.md)
- [VDOM Tracing](./VDOM_TRACING.md) - Debug VDOM issues with `DJUST_VDOM_TRACE=1`
