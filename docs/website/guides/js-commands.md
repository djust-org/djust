# JS Commands

JS Commands are the djust equivalent of Phoenix LiveView 1.0's `Phoenix.LiveView.JS` module. They let you bind a chain of DOM operations to an event attribute and run them **client-side, without a server round-trip** — but still have the option to mix in a server `push` when you need one.

They're the fastest path to closing the DX gap with Phoenix on animation, optimistic UI, and component-scoped interactions.

Eleven commands are supported:

| Command | What it does |
|---------|--------------|
| `show` | Unhide an element (set `display`, remove `hidden`) |
| `hide` | Set an element's `display` to `none` |
| `toggle` | Flip between shown and hidden |
| `add_class` | Add one or more CSS classes |
| `remove_class` | Remove one or more CSS classes |
| `transition` | Add classes, wait *N* ms, then remove them (for CSS animations) |
| `set_attr` | Set an HTML attribute |
| `remove_attr` | Remove an HTML attribute |
| `focus` | Move keyboard focus |
| `dispatch` | Fire a `CustomEvent` on the target |
| `push` | Send a server event (the escape hatch for server round-trips) |

---

## Quick start

### From a Python view

```python
from djust import LiveView
from djust.js import JS

class ModalView(LiveView):
    template_name = "modal.html"

    def mount(self, request, **kwargs):
        self.open_modal = JS.show("#modal").add_class("open", to="#overlay").focus("#modal-title")
        self.close_modal = JS.hide("#modal").remove_class("open", to="#overlay")
```

```html
<!-- modal.html -->
<button dj-click="{{ open_modal }}">Open modal</button>

<div id="overlay" class="fixed inset-0 bg-black/50"></div>
<div id="modal" style="display: none;">
    <h2 id="modal-title">Edit profile</h2>
    <button dj-click="{{ close_modal }}">Close</button>
</div>
```

Clicking "Open modal" runs three ops locally with zero latency:
1. `show` on `#modal`
2. `add_class("open")` on `#overlay`
3. `focus` on `#modal-title`

The server never hears about it.

### From a `dj-hook`

```javascript
window.djust.hooks = {
    FlashMessage: {
        mounted() {
            // Programmatic chain from a hook — Phoenix 1.0 parity.
            this.js()
                .transition("flash-in", { time: 300 })
                .exec(this.el);
        },
        dismiss() {
            this.js()
                .transition("flash-out", { time: 200 })
                .hide()
                .exec(this.el);
        },
    },
};
```

### Directly from JavaScript

```javascript
// Any time, anywhere in your JS:
await window.djust.js
    .show("#modal")
    .addClass("active", { to: "#overlay" })
    .exec();
```

---

## Targeting: `to`, `inner`, `closest`

Every command accepts **at most one** of these three target kwargs. If you omit all three, the command targets the element that fired the event.

| Target | Meaning | Python | JS |
|--------|---------|--------|----|
| `to` | Absolute `document.querySelectorAll` | `JS.show(to="#modal")` | `js.show("#modal", {to: "#modal"})` |
| `inner` | Scoped to the origin element's descendants | `JS.add_class("big", inner=".title")` | `js.addClass("big", {inner: ".title"})` |
| `closest` | Walk up from the origin | `JS.hide(closest=".modal")` | `js.hide(undefined, {closest: ".modal"})` |
| *(none)* | The origin element itself | `JS.add_class("ripple")` | `js.addClass("ripple")` |

### Why `closest` is so useful

A "close" button inside a modal usually wants to hide **the modal**, not itself. Without scoped targets, every modal needed a unique ID:

```html
<!-- Old way — fragile, every modal needs a unique id -->
<div id="modal-42">
    <button dj-click="{{ JS.hide(to='#modal-42') }}">Close</button>
</div>
```

With `closest`:

```html
<!-- New way — the same button works in every modal -->
<div class="modal">
    <button dj-click="{{ JS.hide(closest='.modal') }}">Close</button>
</div>
```

Drop the same `<button>` into every modal in the app. Zero per-instance configuration.

### `inner` for scoped children

`inner` is the mirror image: select within the trigger element's subtree.

```html
<div class="card">
    <h2 class="title">Report</h2>
    <button dj-click="{{ JS.add_class('highlight', inner='.title') }}">
        Highlight title
    </button>
</div>
```

Clicking the button adds `highlight` to the `.title` inside the same card — no ID needed.

---

## Custom commands

The eleven built-ins cover the common cases; `JS.ext` opens the command set
for anything else — scroll, clipboard, a third-party library call. Register
the implementation once in your own static JS, then chain it from Python
like a built-in:

```javascript
// static/app.js — loaded normally (no build step, CSP-safe: no eval)
window.djust.commands.register('scroll_to', (targets, args) => {
    targets.forEach(el =>
        el.scrollIntoView({ behavior: args.smooth ? 'smooth' : 'auto' })
    );
});
```

```python
# views.py
self.jump = JS.ext.scroll_to(to="#top", smooth=True).add_class("flash", to="#header")
```

```html
<button dj-click="{{ jump }}">Back to top</button>
```

The implementation receives `(targets, args, originEl)`: `targets` is the
resolved element list (the same `to=` / `inner=` / `closest=` rules as
built-ins — omit all three and it's the element that fired the event), and a
returned Promise is awaited before the next op in the chain runs.

Rules worth knowing:

- **Names are namespaced.** Ops serialize as `ext.scroll_to`, so a future
  djust built-in can never collide with your command. Registering a name
  that *is* a built-in (`register('show', …)`) throws immediately.
- **Typos fail loud in dev.** Invoking an unregistered command shows the
  DEBUG error overlay with a did-you-mean suggestion (and `console.error`
  in production). Built-in typos on the Python side still raise
  `AttributeError` at mount — `JS.ext` does not loosen the built-in surface.
- **JS-side chains** use the explicit form: `djust.js.ext('scroll_to', {to: '#top'})`
  or mid-chain `this.js().hide('#m').ext('scroll_to', {to: '#top'}).exec(this.el)`.

### Recipes

#### Copy-to-clipboard with instant feedback

```javascript
// static/app.js — registered once
window.djust.commands.register('clipboard_copy', (targets, args) => {
    navigator.clipboard.writeText(args.text);
});
```

```python
# views.py — chain it with built-ins
self.copy_link = (JS.ext.clipboard_copy(text=self.share_url)
                    .add_class("copied")
                    .transition("pulse", time=400))
```

```html
<button dj-click="{{ copy_link }}">Copy link</button>
```

Click → text copied, button pulses — zero server involvement. Before custom
commands, this was a full `dj-hook` with lifecycle ceremony.

#### Optimistic UI: mix client ops with a server push

```python
# Close the modal INSTANTLY, celebrate, then tell the server — one attribute
self.place_order = (JS.hide("#checkout-modal")
                      .ext.confetti(particles=80)
                      .push("submit_order", value={"cart_id": self.cart_id}))
```

The modal disappears at click-time latency; `push` rides the normal event
pipeline, so debouncing, rate limiting, and the WebSocket/HTTP fallback all
apply unchanged.

#### Async commands are awaited in chain order

```javascript
window.djust.commands.register('fade_out', (targets) =>
    Promise.all(targets.map(el =>
        el.animate({opacity: [1, 0]}, 250).finished))
);
```

```python
# The fade COMPLETES before the row is hidden and the server is told
self.dismiss = (JS.ext.fade_out(closest=".notification")
                  .hide(closest=".notification")
                  .push("mark_read"))
```

A command that returns a Promise holds the rest of the chain until it
resolves. And because the targeting is `closest=`, this same button works in
every notification — no per-row IDs.

#### Drive a third-party library from Python

```javascript
window.djust.commands.register('chart_focus', (targets, args) => {
    targets.forEach(el => {
        const chart = Chart.getChart(el);
        chart.setActiveElements([{datasetIndex: 0, index: args.index}]);
        chart.update();
    });
});
```

```python
# A "Show me Q3" button highlights the chart point — no re-render
self.focus_q3 = JS.ext.chart_focus(to="#sales-chart", index=2)
```

The chart instance itself stays owned by a
[`dj-hook`](hooks#typed-values--targets) (with `dj-update="ignore"` keeping
the morph out of its DOM); the command is how Python *drives* it between
renders. Composed further:

```python
# CodeMirror "jump to error": cursor move + panel highlight + focus,
# expressed as one chainable, template-bindable value
self.jump_to_error = (JS.ext.editor_goto(to="#code-editor", line=self.error_line)
                        .add_class("has-error", to="#editor-panel")
                        .focus("#code-editor"))
```

---

## Command reference

### `show(selector=None, *, inner=None, closest=None, display=None, transition=None, time=None)`

Unhide the target. Sets `element.style.display` to `display` (default: browser default, which restores the CSS rule) and removes the `hidden` attribute. Fires a `djust:show` CustomEvent on the target.

```python
JS.show("#modal")
JS.show(closest=".card", display="flex")
JS.show("#modal", transition="fade-in", time=300)
```

### `hide(selector=None, *, inner=None, closest=None, transition=None, time=None)`

Set the target's `display` to `none`. Fires a `djust:hide` CustomEvent on the target.

```python
JS.hide("#modal")
JS.hide(closest=".modal")
```

### `toggle(selector=None, *, inner=None, closest=None, display=None)`

Flip between shown and hidden based on computed style. When showing, `display` sets the CSS value (e.g. `"flex"`).

```python
JS.toggle("#sidebar")
```

### `add_class(names, *, to=None, inner=None, closest=None)`

Add one or more space-separated CSS classes.

```python
JS.add_class("active", to="#overlay")
JS.add_class("active visible", to="#overlay")
```

### `remove_class(names, *, to=None, inner=None, closest=None)`

Remove one or more space-separated CSS classes.

```python
JS.remove_class("hidden", to="#panel")
```

### `transition(names, *, to=None, inner=None, closest=None, time=200)`

Add the given class(es), wait `time` ms, then remove them. This is the usual way to trigger a CSS transition:

```css
.fade-in { animation: fade 300ms ease-out; }
```

```python
JS.transition("fade-in", to="#modal", time=300)
```

### `set_attr(name, value, *, to=None, inner=None, closest=None)`

Set an HTML attribute.

```python
JS.set_attr("data-open", "true", to="#panel")
JS.set_attr("aria-expanded", "true", closest=".dropdown")
```

### `remove_attr(name, *, to=None, inner=None, closest=None)`

Remove an HTML attribute.

```python
JS.remove_attr("disabled", to="#submit-btn")
```

### `focus(selector=None, *, inner=None, closest=None)`

Move keyboard focus to the target.

```python
JS.focus("#search-input")
```

### `dispatch(event, *, to=None, inner=None, closest=None, detail=None, bubbles=True)`

Fire a `CustomEvent` on the target. Third-party libraries (autocomplete widgets, rich-text editors, charts) that listen for `input`/`change`/`submit`/custom events can be kicked into action this way:

```python
JS.dispatch("chart:refresh", to="#sales-chart", detail={"range": "7d"})
```

### `push(event, *, value=None, target=None, page_loading=False)`

The escape hatch: send a server event as part of a chain. Mix optimistic DOM updates with server round-trips in a single handler:

```python
# Close the modal optimistically, then save on the server.
save_and_close = (
    JS.push("save_draft", value={"id": 42})
      .hide("#modal")
      .remove_class("open", to="#overlay")
)
```

Set `page_loading=True` to show the navigation-level loading bar (`dj-page-loading` elements) while the event is in flight. This bridges per-event scoped loading and the page-level progress indicator.

```python
JS.push("generate_report", page_loading=True)
```

---

## Chaining and reuse

Every chain method returns a **new** `JSChain` — chains are immutable. This lets you reuse a base chain across multiple call sites without cross-contamination:

```python
base_reset = JS.remove_class("error", to="#input").remove_class("hint-visible", to="#hint")

reset_and_refocus = base_reset.focus("#input")
reset_and_notify = base_reset.dispatch("form:reset")
```

`base_reset` still has exactly two ops. `reset_and_refocus` has three. `reset_and_notify` has three. No state leaks between them.

### Pure JS chains

From JavaScript, the same fluent API is available as `window.djust.js`:

```javascript
const chain = window.djust.js
    .show("#modal")
    .addClass("open", { to: "#overlay" })
    .focus("#modal-title");

await chain.exec();
```

You can also build a raw empty chain with `djust.js.chain()` and add ops conditionally:

```javascript
let js = window.djust.js.chain().hide("#modal");
if (shouldReset) js = js.removeClass("error", { to: "#form" });
await js.exec();
```

---

## Backwards compatibility with `dj-click="handler_name"`

The event-binding layer detects whether `dj-click` (and other event attributes) contain a JSON command list (`[[...]]`) or a plain handler name, and dispatches accordingly. **Existing code continues to work unchanged** — you only opt into JS Commands by assigning a chain.

```html
<!-- Plain handler — sends an event to the server. Same as before. -->
<button dj-click="save_draft">Save</button>

<!-- Chain — runs locally. -->
<button dj-click="{{ JS.hide('#modal') }}">Close</button>

<!-- Chain with a push — runs locally, then sends a server event. -->
<button dj-click="{{ JS.hide('#modal').push('saved') }}">Save & Close</button>
```

---

## When to reach for what

- **JS Commands (chains)** — UI state the server does not care about. Modals, accordions, dropdowns, toasts, CSS class toggles, optimistic loading states. The fastest path, no round-trip latency.
- **Plain event handlers** — whenever the server needs to know. Form submissions, searches, selections, anything that mutates persistent state.
- **Hooks (`dj-hook`)** — stateful client-side integrations with third-party JS libraries (charts, editors, maps).

Chains are for **fast, declarative DOM work**. For anything that needs JavaScript state (timers, event listeners, library instances), use a hook and call `this.js()` from inside it.

---

## See also

- [Event handlers](event-handlers.md) — the `@event_handler` decorator reference
- [Hooks](hooks.md) — client-side `dj-hook` integration
- [`dj-paste`](dj-paste.md) — paste event handling that pairs well with JS Commands for optimistic UI
