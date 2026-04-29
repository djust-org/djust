# Declarative UX Attributes

Small declarative HTML attributes that replace custom `dj-hook` code every production djust app ends up writing:

- [`dj-mutation`](#dj-mutation) — fire a server event when DOM mutates
- [`dj-sticky-scroll`](#dj-sticky-scroll) — keep a container pinned to the bottom as content appends
- [`dj-track-static`](#dj-track-static) — warn / reload when JS or CSS assets change on a deploy
- [`dj-transition`](#dj-transition) — declarative CSS enter/leave transitions
- [`dj-remove`](#dj-remove) — exit animations before element removal
- [`dj-transition-group`](#dj-transition-group) — enter/leave animations for every child of a list container
- [`dj-flip`](#dj-flip) — smooth transform animation when keyed children reorder
- [`{% djust_skeleton %}`](#-djust_skeleton---template-tag) — shimmer placeholder blocks for loading states

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
- ~~FLIP — animate list-item reordering~~ ✅ — see below
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
- **Reorder animations use a separate attribute.** `dj-transition-group` animates enter and leave, not moves. For smooth transforms when keyed children reorder in place, add `dj-flip` to the same container — see below.

### Scope

Phase 2c of the v0.6.0 Animations & transitions work.

---

## `dj-flip`

Animates list-item **reordering**. When keyed children swap positions, FLIP (First, Last, Invert, Play) interpolates each moved item from its old bounding box to its new one with a CSS transform — so the UI reflects the data reordering smoothly instead of items jumping to their new slots.

Opt in by adding `dj-flip` to the parent container:

```html
<ul dj-flip>
    {% for item in items %}
        <li id="item-{{ item.pk }}">{{ item.name }}</li>
    {% endfor %}
</ul>
```

The technique:

1. **F (First)** — before the VDOM patch, snapshot each child's `getBoundingClientRect()`.
2. **L (Last)** — after the patch, read the new rects.
3. **I (Invert)** — for each child that moved, apply an inverse `transform: translate(-Δx, -Δy)` that visually puts it back at its old position.
4. **P (Play)** — on the next animation frame, clear the transform with a CSS transition — the item animates from old to new.

### Prerequisites

- **Children need stable IDs.** The Rust VDOM diff only emits `MoveChild` patches (which preserve DOM identity) when it can match old and new children by key. Give each `<li>` / `<tr>` / card a stable `id="…"` attribute — typically `id="item-{{ item.pk }}"`. Without a stable key, reorders fall back to delete+insert and FLIP correctly no-ops (there's no "old node" to animate from).

### Tunables

| Attribute | Default | Notes |
|---|---|---|
| `dj-flip-duration` | `300` (ms) | Non-numeric or out-of-range (<0 or >30000) values fall back. |
| `dj-flip-easing` | `cubic-bezier(.2,.8,.2,1)` | Strings containing `;`, `"`, `'`, `<`, or `>` are rejected to prevent CSS-property-breakout attempts. |

### Reduced motion

When the user's OS reports `prefers-reduced-motion: reduce`, `dj-flip` short-circuits with no animation — elements jump to their new positions immediately. No opt-in required.

### Nested containers

Each `[dj-flip]` installs its own `MutationObserver(childList, {subtree: false})`. Nested `[dj-flip]` elements are isolated — the outer observer doesn't animate the inner container's children, and vice versa.

### Combining with `dj-transition-group`

Enter, leave, and reorder are three separate animation moments. Use `dj-transition-group` for enter/leave, `dj-flip` for reorder, both on the same parent:

```html
<ul dj-transition-group="fade-in | fade-out" dj-flip>
    {% for task in tasks %}
        <li id="task-{{ task.pk }}">{{ task.title }}</li>
    {% endfor %}
</ul>
```

### Limitations

- **Block/flex HTML only.** `transform: translate(...)` behaves oddly on `<tbody>`, `<tr>`, and many SVG elements. The first release targets block and flex children; table-row reordering is out of scope.
- **Author-specified `transform` preserved.** If a child already had an inline `transform: rotate(5deg)`, `dj-flip` restores it after the animation completes rather than leaving the element transformless.

### Scope

Phase 2d (final phase) of the v0.6.0 Animations & transitions work.

---

## `{% djust_skeleton %}` (template tag)

Shimmer placeholder blocks for loading states — the counterpart to `dj-flip` for "the data isn't here yet".

Use inside a conditional so the VDOM replaces the skeleton with real content once the server re-renders:

```django
{% load live_tags %}
{% if loading %}
    {% djust_skeleton shape="line" count=3 %}
    {% djust_skeleton shape="circle" width="48px" height="48px" %}
{% else %}
    <ul>
        {% for item in items %}<li>{{ item.name }}</li>{% endfor %}
    </ul>
    <img src="{{ user.avatar }}" class="avatar">
{% endif %}
```

### Arguments

| Argument | Default | Notes |
|---|---|---|
| `shape` | `"line"` | One of `line`, `circle`, `rect`. Invalid values fall back to `line`. |
| `width` | `"100%"` (line/rect) / `"40px"` (circle) | Must match `^[\d.]+(px|em|rem|%|vh|vw|ch)?$`. Invalid values fall back to the shape default. |
| `height` | `"1em"` (line) / `"40px"` (circle) / `"120px"` (rect) | Same regex as `width`. |
| `count` | `1` | Repeated skeleton blocks (line shape only). Clamped to `[1, 100]`. |
| `class_` | `None` | Extra CSS classes appended to `djust-skeleton djust-skeleton-{shape}`. |

### Shimmer CSS

The tag emits a minimal `<style>` block on first use per render (via `context.render_context`) — no separate CSS file to include, no npm dependency. The default shimmer is a linear-gradient animation with `animation-duration: 1.5s`. Respects `prefers-reduced-motion: reduce` — in that case, the placeholder is a static block with no shimmer.

Override the default look by writing your own `.djust-skeleton` rules in your site's stylesheet — later rules win.

### Integrating with `start_async` / `@background`

The skeleton integrates with the existing [async work patterns](loading-states.md) — render it inside a branch conditional on `self.async_pending` or a named loading flag:

```python
class ReportView(LiveView):
    @event_handler
    @background
    def generate_report(self, **kwargs):
        self.loading = True
        self.report = fetch_slow_report()
        self.loading = False
```

```django
{% if loading %}
    {% djust_skeleton shape="rect" width="100%" height="240px" %}
{% else %}
    {{ report|safe }}
{% endif %}
```

When `self.loading = False` fires, the VDOM diff replaces the skeleton with the real markup. No client-side JS involved; this is pure server-rendered state.

### Global page-load skeletons

For hiding the skeleton on a named WebSocket event, wrap in a `dj-loading` block — see the [loading states guide](loading-states.md).

### Scope

Phase 2d (final phase) of the v0.6.0 Animations & transitions work — the alternative to `<Suspense>` for simple placeholder cases where you don't need the full async-value machinery.

---

## CSS `@starting-style` — browser-native enter animations

[CSS `@starting-style`](https://developer.mozilla.org/en-US/docs/Web/CSS/@starting-style) is a browser-native rule that lets authors declare the "starting" values of a transition when an element appears in the DOM. It's the modern alternative to `dj-transition` for enter animations when you're willing to require Chrome 117+, Safari 17.5+, and Firefox 129+.

**djust requires no framework support** — `@starting-style` is pure CSS, and djust's VDOM insert path (`MoveChild` / `InsertChild` / `Replace`) uses ordinary `appendChild` / `insertBefore` / `replaceChild` which the browser honors normally. When the VDOM patch adds a new element, the browser sees a new DOM insertion and applies any matching `@starting-style` rule automatically. This section documents the pattern; no new djust attributes are introduced.

### Quick start

```css
/* In your project CSS */
.toast {
    opacity: 1;
    transform: translateY(0);
    transition:
        opacity 300ms ease-out,
        transform 300ms ease-out;
}

@starting-style {
    .toast {
        opacity: 0;
        transform: translateY(-10px);
    }
}
```

```html
{% if show_toast %}
    <div class="toast">Saved successfully</div>
{% endif %}
```

When the server toggles `show_toast` to `True` and sends a patch that inserts the `<div class="toast">`, the browser:

1. Matches the `.toast` selector.
2. Notices a `@starting-style` block exists for that selector.
3. Applies the `@starting-style` values first (the "from" state).
4. Transitions to the normal values on the next frame.

Result: the toast fades + slides into view without any JS or declarative attribute.

### Comparison: `@starting-style` vs `dj-transition`

| | `@starting-style` | `dj-transition` |
|---|---|---|
| Browser support | Chrome 117+, Safari 17.5+, Firefox 129+ | Any browser with `transition` support (IE10+) |
| Where you write it | In your CSS stylesheet | On the HTML element as an attribute |
| Runtime cost | Zero JS | Small JS module (`41-dj-transition.js`) |
| Per-element customization | Requires a unique class or selector | Inline attribute token list |
| Works with `display: none` toggle | ✅ Yes (spec treats that as a DOM insertion) | ✅ Yes |
| Works with server-side conditional insert | ✅ Yes | ✅ Yes |
| Good for | Static, repeatable animations across many elements | Dynamic, one-off transitions (e.g., a unique modal entry) |

Pick `@starting-style` when the animation is part of the component's visual identity (every `.toast` animates the same way). Pick `dj-transition` when you need per-element transitions, or when you must support older browsers.

### Interop with `dj-remove`

`@starting-style` only handles the enter side. For exit animations, continue using `dj-remove` — browsers don't yet have a native counterpart for "animate an element before it's removed from the DOM."

```html
<div class="toast" dj-remove="opacity-100 transition-opacity-300 opacity-0">
    Saved successfully
</div>
```

The element gets browser-native `@starting-style` fade-in on insert and `dj-remove` fade-out on removal — the two features cooperate cleanly.

### Caveats

- **`@starting-style` values apply only on the first frame after insertion.** If you navigate the user to a page that already has the element visible (e.g., back-navigation with `[dj-sticky-slot]` reattachment), the browser doesn't re-play the starting-style — which is almost always the right call (the sticky content shouldn't re-animate every navigation).
- **Browser support is newer than most animations.** Check [caniuse.com/mdn-css_at-rules_starting-style](https://caniuse.com/mdn-css_at-rules_starting-style) before relying on it for critical UX. For cross-browser compatibility, pair with a `dj-transition` fallback or gate behind `@supports (at-rule(@starting-style))`:

  ```css
  @supports (at-rule(@starting-style)) {
      @starting-style {
          .toast { opacity: 0; }
      }
  }
  ```

- **Not compatible with inline `style=` attributes.** `@starting-style` only works inside stylesheet rules (external CSS or `<style>` blocks). If you need per-element starting states, use a class or attribute selector to hook the rule.
- **VDOM patcher interop tested.** djust's insert paths (`MoveChild`, `InsertChild`, `Replace`, morph-insert) use `appendChild` / `insertBefore` / `replaceChild` which trigger the browser's standard insertion handler and honor `@starting-style`. No special handling required.

### Scope

Documentation-only. No new djust attributes, no new JS module, no wire-protocol changes. The feature is delivered by the browser; djust's role is to not break it (confirmed).

---

## `dj-dialog` — native `<dialog>` modal integration

The HTML `<dialog>` element ships with the browser's
focus-trap, backdrop, and Escape-to-close behavior built in. `dj-dialog`
flips the modal's open state declaratively without writing a hook.

### Quick start

```django
<button dj-click="open_modal">Edit profile</button>

<dialog dj-dialog="{{ modal_open|yesno:'open,close' }}">
    <form method="dialog">
        …
        <button value="save">Save</button>
        <button value="cancel">Cancel</button>
    </form>
</dialog>
```

| Value | Effect |
|---|---|
| `dj-dialog="open"`  | calls `dialog.showModal()` |
| `dj-dialog="close"` | calls `dialog.close()` |

### Behavior

A document-level `MutationObserver` watches for attribute changes and
DOM insertions, so VDOM morphs that swap `dj-dialog` work without
per-element re-registration. Re-asserting `"open"` on an
already-open dialog is a no-op (idempotent). Non-`<dialog>` elements
carrying the attribute are silently ignored — the attribute does not
upgrade arbitrary elements into modals.

### Pairing with `dj-ignore-attrs`

When the user dismisses a `<dialog>` by pressing Escape, the browser
flips the `open` attribute on its own. If the server hasn't yet
re-rendered with `modal_open=False`, the next VDOM patch could
accidentally re-open the dialog. Add `dj-ignore-attrs="open"` to mark
the `open` attribute as client-owned so VDOM `SetAttr` skips it:

```html
<dialog dj-dialog="{{ modal_open|yesno:'open,close' }}"
        dj-ignore-attrs="open">…</dialog>
```

### Scope

`python/djust/static/djust/src/35-dj-dialog.js` (~80 LOC). 8 JSDOM
tests in `tests/js/dj_dialog.test.js`.

---

## Form polish — `dj-no-submit`, `dj-trigger-action`, `dj-loading=""`

Three small declarative attributes that close the most common form-UX
gaps without requiring a hook.

### `dj-no-submit="enter"` — block Enter-to-submit

```html
<form dj-no-submit="enter" dj-submit="save">
  <input type="text" name="title">
  <input type="text" name="tag">
  <button>Save</button>
</form>
```

Pressing Enter inside a single-line `<input>` no longer submits the
whole form — users can confirm a field with Enter and tab to the next
one. Textareas (multi-line input), submit-button clicks, and modified
keys (Shift+Enter, Ctrl+Enter) are unaffected. Comma-separated mode
list is reserved for future expansion (currently only `"enter"` is
recognized).

### `dj-trigger-action` + `self.trigger_submit(selector)` — bridge to native POST

For OAuth redirects, payment-gateway handoffs, or any flow that needs
the browser's native form-submit (full page navigation, no AJAX):

```python
class CheckoutView(LiveView):
    def handle_event_pay(self):
        result = stripe.PaymentIntent.create(...)
        if result.ok:
            self.trigger_submit('#stripe-form')   # client posts the form natively
```

```django
<form id="stripe-form" action="https://checkout.stripe.com/..." method="POST"
      dj-trigger-action>
    <input type="hidden" name="token" value="{{ stripe_token }}">
</form>
```

The form must explicitly opt in with `dj-trigger-action` — refusal to
submit a form without the attribute is logged in debug mode. This
prevents a server-pushed event from submitting an unintended form.

### `dj-loading="event_name"` — scoped loading indicator (shorthand)

```html
<button dj-click="search">Search</button>
<div dj-loading="search">Searching…</div>
```

The element shows only while the named event is in-flight; on
register, it auto-hides without an inline `style="display:none"`.
Equivalent to the verbose form `<div dj-loading.show dj-loading.for="search">`,
just shorter for the common case. The verbose `dj-loading.*` modifier
family still works alongside it.

### Scope

`python/djust/static/djust/src/34-form-polish.js` (~90 LOC) +
`self.trigger_submit()` in `python/djust/mixins/push_events.py`.
11 JSDOM tests in `tests/js/form_polish.test.js`, 4 Python tests
covering the push-event shape.

---

## See also

- [Hooks](hooks.md) — the `dj-hook` primitive these attributes replace
- [JS commands](js-commands.md) — another declarative attribute layer
- [Large lists](large-lists.md) — virtual-list + infinite-scroll often pair with `dj-sticky-scroll`
