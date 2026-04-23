# Virtual lists (`dj-virtual`)

Render large lists (1000s of items) with only the visible window in the DOM. Uses absolute
positioning + translateY to maintain scroll semantics while reusing a small rendering window.

## Quick start

### Fixed-height items (simplest)

```html
<div dj-virtual dj-virtual-item-height="50">
    {% for item in items %}
        <div>{{ item }}</div>
    {% endfor %}
</div>
```

All items must render at the exact pixel height specified. Faster (no measurement needed) but
breaks silently if your CSS or content produces a different height.

### Variable-height items (opt-in)

```html
<div dj-virtual dj-virtual-variable-height dj-virtual-estimated-height="60">
    {% for item in items %}
        <div>{{ item.variable_content }}</div>
    {% endfor %}
</div>
```

A `ResizeObserver` measures each rendered item and caches its height. Unmeasured items (still
off-screen) use `dj-virtual-estimated-height` (default `50`) as a placeholder.

## Tuning `dj-virtual-estimated-height`

The estimated height affects:
- **Scrollbar stability**: if the estimate is much LOWER than actual average, the scrollbar
  jumps when items scroll into view and reveal their true (larger) height. The container's
  total-height estimate grows, pushing the scroll position.
- **Blank tail regions**: if the estimate is much HIGHER than actual, the virtualizer reserves
  more space than needed and you see blank area past the last item.

Rule of thumb: set estimated to the **average expected height** of your items. For chat bubbles
with variable text content, measuring a handful of representative items and averaging is enough.

## Interaction with item reorders

The current height cache is keyed by item index. If you reorder items (sort, insertion in the
middle), cached heights bind to the wrong items until re-measurement happens when each scrolls
back into view. For frequently-reordered lists, a `data-key`-based cache is planned (tracking
issue #951).

## When to use variable vs fixed

- **Fixed**: stable, known-height content. Tables with fixed row heights, avatars-only lists,
  CSS-grid-constrained items.
- **Variable**: user-generated or dynamic content. Chat messages, markdown-rendered posts,
  cards with variable internal layout.

## See also

- `dj-infinite-scroll` — pagination trigger on scroll-near-bottom
- `stream` / `stream_append` / `stream_prune` — for large append-only data where virtualization
  is overkill
