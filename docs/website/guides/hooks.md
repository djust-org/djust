---
title: "Client-Side JavaScript Hooks"
slug: hooks
section: guides
order: 5
level: intermediate
description: "Lifecycle callbacks for integrating charts, maps, editors, and custom DOM behavior"
---

# Client-Side JavaScript Hooks

djust hooks let you run custom JavaScript when elements are mounted, updated, or removed from the DOM. This is essential for integrating third-party libraries (charts, maps, editors) or any behavior that requires direct DOM access.

## What You Get

- **Lifecycle callbacks** -- `mounted()`, `updated()`, `destroyed()`, `disconnected()`, `reconnected()`, `beforeUpdate()`
- **Server communication** -- `pushEvent()` to send events, `handleEvent()` to receive server pushes
- **DOM access** -- `this.el` gives direct access to the hooked element
- **Automatic management** -- Hooks mount and destroy as the DOM changes via LiveView patches

## Quick Start

### 1. Register Hooks in JavaScript

```html
<script>
window.djust.hooks = Object.assign(window.djust.hooks || {}, {
    MyChart: {
        mounted() {
            this.chart = new Chart(this.el, {
                type: 'line',
                data: this.values.data,
            });
        },
        updated() {
            this.chart.data = this.values.data;
            this.chart.update();
        },
        destroyed() {
            this.chart.destroy();
        }
    }
});
</script>
```

### 2. Use `dj-hook` in Your Template

```html
<canvas dj-hook="MyChart" dj-hook-value-data='{{ chart_data_json }}'></canvas>
```

`dj-hook-value-data` is exposed on the instance as `this.values.data`,
JSON-parsed automatically — no manual `JSON.parse(this.el.dataset...)`.
See [Typed values & targets](#typed-values--targets) below for the full
coercion rules.

### 3. Communicate with the Server

```javascript
window.djust.hooks = Object.assign(window.djust.hooks || {}, {
    StoreMap: {
        mounted() {
            this.map = new MapLibrary(this.el);
            this.map.on('click', (e) => {
                this.pushEvent('location_selected', {
                    lat: e.lat, lng: e.lng
                });
            });

            this.handleEvent('center_map', (payload) => {
                this.map.setCenter(payload.lat, payload.lng);
            });
        },
        destroyed() {
            this.map.remove();
        }
    }
});
```

## Lifecycle Callbacks

| Callback | When Called |
|----------|------------|
| `mounted()` | Element first appears in the DOM. Initialize libraries, bind listeners. |
| `updated()` | After a server re-render that changed the hook's element or anything inside it (after a full morph: every render). Sync libraries with new data. |
| `updateOn: 'render'` | Optional property on the hook definition: get `updated()` after every render, as before #4001. |
| `shouldUpdate()` | Optional synchronous predicate before `updated()`; returning `false` skips that callback only. |
| `beforeUpdate()` | Before an incremental patch batch or full HTML morph, once per update within the updated root (including scoped child updates). Save state that would be lost (scroll position, selection). |
| `destroyed()` | Element removed from the DOM, replaced, or its `dj-hook` removed or renamed. Clean up libraries, observers, timers. |
| `disconnected()` | The live connection (WebSocket or SSE) drops. Show offline indicators. |
| `reconnected()` | A connection is open again after `disconnected()`. Refresh state. |

The exact rules for when each one runs are in [The lifecycle contract](#the-lifecycle-contract) below.

After an incremental patch a hook receives `updated()` only when the patch
changed its element (an attribute) or something inside it (text, attributes,
children inserted, removed or moved); a patch elsewhere on the page does not
reach it (#4001). A full morph still updates every hook. A widget that reads
state from outside its element sets `updateOn: 'render'` on its definition to
get `updated()` after every render. For an expensive widget, `shouldUpdate()` can compare its relevant
inputs and return `false` when no work is needed. Compare raw attribute values
before reading `this.values` if parsing large JSON payloads is costly. This
opt-in does not suppress `beforeUpdate()`, removal cleanup, or connection
callbacks. Only synchronous `false` skips the update; errors and promises
fail open. A widget must account for every dependency it uses, including any
changes to children or external state, before opting in.

## The lifecycle contract

These rules hold for every hook, under the full bundle and the default core
bundle alike (#3656). `tests/js/hook-lifecycle-contract-3656.test.js` has a test
for each, and `tests/playwright/test_hook_lifecycle_3656.py` checks them in a
real browser.

An **update** below is one server render applied to the page: an incremental
patch batch, a full HTML morph (including recovery after failed patches, a
`live_redirect` and the render that follows a reconnect), or a scoped update of
an embedded or child view. A page swap (a navigation that loads a new page
shell) is not an update: the old page's hooks get `destroyed()` and the new
page's `mounted()`, with no `beforeUpdate()`.

1. **One instance per element node.** A hook instance is created for one DOM
   node and keeps it: `this.el` never changes. An update that keeps the node
   keeps the instance (attribute and text changes, a keyed move or reorder);
   `djust.getHook(el)` returns the same object. An update that replaces the
   node (the tag changes, an unkeyed list is re-matched by position, a page
   swap rebuilds it) destroys the old instance
   and mounts a new one on the new node. Renaming `dj-hook` on the same node, or
   removing it in a full morph, also destroys the instance (an incremental patch
   never removes a `dj-*` attribute). To tie a hook to a row rather than to a
   position, give the element an `id` or `dj-key`.
2. **`mounted()` runs exactly once per instance**: on page load for the hooks
   already in the page, and after an update for the hooks it inserted. A hook
   mounted by an update gets no `updated()` for that same update, also when a
   `mounted()` calls `djust.updateHooks()` itself.
3. **`beforeUpdate()` runs once per update, before its first DOM change**, for
   every mounted hook whose element is inside the region being updated (the
   whole view, or the container of a scoped update). Hooks outside that region
   get none.
4. **`updated()` runs after the update has been applied** and djust has bound
   the `dj-*` attributes it inserted. After an incremental patch batch it runs
   for every hook whose element or subtree the batch changed (an attribute of
   the element, or text, attributes or children anywhere inside it), and for
   every hook whose definition sets `updateOn: 'render'`; a hook the batch did
   not touch gets none, though it got `beforeUpdate()` (#4001). After a full
   HTML morph, and on the first patch after a child or sticky view's update or
   a reconnect, it runs for every hook whose element is still in the document,
   including hooks outside the updated region. Use `shouldUpdate()` to skip it.
5. **`destroyed()` runs exactly once per instance**, when its element leaves the
   document, is replaced, or loses or renames its `dj-hook`, at the end of the
   update that did it. An element leaving with a [`dj-remove`](declarative-ux-attrs)
   exit transition stays mounted while the transition plays and is destroyed
   when it is finally removed. After `destroyed()` the instance gets no more
   callbacks and no `handleEvent()` deliveries.
6. **Order within one update**: every `beforeUpdate()`, then the DOM change,
   then every `destroyed()`, then `mounted()` and `updated()` in document order
   (a parent before its children). So a replacement's `mounted()` always comes
   after the old instance's `destroyed()`.
7. **`dj-update="ignore"` regions** are never patched: a hook on or inside an
   ignored element keeps its instance and node for as long as the region stays,
   and still gets `beforeUpdate()` / `updated()` like any other hook. On a full
   morph the ignored element's own attributes are frozen too, so put the hook
   and its `dj-hook-value-*` on a wrapper (see [Why `dj-update="ignore"`](#why-dj-updateignore)).
   `dj-update="append"` / `"prepend"` keep the existing children (their hooks
   are updated) and mount hooks on the added ones. `dj-ignore-attrs` leaves the
   listed attributes alone; `this.values` always reads the current attribute.
8. **`disconnected()` and `reconnected()` pair up per instance**, over WebSocket
   and SSE alike. A hook gets one `disconnected()` when the live connection
   drops, however many reconnect attempts then fail, and one `reconnected()`
   when a connection is open again: a WebSocket that opens (also the first
   one, after a failed first handshake), an SSE stream that opens (also as
   the fallback after WebSocket), or an event answered over the page-POST
   HTTP fallback, which counts as connected. `reconnected()` never comes without a `disconnected()`
   before it. A hook mounted while the connection is down gets `disconnected()`
   right after `mounted()`. `reconnected()` runs as soon as the connection is
   open, before the server's render of the view arrives; that render is an
   ordinary update (rules 3 to 6).
9. **A failing hook stays contained.** Every callback (including
   `shouldUpdate()` and `handleEvent()` callbacks) runs inside `try`/`catch`; an
   exception or a rejected promise is logged as
   `[dj-hook] Error in Name.callback():` and the patch, the other hooks and the
   rest of the update go on. A hook whose `mounted()` throws stays mounted, so
   its `destroyed()` still runs and can undo a partial setup. A registry entry
   that is not an object is reported as unregistered and skipped.
10. **Callbacks are not awaited** (see below): `destroyed()` can run while an
    async `mounted()` is still pending, so check for what it set up before
    disposing of it.

### Async lifecycle callbacks (v0.8.6+)

Lifecycle methods may be `async` (return a Promise). The dispatcher
detects Promise return and chains `.catch` to log rejections via
`console.error` — no Unhandled Promise Rejection in the browser console.

```javascript
window.djust.hooks.UserAvatar = {
    async mounted() {
        // Fetch profile data on mount; safe — rejection logs cleanly
        const res = await fetch(`/api/profile/${this.values.userId}`);
        const profile = await res.json();
        this.el.querySelector('img').src = profile.avatar_url;
    },
};
```

**Fire-and-forget contract**: the dispatcher does NOT await your async
hook. Lifecycle callbacks run in parallel with subsequent patches —
your hook's I/O can't block the render loop. If you need sequencing
across hook completion (e.g. "patch B must wait for hook A's fetch"),
coordinate explicitly via `pushEvent` + a server-side handler.

Sync hooks (no async/await, no Promise return) behave exactly as
before. This is purely additive: no API change for existing hook code.

### Settled questions (#4322)

Three behaviours that slice 1 of #3656 left open are settled as follows:

- **`updated()` is not scoped further and is not paired with
  `beforeUpdate()`.** `beforeUpdate()` is exact (the hooks inside the updated
  region, rule 3). `updated()` means "the DOM this widget reads may have
  changed": exact after an incremental patch, which carries a change record
  (#4001), and conservative where there is none (a full morph, html recovery,
  the first patch after a child, sticky or reconnect update), where every
  mounted hook gets it. A hook can therefore get `updated()` without a
  `beforeUpdate()` (outside a scoped update's region) and `beforeUpdate()`
  without `updated()` (an incremental patch that did not touch it). Code that
  needs a pair compares its inputs in `shouldUpdate()` or `updated()`, as the
  Chart.js adapter does.
- **A page swap destroys without `beforeUpdate()`.** `destroyed()` is the last
  callback a widget gets on every exit (removal, replacement, navigation, page
  swap), so state worth keeping is saved there or as it changes (see
  [local drafts](#local-drafts-unsent-edits)), never only in `beforeUpdate()`.
- **Bind-time order.** `mounted()` runs after djust has bound the `dj-*` event
  attributes in the inserted DOM and before `dj-model` binding and virtual-list
  setup. A widget that needs either reads it in `updated()` or after a
  `queueMicrotask()`.

## The widget contract

The lifecycle rules above say *when* a hook runs. These say what a widget (a
hook that hands part of the page to a client-side library: a chart, a map, a
rich-text editor) owns, what reaches it, and what comes back from the server
(#4322). `tests/js/hook-event-outcomes-4322.test.js` tests the event outcomes
over every transport, and `tests/playwright/test_widget_contract_4322.py`
proves the whole contract in Chromium with the shipped Chart.js adapter and the
Quill rich-text editor (a test-only fixture, not part of djust), over
WebSocket, SSE, HTTP-only and under a nonce-only CSP.

### Ownership

Every node and attribute the template renders is **server-owned**: each
update makes it match the server's render again. A widget **owns** only:

1. **The subtree of a `dj-update="ignore"` element** (give it an `id`). The
   patcher never enters it, on incremental patches and full morphs alike; this
   is where the library draws (Quill's editor, a map's tiles). Put the hook on
   a wrapper around it, not on the ignored element (rule 7).
2. **Attributes listed in `dj-ignore-attrs`** on a server-owned element:
   incremental patches never set them and morphs neither set nor remove them.
3. **A canvas's drawing buffer and size.** A morph never removes a canvas's
   `width` / `height`. Don't render `width` / `height` on a canvas a library
   sizes itself (Chart.js does), or a morph resets them to the template's values
   and clears the drawing; list `style` in `dj-ignore-attrs` when the library
   writes inline styles.
4. **JavaScript state**: the hook instance, expandos on its nodes, and anything
   the library keeps.

### What a widget may write back

- Into the DOM it owns, anything.
- Into server-owned DOM, **nothing it expects to keep**. An attribute a widget
  sets on a server-owned element is overwritten when a patch next sets that
  attribute and removed by the next full morph (html recovery, a reconnect's
  mount, an HTTP answer), unless it is in `dj-ignore-attrs`. Text or children a
  widget inserts are replaced by the next patch or morph of that element.
- **Never add, remove or move server-owned nodes.** Patches address nodes by
  their position; a structural change makes the next patch fail, and djust
  recovers by asking for the whole HTML (`html_recovery`), which costs a round
  trip and resets every widget-written attribute on the page.
- **State goes to the server by `pushEvent()`**, and comes back through the
  render, as props.

### Props

- **In:** `dj-hook-value-*` attributes on the hook element, read live through
  `this.values` (JSON-first; see [Typed values](#typed-values--targets)). Keep
  them off an ignored element (its attributes freeze on a full morph). A change
  to a value attribute is a change to the hook element, so `updated()` runs.
- **Out:** `pushEvent()`. `this.values` is read-only.
- **Server props versus local edits:** a widget whose user can edit (an editor)
  decides in `updated()` whether a new server value replaces local content.
  The rule the Quill fixture follows, and the one to copy: replace only content
  the user has not edited since the last successful save.

### Event outcomes

`this.pushEvent(event, payload)` resolves with the server's reply frame, or
`null` when the event did not succeed (unchanged since #3990). Pass
`{outcome: true}` to learn how it ended:

```javascript
const outcome = await this.pushEvent('save', {text}, {outcome: true});
// outcome = {status, reply, error}
if (outcome.status === 'ok') this.dirty = false;
else if (outcome.status === 'disconnected') this.retryOnReconnect = true;
```

| `status` | When | `reply` / `error` |
|---|---|---|
| `ok` | the server ran the turn and answered | `reply`: the `render` frame (its patches and `effects[]`) |
| `error` | the handler raised, the view or handler does not exist, permission was refused, or another server error; also a refused turn's acknowledgement (`error.code: "refused"`) | `error: {message, code, validation_details: null}` |
| `rejected` | the strict parameter contract refused the payload; the handler did not run | `error.validation_details` names the expected, missing and mistyped parameters |
| `rate_limited` | `@rate_limit` or the connection's limiter dropped it (`code: "rate_limited"`, HTTP 429) | `error.code: "rate_limited"` |
| `cancelled` | the client gave up on it: the page navigated or swapped, the connection was closed on purpose, or the page changed while an HTTP event was queued; also an event sent as the page leaves (from `destroyed()` during navigation), which may still reach the server but whose answer is never read | both `null` |
| `disconnected` | there was no connection to send it on (nothing is sent), or the connection dropped before the answer (over SSE, also when the stream reconnects: the new server session never answers it) | both `null` |

Rules:

- **It never rejects**, and resolves exactly once.
- **Matched by `ref`** (transport plan D2): every event carries a `ref`, and
  the server echoes it on its answer, its error frame and its rate-limit frame,
  so an answer to another event never settles this one.
- **`ok` resolves after the reply has been applied**: its DOM change is in
  the page and its effects (`push_event` to `handleEvent()`, flash,
  navigation) have run.
- **`ok` means the server ran the turn**, not that the handler did what the
  widget wanted; a handler reports a domain failure in its render or with
  `push_event`.
- **After `disconnected` the event may or may not have run** (the connection
  dropped after the send). Make events a widget re-sends idempotent, and
  re-send in `reconnected()` or after the reconnect's render.
- **Transports.** The same outcomes over WebSocket, SSE and the page POST. On a
  page that chose the HTTP transport (`use_websocket: False` without
  `EventSource`, or a function view) a hook's event is a page POST, as a
  `dj-click` is. A WebSocket or SSE page whose connection is down does **not**
  fall back to POSTs for hook events; they resolve `disconnected`.
- A hook's events skip `@debounce` / `@throttle` (client-side, for `dj-*`
  bindings) on every transport, and take no loading state.

### Focus, selection and IME

Inside the DOM a widget owns, focus, the caret, the selection and an IME
composition in progress survive every update: an incremental patch (also one
that changes the hook element's own attributes), a full morph and html
recovery, because the patcher never touches that subtree. `updated()` runs
during the composition; a widget must not rewrite its content then (check
`this.dirty`, or the library's own composition state) or it ends the
composition. The browser test composes Japanese text in Quill with the
Chromium IME API across a patch and a recovery morph, and checks the caret,
focus and that `compositionend` comes only from the commit. Server-owned form
inputs are a separate case: the patcher keeps the value and caret of the
focused input itself, and an input a widget renders inside its ignored region
is the widget's.

### Strict CSP

Under a nonce-only `script-src` (no `'unsafe-inline'`, no `'unsafe-eval'`):

- djust's own scripts, the route map of `{% djust_client_config %}`, the
  optional client chunks and the official adapters (`DJUST_CONFIG["extensions"]`)
  carry `request.csp_nonce`. Load the library itself (Chart.js, Quill) with
  `nonce="{{ request.csp_nonce }}"`, and your hook registrations from a nonced
  file or script.
- `{% colocated_hook %}` bodies run in a script element with the nonce. A
  morph of the hook's `<script>` (the socket render has no nonce) does not run
  the same body again; a changed body needs the nonce of the page.
- The DEBUG-only debug panel binds inline handlers and reports
  `script-src-attr` violations; it is not part of the page's client.

### Local drafts (unsent edits)

A widget's unsent local edits are widget state:

- **Across patches and morphs** they stay: they live in the DOM it owns or in
  the library. Don't let `updated()` overwrite them with server props.
- **Across a reconnect** they stay: WebSocket and SSE reconnects morph the
  page back, so the instance survives (`disconnected()` then `reconnected()`,
  then an ordinary update with the server's restored state). The widget
  re-sends what is unsent once it is back.
- **Across navigation** (`dj-navigate`, `live_redirect`, Back, a page swap) the
  instance is destroyed. Edits that must survive are stored by the widget as
  they happen (the Quill fixture writes `localStorage` on each user change,
  keyed by page and element id, and clears it after an `ok` save) and restored
  in `mounted()`. `pushEvent()` from `destroyed()` is not delivered reliably
  and always settles `cancelled`: on the HTTP transport it leaves as a
  keepalive POST that may reach the server, but its answer is not read.
- **`data-draft`** ([DraftModeMixin](../state/index.md#draftmodemixin)) saves
  form fields only. It does not see a widget's owned DOM; a widget that wants
  the same lifetime uses its own storage as above.

## Integrating third-party libraries (Chart.js, maps, editors)

This is the canonical pattern for any client-side library that draws into or
manages its own DOM — Chart.js, Leaflet/MapLibre, CodeMirror, a rich-text
editor. **Use a hook. Do not init the library from an inline `<script>` inside
the reactive root.**

### The trap: inline `<script>` works on reload, blank after navigation

The intuitive approach is to drop a `<script>` next to the element, inside the
LiveView's reactive (`dj-view`) root:

```html
<!-- ❌ Works on a full page load, SILENTLY BLANK after dj-navigate -->
<canvas id="sales"></canvas>
<script>
    new Chart(document.getElementById('sales'), { /* ... */ });
</script>
```

On a hard reload this runs and the chart draws. But when you arrive via a
[`dj-navigate`](navigation#dj-navigate) SPA link, djust patches the new page in
over the WebSocket — and **scripts in morphed-in content do not execute**. The
library never initializes, the canvas stays blank, and **there is no error in
the console**. The confusing signature is *"reload works, navigation doesn't."*
If you've spent an hour on a chart that renders on refresh but is empty after
clicking a link, this is why.

### The fix: register a hook once in the persistent shell

Register the hook **once** in your base template (the persistent shell that
loads on every page and survives SPA navigation), and have the element opt in
via `dj-hook`. The hook's `mounted()` fires both on initial hydration **and**
every time the element is patched in via SPA navigation — so the library
initializes on every route.

```html
<!-- base.html — loaded once, OUTSIDE the per-page reactive content -->
<script>
window.DjustHooks = window.DjustHooks || {};
window.DjustHooks.Chart = {
    // The hook sits on a wrapper <div>; the canvas is an ignored child.
    mounted()   { this.draw(); },   // fires on hydration AND SPA patch-insert
    updated()   { this.draw(); },   // server re-rendered new data
    destroyed() {                   // element left the DOM — dispose
        const c = Chart.getChart(this.target('canvas'));
        if (c) c.destroy();
    },
    draw() {
        // Tear down a prior instance before redrawing (avoids Chart.js's
        // "Canvas is already in use" error on updated()/re-mount).
        const canvas = this.target('canvas');
        const prior = Chart.getChart(canvas);
        if (prior) prior.destroy();
        const data = this.values.chart;
        new Chart(canvas, {
            type: data.type,
            data: { labels: data.labels, datasets: [{ data: data.values }] },
        });
    },
};
</script>
```

```html
<!-- per-page template — the wrapper opts into the hook; the canvas is ignored -->
<div dj-hook="Chart" dj-hook-value-chart='{{ chart_json }}'>
    <canvas id="sales-chart" dj-update="ignore" dj-hook-target="canvas"></canvas>
</div>
```

> `window.DjustHooks` and `window.djust.hooks` are merged into one registry, so
> either works. `window.DjustHooks` is the Phoenix-LiveView-compatible name.
>
> For one name, `window.djust.hooks` wins over `window.DjustHooks`. The hook
> scripts djust ships for components (`SortableList`, `SortableGrid`,
> `JsonViewer`, `LogViewer`) register in `window.DjustHooks` and only when
> neither registry already has that name, so a hook of your own in either
> registry replaces the shipped one. Add yours with `Object.assign` rather than
> assigning a new `window.DjustHooks` object, which would discard the shipped hooks.

### Why `dj-update="ignore"`

Chart.js (and maps, editors, …) mutate the element's own DOM — a `<canvas>`
gets a sized drawing buffer, a map fills a `<div>` with tiles. djust's VDOM has
no idea those mutations happened and would fight the library on the next patch,
wiping its work. `dj-update="ignore"` tells the patcher to **skip this element
entirely** on server re-renders, leaving the subtree fully under the library's
control. You drive updates yourself from `updated()` (reading the live
`this.values` proxy), not from the VDOM.

Two details make this work:

- **Give the ignored element an `id`.** On full-HTML updates the client skips
  any `dj-update` element without one and logs "Element with dj-update must
  have an id".
- **Keep `dj-hook-value-*` off the ignored element.** On a full morph an
  ignored element's attributes are frozen too, so a value attribute on the
  canvas itself would keep its first value and `this.values` would go stale.
  Put the hook and its values on a non-ignored wrapper, as above, and find the
  canvas with `this.target('canvas')`.

> For elements where you want the VDOM to keep patching *most* attributes but
> leave a specific few alone (e.g. a `<dialog open>` toggled by the browser),
> use [`dj-ignore-attrs`](#ignored-attributes-dj-ignore-attrs--v050) instead —
> it's a per-attribute opt-out rather than a whole-element one.

### Cleanup matters

`destroyed()` fires when the element leaves the DOM (including when you navigate
*away* via `dj-navigate`). Disposing the library instance there prevents leaked
canvases, dangling map listeners, and Chart.js's "Canvas is already in use"
error when you navigate back. See [Best Practices](#best-practices) below.

## Hook Instance API

> **Reserved names**: `el`, `viewName`, `values`, `target`, `targets`,
> `pushEvent`, `handleEvent`, `js`, and `_eventHandlers` are framework-owned properties that
> djust assigns on every hook instance (see `this.values` / `this.target()`
> below). If your hook definition declares a member with one of these
> names — `{ values: [], mounted() {...} }`, or a method named `target` —
> the framework's own property silently shadows it. There is no warning at
> registration or mount time; your member is simply never called. Pick a
> different name for hook-owned state (e.g. `this._chart`, `this.data`).

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `this.el` | `HTMLElement` | The DOM element with `dj-hook`. Updated automatically on patches. |
| `this.viewName` | `string` | LiveView name from closest `[dj-view]` ancestor. |

See [Typed values & targets](#typed-values--targets) below for
`this.values`, `this.target()`, and `this.targets()`.

### Methods

**`this.pushEvent(event, payload, options)`** -- Send an event to the server LiveView. It returns a promise that resolves with the server's reply frame once the event is acknowledged, or with `null` when the event did not succeed (an error, a refusal, cancelled, or no live connection). With `{outcome: true}` it resolves with `{status, reply, error}` instead; see [Event outcomes](#event-outcomes). It never rejects.

```javascript
this.pushEvent('item_clicked', { id: this.values.itemId });
```

**`this.handleEvent(eventName, callback)`** -- Listen for server push events.

```javascript
this.handleEvent('highlight', (payload) => {
    this.el.style.backgroundColor = payload.color;
});
```

### Reaching a hook from page code

`window.djust.getHook(el)` returns the instance mounted on an element — the
same object its callbacks receive as `this` — or `null` when no hook is
mounted there (never mounted, or already destroyed). It also accepts a CSS
selector, resolved with `document.querySelector`:

```javascript
const chart = window.djust.getHook('#sales-chart');
chart?.highlight(3);   // any method or state the hook defines
```

Call it when you need the instance, rather than keeping the result: a hook
whose element the server replaces is destroyed and a new instance mounted.

## Typed values & targets

Passing data to a hook used to mean raw `dataset` strings and hand-rolled
parsing. `dj-hook-value-*` attributes are exposed on the instance as
`this.values.*` with JSON-first coercion:

```html
<canvas dj-hook="Chart"
        dj-hook-value-points="[1,2,3]"
        dj-hook-value-animated="true"
        dj-hook-value-title="Sales"></canvas>
```

```javascript
window.djust.hooks = Object.assign(window.djust.hooks || {}, {
    Chart: {
        mounted() {
            this.values.points     // [1, 2, 3]  — Array
            this.values.animated   // true       — Boolean
            this.values.title      // "Sales"    — string (JSON.parse failed → raw)
            renderChart(this.el, this.values);
        },
        updated() {
            // values are LIVE: after a server re-render changes the
            // attributes, reads here see the fresh values automatically.
            renderChart(this.el, this.values);
        },
    },
});
```

The coercion rule: try `JSON.parse`, fall back to the raw string. A literal
string that *looks* like JSON must be JSON-quoted:
`dj-hook-value-code='"007"'` → `"007"` (not the number 7). Attribute names
are kebab-case, property names camelCase
(`dj-hook-value-points-per-page` → `this.values.pointsPerPage`). `this.values`
is read-only — update the attribute server-side and let the morph carry it.
Access values by name only — the object is not enumerable (`Object.keys(this.values)`/spread return nothing in v1).

Named descendants use `dj-hook-target`:

```html
<div dj-hook="Player">
    <video dj-hook-target="video"></video>
    <button dj-hook-target="mute">🔇</button>
</div>
```

```javascript
this.target('video')   // first match (Element or null)
this.targets('mute')   // all matches (Element[])
```

Lookups are live subtree queries scoped to `this.el`. Note: descendants of a
*nested* hook are not excluded in v1 — prefer unique target names when
nesting hooks.

### Before / after

What the same hook looked like on raw `dataset` strings, and with typed
values + targets:

```javascript
// BEFORE: every hook re-invented this
mounted() {
    const points = JSON.parse(this.el.dataset.points || "[]");
    const animated = this.el.dataset.animated === "true";
    const canvas = this.el.querySelector("#chart-canvas-" + this.el.id); // unique IDs…
}

// AFTER
mounted() {
    new Chart(this.target('canvas'), {
        data: this.values.points,                     // [1,2,3] — real Array
        options: {animation: this.values.animated},   // real Boolean
    });
}
```

### Recipe: live values make `updated()` trivial

```python
# server: any handler that changes the data
@event_handler()
def set_period(self, value: str = ""):
    self.points_json = json.dumps(self.compute(value))
```

```javascript
updated() {
    // the morph already updated the attribute; this.values reads it LIVE
    this.chart.data.datasets[0].data = this.values.points;
    this.chart.update();
}
```

No snapshotting, no staleness — the values object reads the current DOM
attribute on every access.

### Recipe: a countdown that survives re-renders

```html
<div dj-hook="Countdown" dj-hook-value-deadline='"2026-08-01T00:00:00Z"'>
    <span dj-hook-target="display"></span>
</div>
```

```javascript
window.djust.hooks.Countdown = {
    mounted() {
        this.timer = setInterval(() => {
            const left = new Date(this.values.deadline) - Date.now();
            this.target('display').textContent = formatDuration(left);
        }, 1000);
    },
    destroyed() { clearInterval(this.timer); },
};
```

The server moves the deadline → the morph updates the attribute → the next
tick reads the new value. Nothing else to wire. (Note the JSON-quoted
attribute value — an ISO timestamp parses as a plain string either way, but
quoting makes the intent explicit.)

To *drive* a hook-owned library instance from Python between renders —
"jump to line 42", "highlight the Q3 data point" — pair hooks with
[custom JS commands](js-commands#custom-commands): the hook owns the
instance, a registered command manipulates it, and the server binds the
chain to any event attribute.

### Pre-1.1 hooks (dataset attributes)

Hooks written before typed values (pre-1.1) commonly read raw `data-*`
attributes off `this.el.dataset` and parsed them by hand:

```html
<canvas dj-hook="Chart" data-values='{{ chart_data_json }}'></canvas>
```

```javascript
window.djust.hooks.Chart = {
    mounted() {
        const data = JSON.parse(this.el.dataset.values);
        this.chart = new Chart(this.el, { type: 'line', data });
    },
    updated() {
        this.chart.data = JSON.parse(this.el.dataset.values);
        this.chart.update();
    },
};
```

**This still works, unmodified.** `dj-hook`, the lifecycle callbacks, and
`this.el` haven't changed — `this.el.dataset` is plain DOM, not a djust
API, so nothing about typed values deprecates it. New hooks should still
prefer `dj-hook-value-*` + `this.values` (above): no manual `JSON.parse`,
live reads on `updated()`, and no risk of shadowing the reserved
`this.values` name (see [Reserved names](#hook-instance-api) above) by
also using it for something else.

## TypeScript & editor support

djust ships ambient type declarations for the public `window.djust` surface
(hook lifecycle + `this` context — including `values` / `target()` /
`targets()` — transports, uploads, streaming, model binding, and
`commands.register`). Point your editor at them — no build step needed:

```json
// jsconfig.json
{
    "compilerOptions": { "checkJs": true },
    "include": ["static/**/*.js", "./typings/djust.d.ts"]
}
```

Locate (or copy) the shipped file with:

```bash
python -c "import djust, pathlib; print(pathlib.Path(djust.__file__).parent / 'static/djust/djust.d.ts')"
```

## Example: Chart.js Integration

```python
import json
from djust import LiveView
from djust.decorators import event_handler

class DashboardView(LiveView):
    template_name = 'dashboard.html'

    def mount(self, request, **kwargs):
        self.sales_data = self.fetch_sales()

    def fetch_sales(self):
        # Your own query; a list the chart can plot.
        return [120, 180, 150, 210]

    @event_handler()
    def refresh_data(self):
        self.sales_data = self.fetch_sales()

    def get_context_data(self, **kwargs):
        return {'chart_json': json.dumps(self.sales_data)}
```

```html
<canvas dj-hook="SalesChart" dj-hook-value-chart='{{ chart_json }}'></canvas>
<button dj-click="refresh_data">Refresh</button>

<script>
window.djust.hooks = Object.assign(window.djust.hooks || {}, {
    SalesChart: {
        mounted() {
            const data = this.values.chart;
            this.chart = new Chart(this.el, {
                type: 'bar',
                data: {
                    labels: data.labels,
                    datasets: [{ label: 'Sales', data: data.values }]
                }
            });
        },
        updated() {
            const data = this.values.chart;
            this.chart.data.labels = data.labels;
            this.chart.data.datasets[0].data = data.values;
            this.chart.update();
        },
        destroyed() {
            this.chart.destroy();
        }
    }
});
</script>
```

## Example: Infinite Scroll

```html
<div id="items">
    {% for item in items %}
    <div class="item">{{ item.name }}</div>
    {% endfor %}
</div>
<div dj-hook="InfiniteScroll" dj-hook-value-page="{{ page }}"></div>

<script>
window.djust.hooks = Object.assign(window.djust.hooks || {}, {
    InfiniteScroll: {
        mounted() {
            this.observer = new IntersectionObserver((entries) => {
                if (entries[0].isIntersecting) {
                    // this.values.page is already a Number (JSON-coerced) —
                    // no parseInt needed.
                    const page = this.values.page + 1;
                    this.pushEvent('load_more', { page });
                }
            });
            this.observer.observe(this.el);
        },
        updated() {
            this.observer.observe(this.el);
        },
        destroyed() {
            this.observer.disconnect();
        }
    }
});
</script>
```

## Best Practices

- **Always clean up in `destroyed()`**: Destroy library instances, disconnect observers, remove global listeners, clear timers.
- **Do not create new instances in `updated()`**: Only refresh existing ones. Creating new instances on every re-render causes memory leaks.
- **Pass data via `dj-hook-value-*` attributes**: use `this.values` for JSON-typed data (arrays, objects, numbers, booleans) — no manual `JSON.parse`. Reserve plain `data-*` / `this.el.dataset` for [pre-1.1 hooks](#pre-11-hooks-dataset-attributes) you haven't migrated yet.
- **Use `pushEvent` / `handleEvent`** for server communication, not direct WebSocket calls.
- **Register hooks before mount**: Place hook definitions in `<head>` or before the djust client script. Hooks registered late are picked up on the next DOM patch. Before client.js loads, `window.djust` does not exist yet, so use `window.DjustHooks = Object.assign(window.DjustHooks || {}, { ... })` there. After client.js, merge into `window.djust.hooks` with `Object.assign` rather than assigning a new object, which would discard hooks already registered (including the built-in `CursorOverlay` and any `{% colocated_hook %}` hooks).

## Colocated Hooks (v0.5.0, Phoenix 1.1 parity)

Write hook JavaScript inline with the template that uses it, instead of in a separate file. The `{% colocated_hook %}` block tag emits a `<script type="djust/hook" data-hook="NAME">` tag; the djust client walks these on init and after each VDOM morph and registers each body as `window.djust.hooks[NAME]`.

```django
{% load live_tags %}

{% colocated_hook "Chart" %}
    hook.mounted = function() {
        const data = this.values.data;
        this.chart = new Chart(this.el, { type: 'line', data });
    };
    hook.updated = function() {
        this.chart.data = this.values.data;
        this.chart.update();
    };
    hook.destroyed = function() {
        this.chart.destroy();
    };
{% endcolocated_hook %}

<canvas dj-hook="Chart" dj-hook-value-data='{{ chart_data_json }}'></canvas>
```

**Convention**: your body assigns callbacks to a local `hook` object. The client wraps the body in an IIFE factory (`(function() { const hook = {}; <body>; return hook; })()`) and stores the result under `data-hook`.

### Namespacing

Two different views that each define `Chart` will collide if both register the hook globally. Opt into namespacing to prefix each hook with the view's module + class name:

```python
# settings.py
DJUST_CONFIG = {"hook_namespacing": "strict"}
```

With strict namespacing on, `{% colocated_hook "Chart" %}` inside a template rendered by `myapp.views.DashboardView` emits `data-hook="myapp.views.DashboardView.Chart"`, and the `<canvas dj-hook="myapp.views.DashboardView.Chart">` selector must match. Set `dj-hook` to the same namespaced name — typically this is done via a Django context variable or a helper that knows the current view.

Per-tag opt-out when a hook truly is global (e.g. a page-wide tooltip handler):

```django
{% colocated_hook "Tooltip" global %}
    hook.mounted = function() { /* ... */ };
{% endcolocated_hook %}
```

The `global` keyword emits the bare `data-hook="Tooltip"` regardless of the `hook_namespacing` setting.

### Security

- The hook body is **template-author JS**, not user input. Never interpolate request data into the body.
- The `{% colocated_hook %}` tag escapes `</script>` / `</SCRIPT>` in the body to prevent premature tag close.
- When the request has a CSP nonce (`request.csp_nonce`, set by django-csp), the tag carries it and the client runs the body in a script element with the same nonce, so a strict nonce CSP needs neither `'unsafe-eval'` nor `'unsafe-inline'`. Without a nonce the client evaluates the body with `new Function(...)`, which needs `'unsafe-eval'`. A hook first rendered by a socket update is registered with the nonce of the page that loaded it; render the tag on the initial page if the policy rotates nonces.
- Every emitted script carries a `/* COLOCATED HOOK: <name> */` banner so security auditors can grep for them.

## Ignored Attributes (`dj-ignore-attrs`) — v0.5.0

Mark specific HTML attributes as client-owned so VDOM `SetAttr` patches skip them. Essential for browser-native elements (`<dialog>`, `<details>`) and third-party JS libraries that manage attributes the server doesn't know about. Phoenix 1.1 calls this `JS.ignore_attributes/1`.

```html
<!-- Browser manages <dialog open> via .showModal() / .close() -->
<dialog id="confirm" dj-ignore-attrs="open">...</dialog>

<!-- Third-party library manages data-state + aria-expanded -->
<div dj-ignore-attrs="data-state, aria-expanded" ...></div>
```

**Format**: comma-separated list of attribute names. Whitespace around commas is tolerated.

**Scope**: on incremental patches only `SetAttr` is skipped (`RemoveAttr` still applies); full morphs neither set nor remove the listed attributes. Users explicitly opt in per element; there is no framework-level default list. Rationale: the browser-native case is common, but silent magic is a bigger cost than the typing.
