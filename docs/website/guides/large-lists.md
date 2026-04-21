---
title: Large Lists — Virtual Scrolling & Infinite Feed
description: Render 100K-row tables at 60fps and wire bidirectional infinite scroll with dj-virtual, dj-viewport-top, dj-viewport-bottom, and stream `limit`.
---

# Large Lists

djust ships two complementary primitives for data-heavy UI:

| Attribute | Purpose | DOM cost |
|-----------|---------|----------|
| `dj-virtual` | Windowed rendering — only the visible slice is in the DOM | Fixed: ~visible-plus-overscan items |
| `dj-viewport-top` / `dj-viewport-bottom` | Fire a server event when the first/last child scrolls into view | IntersectionObserver (no polling) |
| Stream `limit=N` | Cap DOM growth for append-only feeds | Prunes from the opposite edge automatically |

Use `dj-virtual` when the server knows the full list (or a large slice) and you need steady 60fps scroll on 1K-100K rows. Use `dj-viewport-*` + stream `limit` for chat, log viewers, and activity feeds that load data on-demand.

## `dj-virtual` — Windowed lists

```html
<div dj-virtual="rows"
     dj-virtual-item-height="48"
     dj-virtual-overscan="5"
     style="height: 600px; overflow: auto;">
  {% for row in rows %}
    <div id="row-{{ row.id }}" class="row">{{ row.label }}</div>
  {% endfor %}
</div>
```

Required attributes:

- **`dj-virtual="<var_name>"`** — marker; the value is informational (kept for parity with Phoenix conventions).
- **`dj-virtual-item-height="<px>"`** — fixed pixel height per row. Required — every item must render at this height.
- The container must have a **fixed CSS height** and **`overflow: auto`**.

Optional:

- **`dj-virtual-overscan="<N>"`** — extra rows rendered above/below the viewport. Default `3`. Set higher (e.g. `10`) for smoother scroll on slow devices; lower to save DOM.

### How it works

1. On mount, djust snapshots the pre-rendered children as the **item pool**.
2. An inner **shell** is injected, positioned with `transform: translateY(start * itemHeight)`. A sibling **spacer** sets `height = total * itemHeight` so the native scrollbar length is correct.
3. On `scroll` (RAF-batched — one update per frame, 60fps-aligned), djust computes `visibleStart`/`visibleEnd` and re-attaches the slice into the shell. Element identity is preserved, so `dj-hook` mounts stay stable across scrolls.
4. VDOM morphs that re-render the container call `djust.refreshVirtualList(container)` via `reinitAfterDOMUpdate`.

### Limitations (v0.5.0)

- **Fixed height only.** Variable-height items (text wrapping, collapsible rows) are planned for v0.5.1 via `ResizeObserver`. For now, set `white-space: nowrap; overflow: hidden; text-overflow: ellipsis;` on cells to force a single line.
- **No horizontal virtualization** — columns render fully. Keep column count modest.
- **Keyboard navigation** across the virtual boundary is application-controlled; plumb `scrollIntoView()` calls on focus if you need tab-through-row behavior.

### JS API

| Function | Purpose |
|----------|---------|
| `djust.initVirtualLists(root)` | Scan `root` for `[dj-virtual]` containers and set up observers. Called automatically at mount and after VDOM patches. |
| `djust.refreshVirtualList(container)` | Force a repaint. If `container.__djVirtualItems` is set to an array of `HTMLElement`, replaces the item pool. |
| `djust.teardownVirtualList(container)` | Disconnect observers (test helper). |

## `dj-viewport-top` / `dj-viewport-bottom` — Infinite scroll

Phoenix 1.0 parity. Fire a server event when the first or last child of a stream container enters the viewport:

```html
<div dj-stream="messages"
     dj-viewport-top="load_older"
     dj-viewport-bottom="load_newer"
     dj-viewport-threshold="0.1">
  {% for msg in streams.messages %}
    <div id="msg-{{ msg.id }}">{{ msg.content }}</div>
  {% endfor %}
</div>
```

Attributes:

- **`dj-viewport-top="event_name"`** — fire `event_name` once when the first child intersects the viewport.
- **`dj-viewport-bottom="event_name"`** — same for the last child.
- **`dj-viewport-threshold="0.1"`** — IntersectionObserver threshold, 0 – 1. Default `0.1` (10% visible).

### Firing semantics

- **Once per entry.** After fire, the sentinel child gets `data-dj-viewport-fired="true"` so scroll oscillation won't re-fire.
- **Re-arm** by calling `djust.resetViewport(container)` from a hook, or — more idiomatically — by **replacing the sentinel child** (which is what normal `stream_insert` / `stream_prune` ops already do).

### Event format

```js
container.addEventListener('dj-viewport', (e) => {
    console.log(e.detail); // { event: "load_older", edge: "top", target: <container> }
});
```

If `window.djust.pushEvent` is wired (WebSocket connected), the named event is also pushed to the server with `{ edge }` params.

## Stream `limit` — Cap DOM growth

Bidirectional infinite scroll is only useful if the DOM doesn't grow unbounded. The server-side `stream()` method takes a `limit=N` kwarg that emits a `stream_prune` op after inserts:

```python
from djust import LiveView
from djust.decorators import event_handler

class ChatView(LiveView):
    template_name = "chat.html"

    def mount(self, request, **kwargs):
        self.stream("messages", Message.recent(50), limit=50)

    @event_handler
    def load_older(self, **kwargs):
        older = Message.before(self.oldest_id, 50)
        self.stream("messages", older, at=0, limit=50)  # prepends; prunes bottom

    @event_handler
    def load_newer(self, **kwargs):
        newer = Message.after(self.newest_id, 50)
        self.stream("messages", newer, limit=50)  # appends; prunes top
```

Rules:

- **`at=-1`** (default — append) + `limit=N` → prunes from the **top**.
- **`at=0`** (prepend) + `limit=N` → prunes from the **bottom**.
- Explicit control via `self.stream_prune(name, limit=N, edge="top")` / `edge="bottom"`.

The client applies `stream_prune` ops by removing surplus element children from the specified edge.

## Composing the two

A chat app typically uses all three on one container:

```html
<div dj-stream="messages"
     dj-virtual="messages"
     dj-virtual-item-height="64"
     dj-viewport-top="load_older"
     style="height: 600px; overflow: auto;">
  {% for msg in streams.messages %}
    <div id="msg-{{ msg.id }}" class="msg">…</div>
  {% endfor %}
</div>
```

- `dj-virtual` keeps the DOM at ~15 children even with 500 messages in memory.
- `dj-viewport-top` fires `load_older` when the user scrolls to the beginning.
- Server responds with `self.stream("messages", older, at=0, limit=500)` — the prepend + prune keeps the pool bounded, and `dj-virtual` re-renders automatically via the normal stream op pipeline.

## Performance notes

- **RAF batching** — the scroll handler runs at most once per frame. A rapid fling will coalesce into ~60 repaints per second, not hundreds.
- **IntersectionObserver** does not poll — it uses browser layout events and is essentially free.
- **DOM identity** is preserved across scrolls for elements in the pool, so `dj-hook` mounts, attached event listeners, and `dj-model` bindings survive virtualization.
- The client module adds ~7 KB combined (virtual list + infinite scroll, unminified) to `client.js`.
