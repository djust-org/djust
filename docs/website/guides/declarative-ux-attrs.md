# Declarative UX Attributes

Small declarative HTML attributes that replace custom `dj-hook` code every production djust app ends up writing:

- [`dj-mutation`](#dj-mutation) — fire a server event when DOM mutates
- [`dj-sticky-scroll`](#dj-sticky-scroll) — keep a container pinned to the bottom as content appends
- [`dj-track-static`](#dj-track-static) — warn / reload when JS or CSS assets change on a deploy
- [`dj-transition`](#dj-transition) — declarative CSS enter/leave transitions
- [`dj-remove`](#dj-remove) — exit animations before element removal
- [`dj-transition-group`](#dj-transition-group) — enter/leave animations for every child of a list container

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

## `dj-transition`

Declarative CSS enter/leave transitions. Phoenix `JS.transition` parity. Runs a three-phase class application (start → active → end) so template authors can trigger CSS transitions without writing a `dj-hook`.

### Quick start

```html
<!-- Fades in from 0 to 100 opacity over 300 ms (Tailwind) -->
<div dj-transition="opacity-0 transition-opacity-300 opacity-100">
    Hello
</div>
```

The attribute value is **three space-separated class tokens**:

| Phase | Class | Timing |
|---|---|---|
| 1 (start) | first token | applied synchronously when the attribute appears |
| 2 (active) | second token | applied on the next animation frame (transition begins) |
| 3 (end) | third token | applied on the next animation frame (final state) |

On `transitionend` the phase-2 class is removed; phase-3 stays as the final-state class. A 600 ms fallback timeout cleans up phase-2 if `transitionend` never fires (e.g. `display: none` during the animation).

### Re-triggering from JS

Any change to the attribute value re-runs the sequence:

```js
el.setAttribute('dj-transition', 'scale-0 transition-transform-200 scale-100');
```

### Interop with existing CSS frameworks

Works with any class-based CSS framework — Tailwind (`transition-*` / `duration-*`), Bootstrap 5 (`fade` / `show`), or hand-rolled classes. The attribute only orchestrates the class application; it doesn't ship any CSS itself.

### Scope

This is phase 1 of the v0.6.0 Animations & transitions work. Separate follow-ups cover:
- ~~`dj-remove` — run an exit animation before element removal~~ ✅ — see below
- ~~`dj-transition-group` — animate children of a list container (React `<TransitionGroup>` / Vue `<transition-group>` equivalent)~~ ✅ — see below
- FLIP — animate list-item reordering
- Skeleton / shimmer loading-state components

---

## `dj-remove`

Declarative CSS exit transitions. Phoenix `JS.hide` / `phx-remove` parity. When a VDOM patch would physically remove an element carrying `dj-remove="..."`, djust delays the removal until the CSS transition the attribute describes has completed.

### Quick start

```html
<li id="toast-42" dj-remove="opacity-100 transition-opacity-300 opacity-0">
  Saved!
</li>

<li id="toast-42" dj-remove="fade-out">Saved!</li>
```

When the server emits a `RemoveChild` patch for the element (or any other mechanism that would remove it), the client:

1. Applies the start class synchronously (three-token form only).
2. On the next animation frame, swaps in the active + end classes.
3. Waits for `transitionend`, then physically detaches the element.

A 600 ms fallback timer finalizes the removal if `transitionend` never fires. Override it with `dj-remove-duration="N"`:

```html
<li dj-remove="slide-out" dj-remove-duration="500">...</li>
```

> **Gotcha — no CSS transition defined**: if the classes in your `dj-remove` spec don't define a
> `transition:` property, `transitionend` will never fire. The element stays visible for the full
> 600 ms fallback timer before being removed. Override with `dj-remove-duration="N"` (ms) if your
> element should disappear faster when the transition is absent.

### Cancellation

If a subsequent patch removes the `dj-remove` attribute from a pending element, the pending removal cancels: the applied exit classes are stripped, the fallback timer clears, and the element stays mounted.

### Interop with `dj-transition`

`dj-transition` animates element *entry*. `dj-remove` animates element *exit*. An element can carry both — they don't overlap, because the removal hook only fires when a patch would take the element out of the DOM.

### Scope

Phase 2a of the v0.6.0 Animations & transitions work. Only the element that *carries* `dj-remove` is deferred — descendants travel with their parent.

---

## `dj-transition-group`

Orchestrate enter/leave animations for every child of a list container — without hand-writing `dj-transition` and `dj-remove` on each child. React `<TransitionGroup>` / Vue `<transition-group>` parity.

This attribute does not introduce a new animation runner. It wires the existing [`dj-transition`](#dj-transition) (enter) and [`dj-remove`](#dj-remove) (leave) specs onto each child by setting the corresponding attributes automatically.

### Quick start — long form (preferred)

```django
<ul dj-transition-group
    dj-group-enter="opacity-0 transition-opacity-300 opacity-100"
    dj-group-leave="opacity-100 transition-opacity-300 opacity-0">
    {% for toast in toasts %}
        <li id="toast-{{ toast.id }}">{{ toast.text }}</li>
    {% endfor %}
</ul>
```

New `<li>` children animate in via `dj-transition`. Children removed by a VDOM patch animate out via `dj-remove` (the deferral hook is already wired in `12-vdom-patch.js`).

### Short form — pipe-separated halves

```html
<ul dj-transition-group="fade-in | fade-out">
    <li>A</li>
    <li>B</li>
</ul>
```

The short form splits on `|` into enter / leave halves. Each half accepts the same shapes as `dj-transition` / `dj-remove`:

- Three tokens (phase-cycling): `"opacity-0 transition-opacity-300 opacity-100"`
- Single token (one-class + `transitionend`): `"fade-out"`

An empty half or a missing pipe makes the short form invalid (silently ignored) — use the long form if either half isn't needed.

> **Precedence**: when both short-form (`dj-transition-group="enter | leave"`) and long-form
> (`dj-group-enter="..."` / `dj-group-leave="..."`) attributes are present on the same parent,
> the **long form wins**. This lets you use the compact short form as a default and selectively
> override one half with the long form per-parent.

### Initial children

By default, only the **leave** spec is copied onto each child that's present when the group mounts — so they animate out if later removed, but nothing animates in on first paint.

Opt initial children into first-paint enter animation with `dj-group-appear`:

```html
<ul dj-transition-group dj-group-appear
    dj-group-enter="opacity-0 transition-opacity-300 opacity-100"
    dj-group-leave="fade-out">
    <li>Initial 1</li>
    <li>Initial 2</li>
</ul>
```

### Interop — never overwrites per-child attributes

If a child already carries `dj-transition` or `dj-remove`, the group leaves those attributes alone. This is the escape hatch for per-item overrides:

```html
<ul dj-transition-group="fade-in | fade-out">
    <li>Regular child — gets fade-out on leave</li>
    <li dj-remove="slide-out-left">Custom — group respects this</li>
</ul>
```

### Limitations

- **Direct DOM removal doesn't animate.** `dj-transition-group` orchestrates the animation by setting `dj-remove` on children, then relies on the VDOM-patch integration to defer the removal. If app code calls `child.remove()` directly (bypassing `maybeDeferRemoval`), the removal is immediate. This matches how `dj-remove` works on its own.
- **No FLIP yet.** Reordering a list without insert/remove is not animated — you get fade-in/fade-out on added/removed children, not smooth transforms for moves. FLIP is a separate follow-up.

### Scope

Phase 2c of the v0.6.0 Animations & transitions work.

---

## See also

- [Hooks](hooks.md) — the `dj-hook` primitive these attributes replace
- [JS commands](js-commands.md) — another declarative attribute layer
- [Large lists](large-lists.md) — virtual-list + infinite-scroll often pair with `dj-sticky-scroll`
