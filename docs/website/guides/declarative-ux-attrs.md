# Declarative UX Attributes

Three small declarative HTML attributes that replace custom `dj-hook` code every production djust app ends up writing:

- [`dj-mutation`](#dj-mutation) — fire a server event when DOM mutates
- [`dj-sticky-scroll`](#dj-sticky-scroll) — keep a container pinned to the bottom as content appends
- [`dj-track-static`](#dj-track-static) — warn / reload when JS or CSS assets change on a deploy

---

## `dj-mutation`

Fires a server event when the marked element's attributes or children change, via `MutationObserver`. Primary use case: bridging third-party JS libraries (charts, maps, rich-text editors) that mutate the DOM outside djust's control.

### Quick start

```html
<!-- Watch attribute changes on .class or .style -->
<div dj-mutation="handle_change" dj-mutation-attr="class,style"></div>

<!-- Watch child additions/removals -->
<div dj-mutation="on_children_update"></div>

<!-- Debounce bursts (default 150 ms) -->
<div dj-mutation="on_change" dj-mutation-attr="data-v" dj-mutation-debounce="300"></div>
```

```python
from djust import LiveView
from djust.decorators import event_handler

class ChartView(LiveView):
    @event_handler
    def handle_change(self, mutation: str = "", attrs: list = None,
                       added: int = 0, removed: int = 0, **kwargs):
        # mutation is "attributes" or "childList"
        # attrs is the list of changed attribute names (when mutation=="attributes")
        # added/removed is the child-count delta (when mutation=="childList")
        ...
```

### Dispatch path

1. A local cancelable `dj-mutation-fire` CustomEvent bubbles from the element.
2. If not `preventDefault()`ed, the payload is forwarded to `window.djust.handleEvent` — the standard djust event pipeline.

Application code can short-circuit the server call by listening and calling `preventDefault()`:

```js
document.addEventListener('dj-mutation-fire', (e) => {
    if (shouldSkip(e.detail.handler)) e.preventDefault();
});
```

### Caveats

- Don't list sensitive attributes (e.g. a password input's `value`) in `dj-mutation-attr`. The attribute name is included in the server payload — not the value, but still noisy for audit logs.
- Bursts of mutations are coalesced into a single server event via `dj-mutation-debounce` (milliseconds). Default 150 ms is a good balance for typical chart re-renders.

---

## `dj-sticky-scroll`

Keeps a scrollable container pinned to the bottom when new content appends, but backs off when the user scrolls up to read history. Resumes auto-scroll when they scroll back to bottom. The canonical chat / log-viewer UX — built in.

### Quick start

```html
<div dj-sticky-scroll style="overflow-y: auto; height: 400px">
    {% for msg in messages %}
        <div class="msg">{{ msg.text }}</div>
    {% endfor %}
</div>
```

That's it. Appending a new `<div class="msg">` scrolls the container to the bottom. User scrolling up disables auto-scroll. User returning to the bottom re-enables it.

### How it works

- A 1 px sub-pixel tolerance decides "at bottom" (`scrollTop + clientHeight >= scrollHeight - 1`).
- `MutationObserver` with `childList: true, subtree: true` reacts to content changes.
- A passive `scroll` listener tracks whether the user has moved away from the bottom.
- All state lives on the element itself (`el._djStickyAtBottom`); no global coordinator.

---

## `dj-track-static`

Production-critical for zero-downtime deploys. Without it, clients on long-lived WebSocket connections silently run stale JavaScript after you ship new code.

Phoenix parity: this is `phx-track-static`, renamed for djust.

### Quick start

```django
{% load live_tags %}

<script {% djust_track_static %} src="{% static 'js/app.abc123.js' %}"></script>
<link {% djust_track_static %} rel="stylesheet" href="{% static 'css/app.def456.css' %}">
```

Equivalent to writing the attribute by hand:

```html
<script dj-track-static src="..."></script>
```

The `{% djust_track_static %}` tag is purely a discoverability convenience — template authors who don't know about the attribute find the tag via the djust template-tag library.

### Behavior

1. On page load, djust snapshots the `src` / `href` of every `[dj-track-static]` element.
2. On every WebSocket reconnect, it re-queries and compares against the snapshot.
3. If any URL changed, djust dispatches a `dj:stale-assets` CustomEvent on `document`:

```js
document.addEventListener('dj:stale-assets', (e) => {
    console.log('stale assets:', e.detail.changed);
    // e.detail.changed = ['/static/js/app.NEW.js', ...]
    showUpdatePrompt();
});
```

### Auto-reload

To skip the CustomEvent and reload the page directly when that specific asset changes, use `dj-track-static="reload"`:

```html
<script dj-track-static="reload" src="{% static 'js/app.abc.js' %}"></script>
```

Any one `[dj-track-static="reload"]` element going stale triggers `window.location.reload()` on the next reconnect.

### Caveats

- The snapshot is taken once at page load. If an asset is removed from the DOM by a VDOM morph, it's treated as unchanged (we can't distinguish "removed" from "replaced"). Low-impact in practice because `[dj-track-static]` elements live in `<head>` and rarely get morphed.
- The `djust:ws-reconnected` CustomEvent (dispatched by `03-websocket.js` on every reconnect) is the trigger. Application code can listen for that event too if you want custom reconnect behavior — it's a public contract.

---

## See also

- [Hooks](hooks.md) — the `dj-hook` primitive these attributes replace
- [JS commands](js-commands.md) — another declarative attribute layer
- [Large lists](large-lists.md) — virtual-list + infinite-scroll often pair with `dj-sticky-scroll`
