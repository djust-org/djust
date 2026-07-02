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

## Live-changing data (server-driven re-renders)

`dj-virtual` works with a live, `{% for %}`-driven list that changes via normal server
re-renders — not just on first paint (djust ≥ 1.1.0-5). After every VDOM morph djust
reconciles the container automatically:

- If a re-render reverted the container to the raw server list, the shell/spacer are
  re-established transparently (no manual teardown + re-init).
- If a re-render appended a new row outside the wrapper (e.g. a streamed chat message), the
  row is absorbed into the virtual item pool at the tail so it renders inside the shell.

The absorb is **append-only** — a new row lands at the end. Keyed mid-list inserts/removals and
finalize-patch landing for an item scrolled out of the current window are deferred to
differ-level `dj-virtual` awareness (tracked as a follow-up). For explicit control, set
`container.__djVirtualItems` to an array of `HTMLElement` before `djust.refreshVirtualList(container)`
to replace the pool wholesale.

## Layout contract

The injected wrapper carries its own CSS — no host CSS is required. The **shell** is
`position: absolute; top/left/right: 0` (out of flow, so only the spacer defines scroll
height), and the **spacer** is `flex-shrink: 0` (so its height survives a `display: flex`
container). If your container is itself a stretch-sized flex item, give it an explicit
size rather than relying on `align-items: stretch` from its virtualized content — the only
in-flow child after virtualization is the 1px spacer.

## See also

- `dj-infinite-scroll` — pagination trigger on scroll-near-bottom
- `stream` / `stream_append` / `stream_prune` — for large append-only data where virtualization
  is overkill
