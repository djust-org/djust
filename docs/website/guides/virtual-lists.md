# Virtual lists (`dj-virtual`)

Render large lists (1000s of items) with only the visible window in the DOM. Uses absolute
positioning + translateY to maintain scroll semantics while reusing a small rendering window.

## Quick start

### Fixed-height items (simplest)

```html
<div dj-virtual="items" dj-virtual-item-height="50" style="height: 600px; overflow: auto">
    {% for item in items %}
        <div data-key="{{ item.id }}">{{ item }}</div>
    {% endfor %}
</div>
```

The container **must** have a fixed height and `overflow: auto`. Without them it never
scrolls, so nothing is windowed.

All items must render at the exact pixel height specified. Faster (no measurement needed) but
breaks silently if your CSS or content produces a different height.

### Variable-height items (opt-in)

```html
<div dj-virtual="items" dj-virtual-variable-height dj-virtual-estimated-height="60"
     style="height: 600px; overflow: auto">
    {% for item in items %}
        <div data-key="{{ item.id }}">{{ item.variable_content }}</div>
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

In variable-height mode, heights are cached by each item's `data-key` attribute (override the
attribute name with `dj-virtual-key-attr`), so cached heights stay with their items when the
list is reordered. Items without the attribute fall back to index keys, and their cached heights
bind to the wrong items after a reorder until each is re-measured on scrolling back into view.

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

The absorb fallback is **append-only** — a loose row lands at the end. Keyed mid-list
inserts/removals/reorders no longer depend on it: since 1.1.0 the differ is `dj-virtual`-aware
and emits keyed splice ops, so a mid-list insert lands at its keyed position
(`LIVEVIEW_CONFIG['virtual_keyed_ops']`, default **on**; set `False` to opt out).
Finalize-patch landing for an item scrolled out of the current window is still open. For explicit control, set
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
- `stream` / `stream_insert` / `stream_prune` — for large append-only data where virtualization
  is overkill
