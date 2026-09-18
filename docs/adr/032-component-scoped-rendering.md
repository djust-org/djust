# ADR-032: An event that changes only a bound component re-renders that component's subtree, not the page

**Status**: Proposed
**Date**: 2026-09-18
**Citations**: `file:line` pinned to `main` at `6729f40a` unless marked `#2887` (the branch every measurement was taken on: `fix/theming-gallery-render-and-styling` at `3e02340d` merged with that `main`).
**Deciders**: Project maintainers
**Related**:
- [ADR-031](031-class-level-component-rendering.md) — the `BoundComponent` this builds on: one object per view owning state, dispatch and `render()`, registered in `view._components`
- ADR-011 / ADR-014 / ADR-018 — embedded `{% live_render %}` children and their scoped frame (`embedded_update`)
- #2914 — bridged tags re-convert the whole Python context per call (Option 3, researched here, decided §Options 3-5)
- #2913 — a `namedtuple` class minted per value in the Rust→Python conversion (Option 4, same)
- #2900 (the skip gate now sees a component's state), #2737 / #2738 (why the engine does not narrow a context by a read-set), #2912 (the dirty baseline)
- `benchmarks/component_scoped_floor.py` (added with this ADR) — §Measured M1-M5

---

## Summary (plain language first)

Click an accordion item on the storybook page and the browser flips the class in 3 ms (the optimistic rule) and shows the content 217 ms later. Almost all of that is the server rendering the **whole 30 KB page** to find out that one `<div>` changed. The page carries a 35 KB sidebar with 175 `{% url %}` tags in a loop, paid on every click on anything.

Three things were measured before deciding anything (§Measured):

1. The engine's partial-render path *is* engaged on this click and saves nothing: its unit is a top-level template node, and a page's top-level node is `<html>`.
2. **75 % of the render is the tag bridge, not the template.** Every bridged tag call re-converts the entire Python context (~0.77 ms × 177 calls), and inside that conversion a `namedtuple` *class* is minted per value (1 759 per render). Memoising the converted context per frame and interning the classes takes this click from **186 ms to 20 ms** with byte-identical output — on every page with a bridged tag in a loop, not just this one. Those are #2914 and #2913, decided *do* below.
3. What remains after that is the page render itself, which for a component-only change is still the wrong unit of work. A bound component (ADR-031) can render itself; diffing its subtree against the server's copy of the page and splicing the result costs **under 5 ms** here, of which 4.6 ms is the skip gate's snapshot of that sidebar and the render+diff is under 0.1 ms.

So the decision is layered. Ship the bridge fixes first: they are general, exact, and measured at 9×. Then this ADR's mechanism: when an event changed **nothing but one bound component's state** — a fact the runtime already computes for the skip gate — render that component alone, diff its subtree, splice it into the server's tree, and send the same `patch` frame as today with paths that all fall under the component's node. The client changes nothing; recovery, versioning and the next full render see a correct tree. Any doubt → today's path.

The storybook accordion gets this once its live example *is* the bound component's own template rather than an instance component rendered into a derived context value (§Sequencing S3) — which is also the shape ADR-031 documents.

## Context

### Where one click goes (measured on the page, #2887)

Storybook `/theme/gallery/storybook/accordion/`, one `accordion_toggle`, browser timeline (`trace_event`) plus the frame's `timing`:

| phase | ms |
|---|---|
| click → WebSocket send | 3 |
| optimistic `toggle_class` (first DOM mutation) | 3 |
| server `render_with_diff` (`timing.render`) | 175–195 |
| patch received → DOM settled | ~14 |
| **total** | **231** |

Inside that render (`cProfile`, `python/djust/mixins/template.py:1239 render_with_diff`, and the instrumented bridge in #2914):

| where | per render | what |
|---|---|---|
| `build_py_context` ×177 | 112 ms | the whole 66-key context (two 175-row lists) converted to a fresh `PyDict` per bridged tag call (`crates/djust_templates/src/registry.rs:1420`) |
| `context.to_hashmap()` ×177 | 24 ms | every `Value` in every frame cloned before that (`crates/djust_core/src/context.rs:2574`) |
| of which `collections.namedtuple` class creation ×1 759 | ~50 ms | one class per `GroupedResult` per call (`crates/djust_core/src/lib.rs:5821-5834`); 176 `{% url %}` × 10 groups from `{% regroup %}` (`storybook_base.html:291,303-304`) |
| `deep_fingerprint` of `sidebar_components` (35 KB, 61 k steps), twice | ~26 ms | the skip gate's pre/post snapshot |
| template render + VDOM diff proper | ~15 ms | what is left with the bridge memoised (§Measured M7) |

### The partial-render path is engaged and does not help

Instrumented: the view computes `changed_keys = {'_component_accordion'}` and hands Rust `set_changed_keys(['accordion', 'python_examples_html'])`; `render_with_diff` takes the partial branch (`crates/djust_live/src/lib.rs:830` → `render_with_loader_partial` → `renderer::render_nodes_partial`, `crates/djust_templates/src/renderer.rs`). That re-renders a **top-level template node** whose dependency set intersects the changed keys and reuses the cached fragment otherwise. `storybook_detail.html` extends `storybook_base.html`, whose resolved top-level nodes are `{% load %}`, `<!DOCTYPE html>` and one `<html>` element (`storybook_base.html:1-2`). Everything the page shows is inside that node, so its dependency set holds every variable and it is re-rendered whole. True of every page with a document wrapper.

### What exists for embedded children, and why it is not this

A `{% live_render %}` child has its own scope for events addressed to it: `runtime.py:3618-3672` renders only the child (through Django's engine, `websocket.py:501 render_embedded_child_html`) and emits `embedded_update {view_id, html}`, which the client morphs into `[data-djust-embedded]` (`03-websocket.js:1135`, `handleEmbeddedUpdate`). Two things keep it from being the answer:

1. A **parent** event re-renders the parent, and the child is rendered as part of the parent's template (`live_tags.py:2091`, the #1813 reuse hatch, `_render_sticky_child_html`) — the child is not shielded. Measured: parent event → parent builds 1, child builds 1.
2. The child has no VDOM of its own (no `_rust_view`, no version); its `dj-id`s are assigned when the parent parses the full HTML and survive only because the child markup is byte-identical between renders. A bound component lives **inside** the parent's tree; a scoped update must keep that tree consistent or the next diff runs against a stale copy.

### What ADR-031 already provides

- `BoundComponent.render()` (ADR-031 D5): the component's template with its State, wrapped in `<div data-component-id="…">`, cached on the state hash (D6).
- `view._components[component_id]` (D2): the object an event resolves to on both dispatch paths — `runtime.py` `_dispatch_component_event` (`component_id` in params) and the view-level `Meta.event` alias, which knows its `component_id`.
- The skip gate (`runtime.py:3062-3076`) fingerprints the component's State (#2900): `_compute_changed_keys(pre, post)` names the component's slot when, and only when, its state changed.

## Measured

All on one machine, run alone (the first pass, taken while three builds ran in parallel, read 2× higher and was discarded). M1-M5: `benchmarks/component_scoped_floor.py`, 8 toggles, medians. M7-M9: the three research reports, medians of 12-15.

| # | measurement | result |
|---|---|---|
| M1 | full-page toggle today (alias event → `render_with_diff`) | **194.8 ms** (min 181.9) |
| M2 | the accordion example rendered alone against the new state (536 bytes) | **< 0.1 ms** |
| M3 | Rust parse + diff of the component's old vs new markup (`diff_html`; 373 bytes of patches) | **< 0.1 ms** |
| M4 | the skip gate's pre + post `_snapshot_assigns` (paid by every path; 35 KB sidebar) | **4.6 ms** |
| M5 | scoped floor ≈ M2 + M3 + M4 | **4.7 ms** vs M1 194.8 ms (41×); ~0.1 ms once the sidebar is a `static_assign` |
| M6 | partial-render path: top-level nodes of the resolved storybook template | 3 (`{% load %}`, doctype, `<html>`); `<html>` re-renders on every changed key |
| M7 | Option 3 — per-frame context memo (#2914): toggle / `render_with_diff` | 186.4 → **21.1 ms** / 180.5 → 15.3 ms; + interning **19.8** / 14.0 ms; 17 frames byte-identical, 14/14 stress shapes byte-identical, suite green |
| M8 | Option 4 — `namedtuple` class interning alone (#2913) | 186 → **138 ms** (5 277 class creations → 1 per process); 1 149 tests green |
| M9 | Option 5 — sidebar as `{% live_render %}` child, synthetic twin of the page | parent event 137 → **5.9 ms** (→ 3.7 with a child memo); the win is the loop leaving the tag bridge, the memo is worth ~2 ms; sidebar events become a 16-18 KB full-HTML morph instead of an 8 KB patch |

Reading M7-M9 together: once the bridge is memoised, the sidebar loop costs what Option 5 measured it at outside the bridge (~5 ms), so converting the sidebar to a child is no longer needed for this page.

## Decision Drivers

1. **The unit of work must be the thing that changed.** A page render to move one `<div>` is the cost model this ADR retires; shaving the page render leaves the model — but a 9× shave that is exact and general ships first.
2. **Exactness, not heuristics.** The scoped path must produce the DOM the full path would, or not run. The decision is made from what actually changed (the snapshot), not from what the template looks like alone.
3. **No client change.** A `patch` frame with subtree paths is a `patch` frame. Recovery, versioning and the debug panel keep working because the server's tree is kept consistent.
4. **Fall back, never fail.** Every gate failure takes today's path.
5. **Do not widen #2737.** Nothing here narrows a context by a read-set; #2914 memoises the *same* conversion, it does not choose names.

## Options Considered

**A. Finer-grained partial rendering in the engine** — make `render_nodes_partial` recurse into element nodes so a changed key re-renders only the deepest nodes reading it. Would help every page. Rejected *for this ADR*: it changes the template engine's caching model (the per-node fragment cache becomes a tree; the `{% for %}` loop cache, `dj-if` markers and the `context_may_have_changed` propagation across siblings all interact with it). Recorded as the follow-up that would subsume this ADR's narrow case; the narrow case is exact today, the general one is not yet designed.

**B. Make the component a `{% live_render %}` child.** Child-addressed events are scoped, but the event here is a *parent* event and a parent render rebuilds the child (M9, and the child render is Django-engine full HTML, not a patch). Rejected as the mechanism.

**C. Component-scoped render + subtree splice.** *Chosen.* Render the bound component alone, diff its subtree against the node in the server's VDOM, splice, emit the patches. The gate is the changed-keys set the runtime already computes.

**D. Client-side only (optimistic rules).** Exists (`toggle_class` at 3 ms) and cannot carry content the client has not seen. A complement.

**E. Options 3, 4, 5 from the research** — decided in §Options 3-5 below. 3 and 4 reduce the full render by 9× and ship before C; they do not change what is rendered.

## Decision

**D1 — A scoped event.** After the handler runs and the skip gate's post-snapshot is taken, the runtime asks: (a) did the event resolve to a bound component `c` — the `component_id` path, or a `Meta.event` alias whose resolved `component_id` names one; (b) does `c` declare a template (ADR-031 D5); (c) is `_compute_changed_keys(pre, post) == {c's slot}` — nothing else on the view changed; (d) is the view's template *component-opaque* for `c` (D2); (e) no `_force_full_html`, no pending push events, no `_changed_keys` set by the handler, no time-travel replay in progress. All five → the scoped path. Any failure → today's path, unchanged.

**D2 — Component-opaque, decided once per (view class, template source, component).** From `extract_template_variables` on the resolved template source (`python/djust/mixins/jit.py:48`, already cached by content hash): every path that starts with `c`'s name must be exactly the bare name (`{{ nav }}`) — never `nav.active`, `nav|length`, `{% for x in nav %}`, `{% with nav.state as s %}`, `{% include ... with x=nav %}`; and no `@computed` on the view class lists `c` among its dependencies (`decorators.py`, the computed deps). Cached on the view class keyed by template hash. A template that reads the component's state anywhere else never takes the scoped path, because that other read would have to re-render too.

**D3 — The scoped render.** `html = c.render()` (its own state-hash cache). Then one Rust call, `RustLiveView.patch_component_subtree(component_id, html) -> Option<(patches_json, version)>`:
1. parse `html` into a `VNode` with the parser `render_with_diff` uses, continuing `dj-id` assignment from the current counter (`djust_vdom::ensure_id_counter_at_least`) so ids stay unique, and `sync_ids` against the old subtree so unchanged nodes keep theirs;
2. find, in `last_vdom` (`crates/djust_live/src/lib.rs:160`), the unique element with `data-component-id == component_id`; zero or more than one → `None`;
3. `diff(old_subtree, new_subtree)` (`crates/djust_vdom/src/lib.rs:850`) with every `Patch.path` (`:632`) prefixed by the node's path from the root;
4. splice the new subtree into `last_vdom` at that path (the shape `splice_ignore_subtrees` uses, `:546`), bump `version` (`lib.rs:1167`), set `state[name]` to the new HTML as a safe string so a later full render sees what the page shows, and **clear `node_html_cache`** — the fragment cache holds the old markup inside the `<html>` fragment, so the next parent render re-collects once. The text-only fast path (`try_text_only_vdom_update_inplace`, `djust_vdom/src/lib.rs:1076`) is the precedent for updating `last_vdom` in place without a full render.

**D4 — The frame.** The same `patch` frame as today (`type, patches, version, event_name, source, ref`), plus `timing.scope = "component"` so the debug panel and `benchmark_event` can tell. No client change. `html_recovery` serves `last_vdom`, which is current.

**D5 — Where it slots in.** Both dispatch paths, one helper. `_dispatch_event_render` (`runtime.py:3062`): between the skip decision and `_render_and_send`, if D1 holds, call the helper and return. `_dispatch_component_event` (`runtime.py:3805-3823`, which today always emits a full `html_update`): the same helper before the full render. The helper owns D1-D4; the callers pass the resolved component.

**D6 — Failure inside the scoped path is not an error.** `None` from `patch_component_subtree` → DEBUG log, today's path in the same event; the client sees one frame either way.

**D7 — Instance components are out of scope.** They are compared by identity (Phoenix's rule) and have no per-view slot for the gate to name. Nothing changes for them.

## Options 3-5 — researched alongside, decided here

| option | mechanism | measured on this page | general? | effort | decision |
|---|---|---|---|---|---|
| **3** — memoise the converted Python context per frame (#2914) | a `stamp` on each `ScopeFrame` (assigned in `from_shared` and `DerefMut`, the single write door); `bridge_py_dict` builds the handler dict frame by frame, hit → `PyDict.update(cached)`; dotted safe-path heads re-converted fresh so `remint_safe_context` never writes into a cached object | 186 → 21 ms; 17 frames + 14 stress shapes byte-identical; suite green | yes — any page with a bridged tag in a loop pays O(context) per call today | M (~150 lines Rust; route all eight builder sites; tests for stamps, the safe-head bypass, nested-loop `as var`, and the one visible change — a tag mutating a nested value it received is now seen by later tags in the same render, which is Django's behaviour) | **Do.** Not a #2737 read-set narrowing: the same names are converted, once per frame version. |
| **4** — intern `namedtuple` classes by `(name, fields)` (#2913) | a `PyOnceLock<Mutex<HashMap>>` in `named_tuple_class`; both conversion arms use it; `module="djust._rust"` explicit | 186 → 138 ms alone; 5 277 classes → 1 | yes — every `{% regroup %}` crossing the bridge | S (80 lines; 1 149 tests green) | **Do, first** — semantics move toward Python's own (one class per shape). |
| **5** — `{% live_render %}` child memo / sidebar as a child | memo keyed on the child's `_snapshot_assigns` fingerprint, serving cached HTML during a parent render; conversion moves the loop out of the bridge | memo ~2 ms; conversion 137 → 6 ms only because the loop leaves the bridge (which 3 fixes for everyone); sidebar events become a 16-18 KB morph instead of an 8 KB patch; the reuse hatch drops `with` kwargs after mount (pre-existing) | narrow | S memo / M conversion | **Don't now.** Re-evaluate after 3 lands; the memo composes cleanly if a page still needs it. Fix the dropped-kwargs hatch regardless (filed with the report). |

Page-side, independent of all of the above and available today on #2887: `static_assigns = ["sidebar_components"]` (removes M4's 4.6 ms and the per-event re-sync of 35 KB) and a precomputed `comp.url` in `mount()` instead of `{% url %}` per row.

## Security

- The scoped render inserts `c.render()`'s output as `render_with_diff` inserts `{{ nav }}` today: the component template escapes its values, the wrapper is `format_html`. No new `mark_safe` surface (ADR-031 §Security).
- The event gate (`@event_handler`, `is_safe_event_name`, rate limits, permissions) runs before any of this; the scoped path starts after the handler was allowed and ran.
- A patch cannot address outside the component's subtree: every path is prefixed by the server from a node it located itself.

## Consequences

**Positive.** A component-only change costs the component's render plus a subtree diff (M5: < 5 ms here, < 0.1 ms without the sidebar snapshot) instead of the page; the client, wire format and recovery are untouched; every failure mode is today's behaviour; the gate is exact by construction (D1c), not by template inspection alone. Options 3 + 4 make every page with bridged tags in loops ~9× cheaper before this ships.

**Negative, accepted.** One extra full collecting render on the first parent event after a scoped one (the cleared fragment cache). A template that reads the component's state outside `{{ nav }}` never takes the scoped path (D2) — visible in `timing.scope`. Two new Rust entry points to maintain.

**Neutral.** The storybook's live accordion is rendered today by an instance component into `python_examples_html`, a derived context value — D1(c) sees that key change and takes the full path. It takes the scoped path once the example is the bound component's own template (S3).

## Sequencing

- **S0 — #2913** (namedtuple interning), S, its own PR. **S0b — #2914** (per-frame context memo), M, its own PR with the conditions listed. Both land before S1; they are the 9×.
- **S1 (Rust)** — `patch_component_subtree` on `RustLiveView`; `find_by_attr` and prefixed `diff` in `djust_vdom`; `sync_ids` on the subtree. Tests: node missing, duplicated, `dj-id` continuity, `node_html_cache` cleared, version bump, state updated, wire shape of the `patch` frame unchanged.
- **S2 (Python)** — the gate (D1; D2 with its per-class cache) and the shared helper (D5) on both dispatch paths; `timing.scope`. Tests, on a real `WebsocketCommunicator`: scoped event → `patch` with every path under the component node and `timing.scope == "component"`; a template reading `nav.active` elsewhere → full path; a handler that also changes another assign → full path; `{% for x in nav %}` → full path; recovery after a scoped event returns the current markup; the parent event after a scoped one is correct (cache cleared); two views on two sockets; a gate-off per gate condition (#1468).
- **S3 (storybook, #2887)** — render each live example as the bound component's own template so the accordion, tabs, dropdown and modal pages take the scoped path; `benchmark_event` before/after in the browser.
- **S4 (docs)** — `components.md`: when a component event is scoped and how to keep a template component-opaque.

## Verification

- The oracle is the browser: `trace_event` on the storybook accordion — server time under 25 ms after S0/S0b, under 5 ms after S3, and the total under 30 ms.
- Each gate condition in D1/D2 has a gate-off: remove it and its exactness test goes red.
- `make test` stays green; the ADR-031 suites and `test_bound_component_skip_gate_2900.py` are the regression net.

## Non-goals

- Finer-grained partial rendering in the engine (Option A) — recorded as follow-up work.
- Changing the `embedded_update` contract for `{% live_render %}` children.
- Scoping events for instance components (D7).
- Read-set narrowing of the bridged-tag context (#2737's verdict stands).
