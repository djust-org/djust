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
| Stream `limit=N` | Cap DOM growth for append-only feeds | Not yet capped client-side (known issue #2964) |

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
- **A height mode — pick one:**
  - **`dj-virtual-item-height="<px>"`** — fixed pixel height per row; every item must render at this height.
  - **`dj-virtual-variable-height`** — variable heights, measured with `ResizeObserver`.
- The container must have a **fixed CSS height** and **`overflow: auto`**.

Optional:

- **`dj-virtual-overscan="<N>"`** — extra rows rendered above/below the viewport. Default `3`. Set higher (e.g. `10`) for smoother scroll on slow devices; lower to save DOM.
- **`dj-virtual-estimated-height="<px>"`** — variable mode only: baseline height for items not yet measured. Default `50`.
- **`dj-virtual-key-attr="<attr>"`** — variable mode only: the item attribute used as the height-cache key, so cached heights follow their item when the list reorders. Default `data-key`; items without it fall back to their index.

### How it works

1. On mount, djust snapshots the pre-rendered children as the **item pool**.
2. An inner **shell** is injected, positioned with `transform: translateY(start * itemHeight)`. A sibling **spacer** sets `height = total * itemHeight` so the native scrollbar length is correct.
3. On `scroll` (RAF-batched — one update per frame, 60fps-aligned), djust computes `visibleStart`/`visibleEnd` and re-attaches the slice into the shell. Element identity is preserved, so `dj-hook` mounts stay stable across scrolls.
4. VDOM morphs that re-render the container call `djust.refreshVirtualList(container)` via `reinitAfterDOMUpdate`.

### Layout contract

`dj-virtual` sets its own CSS on the injected wrapper elements — you should not need to add any:

- The **shell** is `position: absolute; top/left/right: 0` — taken fully out of flow so **only the spacer** contributes to `container.scrollHeight`. (A `position: relative` shell double-counts its own rendered rows against the spacer and leaves dead space past the last item.)
- The **spacer** is `flex-shrink: 0` so its explicit `height` is honored even when the container is a `display: flex` item — a flex item's default `flex-shrink: 1` otherwise crushes it to `offsetHeight: 0` and the list silently never scrolls.

Because the shell is absolutely positioned, the container is made a positioned ancestor (`position: relative` if it was `static`). One host-page caveat: if your container is itself a flex item relying on `align-items: stretch` for its cross-axis size, note that after this change the only remaining in-flow child is the 1px-wide spacer — give the container an explicit width/height (or `flex-shrink: 0` / `min-height: 0`) rather than relying on stretch from its virtualized content.

### Server-driven re-renders & live data

`dj-virtual` is **self-healing** across server-driven re-renders (djust ≥ 1.1.0-5). The server always renders the full `{% for %}` list (it has no notion of client-side virtualization), so a re-render can replace the container's children back to the raw list, or append a new row outside the shell. djust now reconciles both automatically after every VDOM morph:

- **Full re-render** (the container's children reverted to the raw list): the managed shell/spacer are detected as clobbered and the container is transparently re-virtualized against the fresh children — no manual `teardownVirtualList` + re-init needed.
- **Appended row** (e.g. a new chat message landing outside the wrapper): the loose element is absorbed into the item pool (at the tail) so it renders inside the shell and receives subsequent patches, instead of leaking as a stray sibling.

Scope note: the client-side *absorb* fallback is **append-only** (a loose row lands at the tail — correct for chat/feeds). Keyed mid-list inserts, removals and reorders no longer rely on it: since 1.1.0 the differ is `dj-virtual`-aware and emits keyed splice ops, so a server-side insert at position 5 lands at position 5 rather than the tail (`LIVEVIEW_CONFIG['virtual_keyed_ops']`, default **on**; set it to `False` to opt out). Finalize-patch landing for an item scrolled OUT of the current window is still open. For explicit control you can still set `container.__djVirtualItems` to an array of `HTMLElement` before `refreshVirtualList` to replace the pool wholesale.

### Limitations

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
    <div id="messages-{{ msg.id }}">{{ msg.content }}</div>
  {% endfor %}
</div>
```

Attributes:

- **`dj-viewport-top="event_name"`** — fire `event_name` once when the first child intersects the viewport.
- **`dj-viewport-bottom="event_name"`** — same for the last child.
- **`dj-viewport-threshold="0.1"`** — IntersectionObserver threshold, 0 – 1. Default `0.1` (10% visible).

### Firing semantics

- **Once per entry.** After fire, the sentinel child gets `data-dj-viewport-fired="true"` so scroll oscillation won't re-fire.
- **Re-arm** by calling `djust.resetViewport(container)` from a hook, or — more idiomatically — by **replacing the sentinel child** (for example when a re-render inserts new items at that edge).

### Event format

```js
container.addEventListener('dj-viewport', (e) => {
    console.log(e.detail); // { event: "load_older", edge: "top", target: <container> }
});
```

The named event is also sent to the server via `window.djust.handleEvent(event, { edge })`, over whichever transport is active (WebSocket, SSE or HTTP).

## Stream `limit` — Cap DOM growth

Bidirectional infinite scroll is only useful if the DOM doesn't grow unbounded. The server-side `stream()` method takes a `limit=N` kwarg intended to prune the stream after inserts.

> **Known issue: #2964.** At 1.2.0rc10, `limit=` only trims the batch being inserted, server-side. The `stream_prune` op it queues (and the one `stream_prune()` queues) is never delivered to the browser, so DOM growth is **not** capped. The rules below describe the intended behaviour.

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

Once #2964 is fixed, the client will apply `stream_prune` ops by removing surplus element children from the specified edge.

## Composing the two

A chat app typically uses all three on one container:

```html
<div dj-stream="messages"
     dj-virtual="messages"
     dj-virtual-item-height="64"
     dj-viewport-top="load_older"
     style="height: 600px; overflow: auto;">
  {% for msg in streams.messages %}
    <div id="messages-{{ msg.id }}" class="msg">…</div>
  {% endfor %}
</div>
```

- `dj-virtual` keeps the DOM at ~15 children even with 500 messages in memory.
- `dj-viewport-top` fires `load_older` when the user scrolls to the beginning.
- Server responds with `self.stream("messages", older, at=0, limit=500)`, and `dj-virtual` re-renders automatically after the morph. The prune that should keep the pool bounded does not reach the browser yet (known issue #2964).

## Performance notes

- **RAF batching** — the scroll handler runs at most once per frame. A rapid fling will coalesce into ~60 repaints per second, not hundreds.
- **IntersectionObserver** does not poll — it uses browser layout events and is essentially free.
- **DOM identity** is preserved across scrolls for elements in the pool, so `dj-hook` mounts, attached event listeners, and `dj-model` bindings survive virtualization.
