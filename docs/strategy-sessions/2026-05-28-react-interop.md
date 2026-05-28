# Strategy Session — React (and other-framework) component interop

**Date**: 2026-05-28
**Status**: Recommendation — not yet a roadmap commitment.
**Trigger**: User question — "what's the best way to interact with React components using djust?"
**Outcome**: Recommend `dj-island` framework primitive (v1.1 candidate) + `djust-react` companion helper (out-of-tree) + a layered strategy that also covers Vue, Svelte, Solid, and rich-text-editor-style "this subtree owns its own DOM" cases.

---

## The honest framing

djust's marketing position is "you don't need React to be reactive." That's
aspirational. The real world has:

- Charting libraries (Recharts, Chart.js wrappers, ECharts, Plotly)
- Rich text editors (Lexical, TipTap, ProseMirror as React)
- Data grids (AG Grid, TanStack Table, MUI DataGrid)
- 3D / canvas / map widgets (react-three-fiber, react-map-gl, deck.gl)
- Drag-and-drop (dnd-kit, react-beautiful-dnd)
- Internal company design systems built on React
- Teams migrating *to* djust incrementally — the old screens stay React

A framework that refuses to interop with React is a framework that loses
the migration funnel. The "no JS framework needed" pitch is for *new*
work. The "I have a chart library that only exists as a React component"
case must be answered.

This applies to Vue / Svelte / Solid / Lit just as much as React. The
recommendation here is **framework-agnostic** — the primitive is "this
subtree is opaque to djust's VDOM diff; some other thing owns it."

## What djust has today

Three primitives that *almost* solve this, but each in a different shape:

| Primitive | What it does | What it doesn't |
|---|---|---|
| `dj-ignore-attrs="open,...";` ([src/31-ignore-attrs.js](../../python/djust/static/djust/src/31-ignore-attrs.js)) | Per-attribute opt-out: VDOM `SetAttr` patches skip these attribute names. Phoenix 1.1 parity (`JS.ignore_attributes/1`). | Doesn't protect children. The differ still emits `InsertChild`/`RemoveChild`/`SetText` against descendants. |
| `dj-sticky-view` ([src/45-child-view.js](../../python/djust/static/djust/src/45-child-view.js)) | Stash-and-restore an entire subtree across `live_redirect` mount swaps. The framework preserves the DOM through mount/unmount. | Only protects the **mount boundary**. Per-event VDOM patches still pass through the subtree. |
| `dj-sticky-scroll` ([src/38-dj-sticky-scroll.js](../../python/djust/static/djust/src/38-dj-sticky-scroll.js)) | Preserves scroll position. | Niche; not relevant to component interop. |

**The gap**: no "this element's children are opaque to the per-event
VDOM differ" attribute. That's the primitive React interop needs — the
React tree manages its own children, and djust's morphdom-style
reconciliation will corrupt it.

## Approaches considered

### A. Custom Element wrapper (works today, no new primitive)

Wrap each React component in a custom element via `r2wc` / `preact-custom-element`:

```django
<my-chart data='{{ chart_data|json }}'></my-chart>
```

**Pros**: standards-compliant, framework-neutral, works with djust today
(it's just an HTML element).

**Cons**: every React component needs an individual wrapper. Props limited
to attribute-serializable values (JSON-encoded). Events via `CustomEvent`.
Wrappers must be defined *before* djust mounts the page, which collides
with djust's progressive-enhancement story.

**Verdict**: viable, but not the recommended default. Best for component
libraries (a vendor wraps once, consumers import the element).

### B. Mount React roots inside djust-managed DOM (the naive shape)

User writes `<div data-react="MyChart" data-props="..."></div>` in their
template, hooks `mountReactIsland(el, MyChart)` client-side. Without a
djust-side primitive, this **breaks** on the next VDOM patch — djust's
differ will morph the React-rendered DOM and React's reconciler will
throw on next render.

**Verdict**: blocked on the missing opacity primitive.

### C. Iframe isolation

Each component runs in its own iframe. Hard isolation; bulletproof.

**Pros**: total separation, works for any framework, CSP-safe, even
allows multiple React versions on the same page.

**Cons**: heavy (each iframe is a new browsing context); styling crosses
the iframe boundary awkwardly; events need `postMessage`; not what most
teams want.

**Verdict**: keep as an escape hatch documented for the "I have a
30-year-old jQuery plugin" or "I need to run two React versions" cases.
Not the default.

### D. Server-side React (SSR + hydration via Node sidecar)

djust calls into Node to render React server-side, sends the HTML, then
hydrates client-side.

**Verdict**: **hard pass.** Defeats djust's "one stack, one deploy, one
stack trace" claim. Introduces a Node dependency that breaks the
single-Python-process deployment story.

### E. **`dj-island` framework primitive + `djust-react` companion** (recommended)

A new attribute marks a subtree as opaque to the VDOM differ:

```django
<div dj-island
     data-react="MyChart"
     data-props='{{ chart_data|json }}'
     dj-on-bar-click="bar_clicked">
</div>
```

Differ behavior under `dj-island`:

1. **Per-event VDOM patches** (`InsertChild` / `RemoveChild` / `MoveChild`
   / `SetText` / `InsertSubtree` / `RemoveSubtree`) targeting *descendants*
   of a `dj-island` element are **dropped**. djust does not touch the
   inhabitant's DOM.
2. **`SetAttr` patches on the island element itself** continue to apply.
   This is how new props flow from server → island: server re-renders
   the parent template, `data-props` attribute changes, client-side
   MutationObserver picks it up, calls the inhabitant's "new props" hook.
3. **Event routing**: `dj-on-<event-name>="handler"` on the island catches
   `CustomEvent`s bubbling from inside (the standard way React components
   communicate with non-React parents). This is just the existing
   `dj-on` mechanism applied to a CustomEvent.
4. **Lifecycle**: when djust removes a `dj-island` element (via
   `RemoveChild` against the *parent* — that patch still applies normally),
   the inhabitant gets a `djust:island-unmount` event to clean up the
   React root before the element is detached.
5. **Composes with `dj-sticky-view`**: an island under a sticky-child
   LiveView survives both per-event diffs AND `live_redirect` mount swaps.
   That's the "React component with persistent state across navigation"
   shape — the highest-value composition.

**Companion helper (`djust-react`, out-of-tree)**:

A ~150-line module published separately (NOT bundled into core djust):

```javascript
// djust-react/index.js
import { createRoot } from 'react-dom/client';

const ROOTS = new WeakMap();

export function mountReactIsland(el, Component) {
    const root = createRoot(el);
    ROOTS.set(el, root);

    const render = () => {
        const props = el.dataset.props ? JSON.parse(el.dataset.props) : {};
        root.render(<Component {...props} />);
    };

    render();

    // Re-render when data-props changes (djust SetAttr patches).
    const obs = new MutationObserver(() => render());
    obs.observe(el, { attributes: true, attributeFilter: ['data-props'] });

    // Clean up when djust removes the island.
    el.addEventListener('djust:island-unmount', () => {
        obs.disconnect();
        root.unmount();
        ROOTS.delete(el);
    }, { once: true });
}

// Helper for React → djust event flow.
export function dispatchToDjust(el, name, detail) {
    el.dispatchEvent(new CustomEvent(name, { detail, bubbles: true }));
}
```

User wires up once per page:

```javascript
import { mountReactIsland } from 'djust-react';
import { MyChart } from './components/MyChart';

document.addEventListener('djust:island-init', (ev) => {
    if (ev.target.dataset.react === 'MyChart') {
        mountReactIsland(ev.target, MyChart);
    }
});
```

The `djust:island-init` event fires from djust core when it encounters a
`dj-island` element it doesn't recognize — gives the user code a hook
to wire up the inhabitant.

---

## Recommended layered strategy

| Layer | Audience | Mechanism |
|---|---|---|
| 1 — Core primitive | Framework authors / interop tooling | `dj-island` attribute in djust core (new). VDOM differ respects opaque-subtree boundary. Ships in v1.1 candidate. |
| 2 — React helper | Teams embedding React widgets | `djust-react` npm package (~150 lines). Wraps `createRoot` + props sync + lifecycle. Out-of-tree; semver independent of djust core. |
| 3 — Compose with sticky-child | Teams whose React widget has internal state that must survive djust navigation | `<div dj-sticky-view="chart-1" dj-island data-react="MyChart" data-props="...">` — both primitives compose, sticky preserves across mount, island preserves across diff. |
| 4 — Custom element vendoring | Component-library publishers | Vendor wraps their React components into custom elements (`r2wc`, `lit-react`, `preact-custom-element`). Consumer just uses `<my-component>` like any HTML element. No `dj-island` needed; the CE is opaque by definition. |
| 5 — Iframe escape hatch | Hard-isolation cases (multi-version React; untrusted code; jQuery legacy) | Document the pattern; no framework support needed. Iframe is already a black box. |

**The user picks the layer that matches their case**, but all five share
the same djust-side primitive: a way to mark a region as opaque. Layer 1
is the only one djust ships. Everything else is documentation or
out-of-tree code that depends on the primitive.

## Concrete v1.1 proposal

**Scope of work for djust core:**

1. New attribute: `dj-island` (boolean, attribute-presence semantics —
   same as `dj-root` / `dj-view`).
2. Differ changes (in [`crates/djust_vdom/`](../../crates/djust_vdom/)):
   - When walking the previous + next VDOM trees, if a node has
     `dj-island`, treat its children as identical-by-reference (no
     descent into them).
   - Still emit `SetAttr` patches on the island element itself.
3. Client patch-applier changes ([`src/12-vdom-patch.js`](../../python/djust/static/djust/src/12-vdom-patch.js)):
   - Already a no-op since the differ won't emit child patches under an
     island — but add a defensive guard so a buggy server can't bypass.
4. Lifecycle events fired from the morphdom hook in core:
   - `djust:island-init` (CustomEvent, bubbles) — fires when a `dj-island`
     element first appears in the DOM.
   - `djust:island-unmount` (CustomEvent) — fires on the element just
     before djust removes it.
5. Django template tag (optional, convenience):
   ```django
   {% load djust_islands %}
   {% react_island "MyChart" props=chart_data on_bar_click="bar_clicked" %}
   ```
   Expands to the canonical `<div dj-island data-react="MyChart" data-props="..." dj-on-bar-click="bar_clicked">`.

**Estimated effort**: ~1 week. Most of the work is in the Rust differ
(skip descent into `dj-island` subtrees) + tests across the patch types.
The client side is small.

**Risks:**
- Tests against React's `createRoot` lifecycle from JSDOM are awkward
  (React 19's act() helper is mandatory; our existing JS test harness
  doesn't import React). Solution: keep `djust-react` testing
  out-of-tree; in-core tests just verify the differ-doesn't-descend
  property + the lifecycle events fire.
- Documenting the `data-props` JSON-serializability constraint (no
  functions, no DOM refs, no Date objects without `toJSON()`).
  Standard React-from-outside constraint; users know it.
- The `dj-on-<event>` routing must distinguish CustomEvents from native
  events (`bar-click` vs `click`). Already supported (the event name
  is the user's choice; djust doesn't validate against the native event
  list), but doc it explicitly.

**Out of scope for v1.1:**
- A bundled React helper (`djust-react` stays out-of-tree; published
  separately so core djust has no React dependency).
- Vue/Svelte/Solid-specific helpers (the primitive works for all of
  them; the community can publish helpers).
- SSR / hydration of React components from Python (hard pass; would
  require a Node sidecar).

## Where this fits in the v1.1 brainstorm

The v1.1 brainstorm at [docs/strategy-sessions/2026-05-19-v1.1-brainstorm.md](2026-05-19-v1.1-brainstorm.md)
named **E6 (HTMX interop)** and **E1 (DRF / django-ninja interop)** as
the recognized interop work. React-component interop is the missing
sibling — same shape (Path E "Ecosystem interop"), same effort tier (S
or M), arguably higher demand from the migrate-to-djust audience.

Propose adding as **E7** to the v1.1 brainstorm matrix:

| ID | Description | Effort | Risk | Novel | Audience | Differentiator | Score |
|---|---|---|---|---|---|---|---|
| E7 | **`dj-island` + React/Vue/Svelte interop primitive** — opaque-subtree attribute, lifecycle events, `djust-react` companion helper out-of-tree | S | L | M | **H** | M | **3** |

Score rationale: same tier as E1 (DRF interop) and E6 (HTMX interop) —
all three are "small, high-audience interop" items that compose with
the existing Path D / Path E options without committing to either.

## What to do next

If the launch owner agrees with the recommendation:

1. **Add E7 to the v1.1 brainstorm** (`docs/strategy-sessions/2026-05-19-v1.1-brainstorm.md`).
2. **File a tracking issue** on `djust-org/djust` for the v1.1 line —
   title: *"feat(vdom): `dj-island` attribute for opaque-subtree interop
   (React / Vue / Svelte / rich-text-editors)"*. Description: link to
   this strategy doc.
3. **Wait for v1.0.0 GA** — this is NOT a v1.0 blocker, and shipping it
   pre-1.0 would slow the GA cut. Right after GA, when the headline-path
   re-strategize happens (per the v1.1 readiness session), put it on
   the v1.1 candidate list.
4. **Until the primitive ships, document the Custom Element wrapping
   workaround** as the supported-today story. It works against djust's
   existing surface; users with urgent React interop needs aren't blocked.

## Honest caveats

- This recommendation hasn't been prototyped. The differ-doesn't-descend
  property is conceptually clean but the Rust implementation may surface
  edge cases (what about `key` attributes inside the island? What if the
  user nests `dj-island` inside another `dj-island`?). Estimate +0.5 wk
  for those.
- The MutationObserver-on-`data-props` shape is the simplest mechanism
  for new-props delivery, but it has a quirk: if the user writes
  multi-line JSON or HTML-encoded JSON, observer-triggered re-renders may
  fire on partial updates. The fix is to write `data-props` atomically
  from the differ side — already what djust's `SetAttr` patch does.
- Once `dj-island` exists, it becomes the canonical "third-party widget"
  boundary for everything that owns its own DOM — not just React. This
  is a feature, not a bug, but it means the doc and naming should not
  be React-specific. The attribute is `dj-island`, not `dj-react`.

## Provenance

Generated 2026-05-28 in response to user goal "determine the best way to
interact with React components using djust." Spawned no subagents (the
research path was tractable inline). The recommendation will be revisited
at the v1.1 strategy gate post-1.0-GA.
