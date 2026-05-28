# ADR-020: `dj-island` — Opaque-Subtree Attribute for React, Vue, Svelte, and Third-Party-Widget Interop

**Status**: Proposed
**Date**: 2026-05-28
**Deciders**: Project maintainers
**Related**:
- [ADR-018](018-sticky-child-state-persistence.md) — Sticky-child LiveView state persistence; `dj-island` composes with `dj-sticky-view` to give "third-party widget with state surviving djust navigation"
- [ADR-019](019-liveview-native.md) — LiveView Native; ADR-020 is the **web** counterpart to ADR-019's mobile-native interop story (same shape, different boundary)
- [`docs/strategy-sessions/2026-05-28-react-interop.md`](../strategy-sessions/2026-05-28-react-interop.md) — research + alternatives evaluation that informed this ADR
- [`crates/djust_vdom/src/lib.rs`](../../crates/djust_vdom/src/lib.rs) — `Patch` enum + `diff()` walker, the dispatch point for the opacity rule
- [`python/djust/static/djust/src/12-vdom-patch.js`](../../python/djust/static/djust/src/12-vdom-patch.js) — client-side patch applier; receives the lifecycle event additions
- [`python/djust/static/djust/src/31-ignore-attrs.js`](../../python/djust/static/djust/src/31-ignore-attrs.js) — `dj-ignore-attrs`; the per-attribute precedent this ADR generalizes to per-subtree
- [`python/djust/static/djust/src/45-child-view.js`](../../python/djust/static/djust/src/45-child-view.js) — `dj-sticky-view`; the mount-boundary primitive this composes with
- Phoenix LiveView prior art: `phx-hook` + `phx-update="ignore"` — the Elixir equivalent we mirror in Python shape

---

## Summary

djust grows a new attribute, **`dj-island`**, that marks a subtree as
opaque to the per-event VDOM differ. The Rust `djust_vdom` crate skips
descent into the children of any `dj-island` element; `SetAttr` patches
on the island element itself still apply (this is the channel through
which server state flows into the island as `data-props` JSON).

Two lifecycle events fire from the client-side patch applier:
**`djust:island-init`** (when an island first appears in the DOM) and
**`djust:island-unmount`** (just before djust removes the element).
User code wires these to its framework of choice — `createRoot` +
`unmount` for React, `createApp` + `unmount` for Vue, `new Component` +
`$destroy` for Svelte, framework-equivalents elsewhere.

A companion package — **`djust-react`** (out-of-tree npm package,
~150 lines) — wraps the React lifecycle on top of these events. Core
djust has zero React dependency; the same `dj-island` primitive serves
Vue, Svelte, Solid, Lit, and any third-party widget that owns its own
DOM subtree (rich text editors like Lexical/TipTap/ProseMirror,
charting libraries, data grids, drag-drop, 3D, maps).

The pattern is borrowed from Phoenix LiveView's `phx-update="ignore"` +
`phx-hook` shape — same conceptual move ("declare this region as
not-my-DOM-to-touch and route events through a stable API"), djust's
implementation.

## Context

### What we have today

djust today has three primitives that partially address the third-party
interop problem, but each in a different shape and none of them are
sufficient by themselves:

| Primitive | What it protects | What it doesn't |
|---|---|---|
| `dj-ignore-attrs="open,..."` ([src/31-ignore-attrs.js:30](../../python/djust/static/djust/src/31-ignore-attrs.js)) | Per-attribute opt-out: VDOM `SetAttr` patches skip these attribute names. Phoenix 1.1 parity (`JS.ignore_attributes/1`). | Doesn't protect children. The differ still emits `InsertChild` / `RemoveChild` / `MoveChild` / `SetText` / `InsertSubtree` / `RemoveSubtree` against descendants. |
| `dj-sticky-view` ([src/45-child-view.js:25](../../python/djust/static/djust/src/45-child-view.js)) | Stash-and-restore an entire subtree across `live_redirect` mount swaps. The framework preserves the DOM through mount/unmount. | Only protects the **mount boundary**. Per-event VDOM patches still pass through the subtree. |
| `dj-sticky-scroll` ([src/38-dj-sticky-scroll.js](../../python/djust/static/djust/src/38-dj-sticky-scroll.js)) | Preserves scroll position across re-renders. | Orthogonal to interop. |

**The gap**: no "this element's children are opaque to the per-event
VDOM differ" attribute. That's the primitive third-party widget interop
needs — React's reconciler will throw if morphdom-style reconciliation
mutates its internal DOM; rich text editors corrupt user input state;
charting libraries get visual artifacts. The widget owns its DOM; the
framework must respect that ownership.

### Why this needs to be a framework primitive (not just a library)

A user can mount a React root inside a djust template TODAY by hand
(`createRoot(document.querySelector('#chart')).render(...)`). It works
once. On the next VDOM patch from djust, the differ walks into the
chart subtree, sees DOM nodes React created, doesn't recognize them
against the server VDOM tree, and emits patches to "fix" them. React's
internal reconciler tree no longer matches the DOM; the next React
render crashes.

The user cannot fix this with a library; the opacity is a property of
the VDOM differ's *traversal*. Only the framework can decide not to
descend.

### Real-world pressure for this primitive

- Charting libraries (Recharts, Chart.js wrappers, ECharts, Plotly,
  Highcharts, deck.gl)
- Rich text editors (Lexical, TipTap, ProseMirror as React, Slate,
  Quill)
- Data grids (AG Grid, TanStack Table, MUI DataGrid)
- 3D / map widgets (react-three-fiber, react-map-gl, Mapbox GL JS)
- Drag-and-drop (dnd-kit, react-beautiful-dnd, Sortable.js)
- Internal company design systems built on React / Vue / Svelte
- Teams migrating *to* djust incrementally — the old screens stay
  React; new screens are djust; gradual rollout requires both to
  coexist on shared pages

The marketing position "you don't need React to be reactive" is for
*new* work. The "I have a chart library that only exists as a React
component" case must be answered or the migration funnel breaks.

## Decision

**Add `dj-island` to djust core as a v1.1 candidate feature**, with the
following semantics:

1. **Differ behavior** (Rust, `crates/djust_vdom/src/lib.rs`):
   - When the VDOM differ walks the previous and next trees, it checks
     each node for the presence of the `dj-island` attribute.
   - If `dj-island` is present, the children of that node are treated as
     **identical-by-assumption**: no recursive descent, no
     `InsertChild` / `RemoveChild` / `MoveChild` / `SetText` /
     `InsertSubtree` / `RemoveSubtree` patches are emitted for any
     descendant of the island.
   - `SetAttr` and `RemoveAttr` patches on the island element *itself*
     continue to apply normally. This is how new props flow from
     server to island.
2. **Client behavior** (JavaScript,
   `python/djust/static/djust/src/12-vdom-patch.js`):
   - Already a no-op in the happy path since the differ won't emit
     child-level patches under an island.
   - Add a defensive guard: if a `RemoveChild` / `InsertChild` /
     `SetText` patch ever targets a node inside an island (e.g., from a
     buggy server, a wire-protocol corruption, or a third-party
     middleware), the client drops the patch with a `console.warn`
     under `globalThis.djustDebug`.
3. **Lifecycle events** (new, dispatched from the client patch applier
   and the initial-mount walker):
   - **`djust:island-init`** (`CustomEvent`, bubbles) fires when an
     element with `dj-island` first enters the DOM — on initial page
     render, when an island is inserted as part of an `InsertSubtree`
     patch, or when a parent's mount/redirect brings one in.
   - **`djust:island-unmount`** (`CustomEvent`, bubbles) fires
     synchronously, just before djust removes a `dj-island` element
     (when the parent's `RemoveChild` / `RemoveSubtree` resolves to
     this node). User code uses this to call `root.unmount()` / framework
     equivalent before the DOM disappears.
4. **Optional Django template tag** (`python/djust/templatetags/djust_islands.py`):
   - `{% react_island name="MyChart" props=data on_bar_click="bar_clicked" %}`
   - Expands to canonical
     `<div dj-island data-react="MyChart" data-props="{json}" dj-on-bar-click="bar_clicked"></div>`
   - Pure convenience; the raw attribute form remains the canonical API.

### Companion package: `djust-react` (out-of-tree)

A ~150-line npm package — **NOT** bundled into core djust; published
separately so core has zero React dependency:

```javascript
// djust-react/index.js
import { createRoot } from 'react-dom/client';

export function mountReactIsland(el, Component) {
    const root = createRoot(el);
    const render = () => {
        const props = el.dataset.props ? JSON.parse(el.dataset.props) : {};
        root.render(<Component {...props} />);
    };
    render();

    const obs = new MutationObserver(() => render());
    obs.observe(el, { attributes: true, attributeFilter: ['data-props'] });

    el.addEventListener('djust:island-unmount', () => {
        obs.disconnect();
        root.unmount();
    }, { once: true });
}

export function dispatchToDjust(el, name, detail) {
    el.dispatchEvent(new CustomEvent(name, { detail, bubbles: true }));
}
```

User wiring (once per page):

```javascript
import { mountReactIsland } from 'djust-react';
import { MyChart } from './components/MyChart';

document.addEventListener('djust:island-init', (ev) => {
    if (ev.target.dataset.react === 'MyChart') {
        mountReactIsland(ev.target, MyChart);
    }
});
```

The same pattern, with a different inhabitant: `djust-vue`, `djust-svelte`,
`djust-solid`, `djust-lit` — community packages, ~150 lines each.

## Architecture

### The boundary

```
┌─────────────────────────────────────────────────────────┐
│ djust LiveView template                                 │
│                                                         │
│   <div dj-root>                                         │
│     <h1>Dashboard</h1>                                  │
│     ┌─────────────────────────────────────────────────┐ │
│     │  <div dj-island                                 │ │
│     │       data-react="MyChart"                      │ │
│     │       data-props='{"series": [1,2,3]}'          │ │
│     │       dj-on-bar-click="bar_clicked">            │ │
│     │   ╔═══════════════════════════════════════════╗ │ │
│     │   ║                                           ║ │ │
│     │   ║   ← OPAQUE TO djust VDOM DIFF →           ║ │ │
│     │   ║                                           ║ │ │
│     │   ║   React tree lives here. djust treats     ║ │ │
│     │   ║   the contents as identical-by-assumption ║ │ │
│     │   ║   and never emits child patches.          ║ │ │
│     │   ║                                           ║ │ │
│     │   ║   React owns: layout, render lifecycle,   ║ │ │
│     │   ║   DOM mutations, internal state.          ║ │ │
│     │   ║                                           ║ │ │
│     │   ╚═══════════════════════════════════════════╝ │ │
│     │  </div>                                         │ │
│     └─────────────────────────────────────────────────┘ │
│     <p>Click count: {{ count }}</p>                     │
│   </div>                                                │
└─────────────────────────────────────────────────────────┘

Flow of data:
  ↓ server-to-island: SetAttr(data-props=...) on the island
  ↑ island-to-server: dj-on-* catches CustomEvent bubbling up
```

### Data flow: server → island

1. Server-side state changes (`self.chart_data = [...]`).
2. `render_with_diff()` re-renders the parent template; new
   `data-props='{"series":[4,5,6]}'` value gets written into the island
   element's attribute.
3. Rust differ compares old VDOM to new VDOM, encounters the
   `dj-island` element, and emits `SetAttr(path=[...], name="data-props",
   value="...")` — and emits *nothing* for descendants of the island
   (the rule).
4. Client patch applier applies the `SetAttr`. The island element's
   `data-props` attribute changes.
5. The user's `MutationObserver` (in the `djust-react` helper) fires;
   re-renders the React tree with the new props.

### Data flow: island → server

1. User clicks a bar in the React chart.
2. React handler does
   `el.dispatchEvent(new CustomEvent('bar-click', { detail: { index: 2 }, bubbles: true }))`
   on a node inside the island.
3. The CustomEvent bubbles up the DOM and hits the island element,
   which has `dj-on-bar-click="bar_clicked"`.
4. djust's standard `dj-on-<event>` handler intercepts the bubble,
   serializes `event.detail` as the handler kwargs, sends a WS message
   to the server.
5. Server-side `@event_handler def bar_clicked(self, index=0, **kwargs)`
   runs; mutates state; triggers another re-render cycle.

### Composition with `dj-sticky-view` (ADR-018)

```
<div dj-view dj-sticky-view="dashboard-chart" dj-sticky-root>
  <div dj-island data-react="MyChart" data-props="{...}">
    [React tree — persistent across BOTH per-event diffs AND mount swaps]
  </div>
</div>
```

- `dj-sticky-view` (ADR-018): subtree survives `live_redirect` mount
  swaps. The DOM (including the React tree's mounted state) is stashed
  to a module-local cache and restored on the new view's mount.
- `dj-island` (this ADR): subtree is opaque to per-event VDOM patches
  within a single mount. React owns its own children.

**Together**: a React component (e.g., a complex form-editing widget) can
maintain its internal state — text caret position, scroll, focus,
in-progress validation — across:
- per-event djust state mutations (handled by `dj-island`);
- `live_redirect` to a different LiveView (handled by `dj-sticky-view`);
- WebSocket reconnect (handled by the existing sticky-child reconnect
  state machine, ADR-018).

This is the highest-value composition and the reason both ADRs exist as
separate primitives that compose, rather than a single conflated
attribute.

## Consequences

### Positive

- **Closes a real interop gap** that today blocks teams from embedding
  React (or any framework that owns its own DOM) inside djust.
- **Framework-agnostic by design.** The same primitive serves React,
  Vue, Svelte, Solid, Lit, ProseMirror, Lexical, Chart.js, AG Grid,
  Mapbox GL — anything that owns a DOM subtree.
- **Composes with existing primitives** (`dj-sticky-view`,
  `dj-ignore-attrs`) without breaking either. Each primitive owns a
  distinct axis (per-attribute / per-subtree-per-event / per-subtree-
  across-mount).
- **Phoenix LiveView parity.** Phoenix's `phx-update="ignore"` +
  `phx-hook` is the recognized shape for this in the Elixir community;
  cross-language parity makes the migration story bidirectional
  (Phoenix devs can read djust apps; djust devs can read Phoenix apps).
- **Zero React dependency in core.** Helpers ship out-of-tree
  (`djust-react`, `djust-vue`, etc.); core djust stays framework-agnostic.
- **Unblocks the migration funnel.** A team with a half-React, half-djust
  codebase can incrementally adopt djust without a "rewrite everything in
  pure djust" prerequisite.

### Negative

- **One more attribute on the contract.** `dj-root`, `dj-view`,
  `dj-sticky-view`, `dj-sticky-scroll`, `dj-ignore-attrs`, and now
  `dj-island` — six framework-controlled attributes. The cognitive
  load is real; the docs need to explain each clearly.
- **Differ complexity.** The "don't descend" rule is one branch in the
  recursive diff walk, but it has interaction surface with `key`
  attributes, with nested islands, with `dj-if` boundary markers
  inside (must be illegal — see Iteration plan), and with diff path
  computation for sibling nodes. Estimate +0.5 wk over the naive
  implementation.
- **Doc-snippet executable-verification gap.** Like every framework
  attribute, drift between "what the docs say `dj-island` does" and
  "what the differ actually does" is a real risk. Mitigation: a
  property test for `differ(parent_with_island) emits no patches under
  island regardless of inner mutations`.
- **MutationObserver dependency in the helper.** The `djust-react` helper
  depends on `MutationObserver` to detect `data-props` changes. Available
  in every browser djust supports (IE11 is not a target); no risk.
- **JSON-serializability constraint on `data-props`.** Same constraint
  as React-from-outside generally: no functions, no DOM refs, no
  un-`toJSON`'d Date objects. Standard; documentable.

### Neutral

- **Naming.** `dj-island` is deliberately framework-agnostic. The
  attribute is not `dj-react` because the same primitive serves Vue,
  Svelte, Solid, and non-framework widgets. Doc + naming convention
  should not suggest React-specificity.

## Iteration plan

### Iter A — differ primitive + lifecycle events (~1 week)

1. Rust differ: add `dj-island` attribute check in the recursive walk
   in `crates/djust_vdom/src/lib.rs`. If the attribute is present on
   a node, set a flag that causes the children-diff loop to be skipped
   for that node. Property tests:
   - `inner-text-mutation`: server inner-text change inside an island
     emits no patches → ✅
   - `inner-attribute-mutation`: server-side `<div class="...">` change
     inside an island emits no patches → ✅
   - `outer-attribute-mutation`: server-side `data-props` change on the
     island itself DOES emit a `SetAttr` patch → ✅
   - `island-replaced`: if the parent decides to swap the whole island
     for a different element, that's an outer-level `Replace` / `Remove`
     + `Insert`, which DOES apply (per-subtree primitive lives one
     level inside) → ✅
2. Client patch applier: defensive guard against bypass; emit
   `djust:island-init` on first encounter; emit
   `djust:island-unmount` before removal.
3. Initial-mount walker: scan the initial server-rendered HTML for
   `dj-island` elements, emit `djust:island-init` for each — covers the
   "first paint" case (an island that exists at SSR time, not just one
   inserted later by an `InsertSubtree` patch).
4. Tests in `python/djust/tests/test_island_*.py`:
   - End-to-end with a mock React-like child that mutates its own DOM
     mid-test; verify djust does not corrupt it.
   - Composition with `dj-sticky-view`: react root stays mounted across
     `live_redirect`.

### Iter B — Django template tag + docs (~3 days)

1. `python/djust/templatetags/djust_islands.py` — `{% react_island %}`,
   `{% vue_island %}`, `{% svelte_island %}`, `{% widget_island %}` tags;
   all expand to the canonical `<div dj-island ...>` form.
2. Docs page `docs/website/guides/component-interop.md`:
   - The `dj-island` primitive itself
   - The `djust-react` companion (out-of-tree)
   - Step-by-step: embedding a Recharts chart, a Lexical editor, a
     mapbox-gl widget
   - The Phoenix LiveView correspondence (for readers coming from there)
3. CHANGELOG entry: feat(vdom): `dj-island` framework-agnostic
   opaque-subtree attribute (closes the third-party widget interop
   gap).

### Iter C — `djust-react` companion package (out-of-tree, ~3 days)

1. Repository `djust-org/djust-react` (new repo, separate from core).
2. ~150 lines of source: `mountReactIsland`, `dispatchToDjust`,
   `useDjustEvent` (React hook), unit tests against a JSDOM React 19
   harness.
3. npm publish under `@djust-org/djust-react`. Semver independent of
   core djust. Core djust v1.1 + `djust-react` v0.1 is the first
   matrix.
4. Companion guides for `djust-vue` (Vue 3), `djust-svelte` (Svelte 5)
   — community-driven; the docs page shows the pattern.

### Out of scope for v1.1

- SSR / hydration of React from Python (would require a Node sidecar;
  kills the "one stack, one deploy" claim — hard pass).
- Bundling `djust-react` into core djust (stays out-of-tree).
- A "translate React components to djust LiveComponents automatically"
  build step (nope; deliberate non-goal).

## Verification

The Iter A acceptance gate consists of three property tests + one
end-to-end integration test:

1. **`test_island_subtree_opaque_to_inner_mutations`** (Rust property
   test in `crates/djust_vdom/tests/`):
   - For any `VNode` tree containing a `dj-island` node, mutating
     inner-text or inner-attributes within the island and re-diffing
     emits **zero** child patches under the island.
2. **`test_island_outer_attrs_continue_to_diff`** (Rust):
   - For any `VNode` tree containing a `dj-island` node, mutating the
     `data-props` attribute on the island itself emits exactly one
     `SetAttr` patch with the new value.
3. **`test_island_lifecycle_events_fire_correctly`** (JS):
   - Initial-mount walker fires `djust:island-init` once per island in
     the SSR'd HTML.
   - Patch applier fires `djust:island-init` when an island is part of
     an `InsertSubtree` patch.
   - Patch applier fires `djust:island-unmount` synchronously before
     removing an island via `RemoveChild` / `RemoveSubtree`.
4. **`test_react_island_end_to_end`** (integration; uses JSDOM + React
   19's `act()`):
   - Embed a real React component inside a djust LiveView using
     `djust-react`'s `mountReactIsland`.
   - Send a server-side state mutation; verify the React component
     receives new props and re-renders.
   - Fire a `CustomEvent` from inside React; verify the djust event
     handler runs.
   - `live_redirect` to a different LiveView; verify the React component
     unmounts cleanly (no React reconciler crash).

### Sticky-child compose test

A separate test verifies the `dj-sticky-view` + `dj-island` composition:
mount a React component inside a sticky child, mutate React-internal
state (caret position in a text editor), trigger a `live_redirect`, and
verify on the new LiveView's mount that the React tree's internal state
is preserved. This is the headline integration story; if it fails, the
ADR's primary value proposition is broken.

## Alternatives considered

### A. Custom Element wrapper (`r2wc` / `preact-custom-element`)

Wrap each React component in a custom element; djust treats the CE as
opaque HTML.

- **Pros**: Works today without any djust change; standards-compliant;
  framework-neutral; design-system publishers can vendor wrappers once.
- **Cons**: One wrapper per component; props limited to attribute-
  serializable values (same as `dj-island`'s `data-props` JSON
  constraint); event-routing via `CustomEvent` is standard; the wrapper
  must be defined before the page mounts (collides with djust's
  progressive-enhancement story); doesn't compose as cleanly with
  `dj-sticky-view`.
- **Verdict**: Document this as the **supported-today story** until
  `dj-island` ships in v1.1. Best for **component-library publishers**
  (vendor wraps once; consumer uses the CE like any HTML element). NOT
  the recommended default for in-app use because the per-component
  wrapping overhead doesn't pay off when one app has 20+ React widgets.

### B. Mount React roots without an opacity primitive

User writes `<div data-react="MyChart">` and calls `createRoot(el)`
client-side. Without a djust-side primitive marking the element as
opaque.

- **Pros**: Smallest possible client surface area; no new attribute.
- **Cons**: **Blocked.** djust's per-event differ walks into the React
  tree on the next state mutation and corrupts the DOM. React's
  reconciler crashes on the subsequent render. This is not a
  "documentable workaround" — it's a hard failure.
- **Verdict**: Discarded.

### C. Iframe isolation

Each React component runs in its own iframe.

- **Pros**: Total separation; bulletproof; CSP-safe; allows multiple
  React versions on the same page; works for any framework, any
  vintage.
- **Cons**: Heavy (each iframe is a new browsing context); styling
  crosses iframe boundary awkwardly; events need `postMessage`; auth
  cookies / same-origin policy interact with embed shape.
- **Verdict**: Document as **escape hatch** for hard-isolation cases
  (multi-version React; untrusted code; jQuery legacy widget that
  pollutes globals). NOT the default; NOT a framework-supported feature.

### D. Server-side React rendering via Node sidecar

djust spawns a Node process that renders React server-side; the HTML
flows into the djust template; hydrates client-side.

- **Pros**: SSR works; first paint is correct; SEO; doesn't need a
  djust-side opacity primitive (the rendered tree is just HTML).
- **Cons**: Introduces a **Node dependency** in production deployments.
  Breaks the "one stack, one deploy, one stack trace, one language"
  pitch that's the core of djust's positioning. Doubles the operational
  surface (now you have Python + Node both running). Adds a deploy
  artifact (Node bundle).
- **Verdict**: **Hard pass.** This option is incompatible with djust's
  fundamental architecture and shouldn't ship.

### E. `dj-island` (this ADR)

The recommended primitive, scoped above.

- **Pros**: Framework-agnostic by design (serves React, Vue, Svelte,
  Lit, third-party widgets); composes with `dj-sticky-view` and
  `dj-ignore-attrs`; Phoenix LiveView parity; ~1 week effort for the
  core change; helpers ship out-of-tree so core has zero framework
  dependency.
- **Cons**: Adds an attribute to the framework contract surface
  (mitigation: clear docs + naming convention).
- **Verdict**: **Accepted as the v1.1 candidate.** This ADR is the
  decision.

### F. Wait — defer to v1.2 or later

Don't ship anything for component interop in v1.1; let users solve it
with Approach A (Custom Element wrappers) and revisit later.

- **Pros**: Smallest v1.1 scope.
- **Cons**: The migration funnel keeps leaking — teams who want to
  adopt djust incrementally hit the React boundary and either (a)
  accept a Custom Element wrapping overhead per component, (b) build
  their own ad-hoc `dj-island`-shaped workaround that fights the
  framework, or (c) give up. v1.1 is the natural slot for an
  interop-themed feature given the v1.1 brainstorm already names
  HTMX-interop and DRF-interop.
- **Verdict**: Rejected. The Approach A workaround stays in the docs as
  the supported-today story for users with v1.0 needs, but the
  framework primitive ships in v1.1.

---

## Provenance

Generated 2026-05-28 in response to user goal "determine the best way
to interact with React components using djust." Builds on the research
captured in [`docs/strategy-sessions/2026-05-28-react-interop.md`](../strategy-sessions/2026-05-28-react-interop.md).
Propose adding row **E7** to the
[v1.1 brainstorm matrix](../strategy-sessions/2026-05-19-v1.1-brainstorm.md)
to formally slot this work alongside E1 (DRF interop) and E6 (HTMX
interop) at score-tier 3.
