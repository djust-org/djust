# List Reordering Performance: Keyed vs Unkeyed Lists

## The Problem

When you render a list in a djust template **without** `data-key` attributes, the VDOM diff algorithm matches children **by position**. If items reorder (sort, filter, remove from the middle), every shifted item generates a patch — even though the items themselves haven't changed.

### Example: Removing the First Item

**Template (unkeyed):**
```html
{% for task in tasks %}
  <li>{{ task.name }}</li>
{% endfor %}
```

With 100 items, removing the first one produces **99 SetText patches + 1 RemoveChild** — the diff sees every position has new text and rewrites them all.

**Template (keyed):**
```html
{% for task in tasks %}
  <li data-key="{{ task.id }}">{{ task.name }}</li>
{% endfor %}
```

The same operation now produces **1 RemoveChild** — the diff matches items by key and knows only one was removed.

## Performance Comparison

| Operation | Unkeyed (100 items) | Keyed (100 items) |
|-----------|---------------------|-------------------|
| Remove first | 99 SetText + 1 RemoveChild | 1 RemoveChild |
| Full reverse | 100 SetText | ~100 MoveChild (no text changes) |
| Append one | 1 InsertChild | 1 InsertChild |
| Change one in middle | 1 SetText | 1 SetText |

**Key takeaway:** Unkeyed and keyed lists perform identically for appends and single-item edits. The difference only matters when items **reorder, get removed from the middle, or get inserted at the beginning**.

## When to Use `data-key`

**Add `data-key` when:**
- Items can be sorted, filtered, or reordered by the user
- Items can be removed from anywhere in the list (not just the end)
- Items can be inserted at positions other than the end
- The list is large (>20 items) and updates frequently

**Skip `data-key` when:**
- The list only ever appends or replaces entirely
- Items never reorder (e.g., a static nav menu)
- The list is small (<10 items) — the overhead difference is negligible

## How to Add Keys

Use any **stable, unique** identifier as the key value:

```html
<!-- Database primary key (most common) -->
{% for product in products %}
  <div data-key="{{ product.id }}">{{ product.name }}</div>
{% endfor %}

<!-- UUID or slug -->
{% for page in pages %}
  <li data-key="{{ page.slug }}">{{ page.title }}</li>
{% endfor %}

<!-- Composite key for join tables -->
{% for membership in memberships %}
  <tr data-key="{{ membership.user_id }}-{{ membership.group_id }}">
    ...
  </tr>
{% endfor %}
```

**Do NOT use the loop index as a key** — `data-key="{{ forloop.counter }}"` is equivalent to unkeyed diffing and provides no benefit.

The `id` HTML attribute also works as a fallback if `data-key` is not present, but `data-key` is preferred because it doesn't affect CSS or JavaScript selectors.

## Debugging

Enable VDOM tracing to see which diff strategy is used and how many patches are generated:

```bash
DJUST_VDOM_TRACE=1 python manage.py runserver
```

Or in `settings.py`:
```python
LIVEVIEW_CONFIG = {'debug_vdom': True}
```

When an unkeyed list produces many patches from a reorder, the trace output will include:

```
[VDOM TRACE] PERFORMANCE WARNING: Unkeyed list with 100 children produced 99 patches.
This often means the list was reordered. Add `data-key` attributes to enable keyed
diffing and reduce patch count.
```

## Further Reading

- [VDOM Tracing Guide](../VDOM_TRACING.md) — Full tracing documentation
- [VDOM Torture Test Report](../VDOM_TORTURE_TEST_REPORT.md) — Benchmark data
- [VDOM Architecture Comparison](../VDOM_ARCHITECTURE_COMPARISON.md) — How djust's diff compares to other frameworks
