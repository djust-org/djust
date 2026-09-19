# ADR-033: A plain component is state a handler writes to — content-compared, written through, typed on the wire, named per instance

**Status**: Proposed
**Date**: 2026-09-19
**Citations**: `file:line` pinned to `main` at `0c1180d6` and the storybook branch `feat/storybook-ux-wt` at `4efa576d`.
**Deciders**: Project maintainers
**Related**:
- [ADR-031](031-class-level-component-rendering.md) — class-level `LiveComponent` descriptors: per-view `State`, attribute write-through, events routed by attribute name. This ADR gives *plain* components the same three properties where they are missing.
- [ADR-032](032-component-scoped-rendering.md) — component-scoped rendering; its storybook (S3) is the proving ground here.
- #2900 — the change-detection rule that fingerprints a `BoundComponent` as its State (`change_detection.STATE_MARKER`), reused verbatim.
- #2664 — structural fingerprinting of containers and its budget (`change_detection.DEFAULT_BUDGET`), the cost model this ADR inherits.

## Summary (plain language first)

A Django developer writes this and expects it to work:

```python
class MyView(LiveView):
    def mount(self, request, **kwargs):
        self.rating = Rating(value=4, max_stars=5)

    @event_handler()
    def set_rating(self, value, **kwargs):
        self.rating.value = value
```

Today it renders four stars forever. The write lands on the instance, change detection compares a plain `Component` by `id()`, sees nothing, answers `noop`, and even a later full render still shows four stars because the synced state was never re-sent (measured below). The framework's own storybook worked around this by rebuilding the component inside the handler and, later, by keeping the value on the view and deriving the component in `get_context_data()` — both correct, neither what the reader above wrote.

For class-level descriptors (ADR-031) the same write works: `self.nav.active = x` lands on a per-view `State`, the snapshot fingerprints that State, and the event patches. This ADR makes a plain component behave the same way — **a component's constructor kwargs are its state; writing an attribute writes that state; the state is compared by content** — and settles two adjacent conventions the storybook surfaced while generating usage snippets: values arrive on the wire typed, and an instance carries a `name` so one handler can serve several. Nothing changes on the wire or in `client.js`.

## Context

Three facts, all measured on the storybook branch (`python/djust/tests/test_storybook_ux.py`, and the probe in this ADR's PR):

1. **An attribute write on a plain component is invisible.** `Rating` is a `Component`, not a `LiveComponent` with a `State`. `change_detection.fingerprints_by_content(Rating(...))` is `False`; `_snapshot_assigns` (`websocket.py:331`) therefore records `id(component)`, which an attribute write does not change. Dispatching `set_rating` with a handler that does `self.component.value = int(value)` answers `noop`, and `render_with_diff()` afterwards still counts four `rating-star-full` — `_sync_state_to_rust` compares the previous *reference* and sees the same object (`mixins/rust_bridge.py:781`, `_prev_context_refs`).

2. **Event values arrive as strings.** A star renders `dj-click="set_rating" data-value="4"` (`components/components/rating.py:63`). The client already parses typed params — `dj-value-id:int="42"` → `{id: 42}` (`static/djust/src/08-event-parsing.js:233,264`) — but the components emit the untyped form, so every handler starts with `int(value)`. The storybook's first generated snippet omitted it and rebuilt `Rating(value="4")`, and the component compared an int to a str: `TypeError: '<=' not supported between instances of 'int' and 'str'` on the Rating page.

3. **Two instances of one component cannot be told apart.** `set_rating` is the default of Rating's `event` kwarg; 88 of the Python components take `event=` the same way, and the handler receives only `value`. The only way to have two ratings on a page is to rename the event per instance (`Rating(event="rate_delivery")`), which conflates the verb (what happened) with the noun (which one). Descriptors do not have this problem: `{{ nav }}` wraps its markup in `data-component-id="nav"` (`components/base.py:707`), the client sends the id with every event inside it, and `ViewRuntime._dispatch_component_event` routes to that instance.

What Django developers already know covers all three. Identity is a *name*, not a renamed verb: `Form(prefix="service")` namespaces every field of one form class; a formset does it by index (`form-0-rating`); HTML itself submits `name` beside the value. Phoenix LiveView, which djust follows, does the same: stateless function components carry `phx-value-id`, stateful live components get `phx-target`. djust has both halves — `dj-value-*` params and `component_id` routing — and lacks the convention that says which to use when, plus components that emit their own identity.

## Measured

| # | What | Result |
|---|---|---|
| M1 | `fingerprints_by_content(Rating(value=4))` | `False` — compared by `id()` |
| M2 | `_snapshot_assigns` before/after `component.value = 2` | equal — the write is invisible |
| M3 | `dispatch_event("set_rating")` with an in-place write handler | frame `noop`; a fresh `render_with_diff()` still shows 4 full stars |
| M4 | The same view with the state on the view and the component derived in `get_context_data` | `patch`, 2 full stars (the storybook's current generated usage) |
| M5 | Storybook Rating page, demo handler rebuilding `Rating(value="4")` from the wire string | `TypeError: '<=' not supported between instances of 'int' and 'str'` |
| M6 | Python components taking an `event=` kwarg | 88 of 169 |
| M7 | Storybook accordion toggle, whole-page render vs scoped (ADR-032 M10) | 200 → 19 ms; the snapshot pair is ~4.6 ms of what remains (ADR-032 M4) |
| M8 | *(S1, measured)* snapshot pair for a 10 000-row `DataTable`: with its `fingerprint_fields` vs walked under the budget | 15 µs vs 16.3 ms (truncated at 20 000 nodes) |

M7 is the cost model: content fingerprinting of components lands on the same snapshot pair.

## Decision Drivers

1. **The obvious code must work.** `self.rating.value = 5` in a handler re-renders, or the framework is lying to a Django developer about what an attribute is.
2. **One rule for every snapshot.** #2900 established that a component is compared as its state everywhere (`_snapshot_assigns`, the dirty baseline, `@computed` deps, `_sync_state_to_rust`). Plain components join that rule; they do not get a fourth one.
3. **No wire or client change.** Typed params and `dj-value-*` already exist on the client; this ADR uses them.
4. **Identity is a noun.** `event=` stays as a rename; `name=` is how an instance says which one it is.
5. **Budgeted, opt-outable cost.** A data table holding ten thousand rows must not pay a ten-thousand-row walk twice per click because a rating got a feature.

## Options Considered

**A. Keep the workaround as the convention** — state on the view, component derived in `get_context_data`, rebuild in handlers. Correct and what the storybook generates today. Rejected as the *taught* shape: it is boilerplate around a footgun, and it leaves M3 in place for anyone who writes the natural code.

**B. Make plain components descriptors** — give the stateful shipped components a `State` and declare them class-level (`rating = Rating()`), the ADR-031 shape. Right for fixed widgets with behaviour of their own (it reads like fields on a Form class), and it is already available. Rejected as the *only* answer: it does not cover a component per row of a list, the formset case, and it moves the handler onto the component when many uses want the view to own the decision.

**C. Bind the handler at construction** — `Rating(on_select=self.rate_service)`, the framework deriving the event name from the method. Typo-proof, but one method per instance, so it scales like the rename convention and still needs A or B underneath. Rejected.

**D. State-backed plain components + typed values + `name` identity** — this ADR. B remains available and is documented as the shape for fixed widgets with their own behaviour.

## Decision

**D1 — A plain component's kwargs are its state, and the state is what every snapshot compares.** `Component.__init__` (`components/base.py:284`) already receives the constructor kwargs (every shipped component passes its own to `super().__init__`). It keeps them as `state` (a dict the instance owns) and sets `_djust_fingerprint_state = True` on the class, the marker `change_detection._unwrap` honours (`change_detection.py:97-104`). `fingerprints_by_content(component)` becomes `True`; `_snapshot_assigns`, the dirty baseline, `@computed` dependency keys and `_sync_state_to_rust` walk the state with no code of their own — the #2900 rule, unchanged. A component that rebuilt itself every render (`Rating(value=self.value)` in `get_context_data`) now compares *equal* when nothing changed, where today a fresh object always counted as changed.

**D2 — Attribute writes go through to the state.** `Component.__setattr__` writes a public attribute (no leading underscore, and a key of `state` or a declared field) to both the instance and `state`, so rendering (which reads attributes) and change detection (which walks state) agree. Private attributes, `template`, `template_name`, `component_id` and anything a subclass computes in `__init__` are untouched. `BoundComponent` keeps its own write-through (`components/base.py:508+`); the two are the same idea on two classes and are pinned by one shared test.

**D3 — Cost is budgeted and opt-outable, per component; the default walks everything.** The walk uses `DEFAULT_BUDGET` (`change_detection.py:43`, 20 000 nodes) and `warn_fingerprint_truncated` exactly as containers do. A component may narrow it: `fingerprint_fields = ("columns",)` walks only those state keys structurally; every other key is a one-node leaf — a scalar compares by value, a container by `id()` — so `self.table.rows = fetch()` is still seen and `self.table.rows.append(x)` is not, the same contract the budget gives a truncated container. Scalars therefore never need listing; the tuple names the containers worth walking, and `()` walks none. `_djust_fingerprint_state = False` on a subclass restores whole-object `id()` comparison (reassign the component to re-render). The storybook's parameters table shows which fields a component fingerprints.

*Decided (2026-09-19):* narrowing is opt-**in**. The alternative — data-holding components defaulting to `id()` and opting in — was rejected because it reinstates the footgun this ADR exists to remove for exactly the components where a developer is most likely to write `self.table.sort_by = "name"`: the natural code must work first, and the budget (20 000 nodes, one warning) bounds the cost until a component declares `fingerprint_fields`. The data-holding components that ship with djust (`data_table`, `data_grid`, `virtual_list`, the charts) declare theirs in S1, so the default never meets a ten-thousand-row walk in the box. *Implemented (S1):* `DataTable`/`DataGrid` walk `columns`, `BarChart`/`LineChart` walk `labels`, `VirtualList`/`PieChart`/`Sparkline` walk nothing; their `rows`/`items`/`data`/`series`/`segments` compare by identity.

**D4 — Values are typed on the wire.** A component that emits a value renders the typed form the client already parses: `dj-value-value:int="4"`, `dj-value-open:bool="true"`, `dj-value-index:int` … (`08-event-parsing.js:233`). Handlers receive `int`/`float`/`bool`; the `int(value)` line disappears from every snippet and every handler. Untyped `data-value` keeps working; the components stop emitting it. Implemented once, in a `Component.event_attrs(...)` helper that renders `dj-<trigger>="<event>"` plus typed params, adopted component by component (M6: 88 emitters). *Implemented (S3):* `event_attrs(event, trigger="click", **params)` — `int` → `:int`, `bool` → `:bool`, `float` → `:float`, `str` untyped, anything else `:json`, `None` omitted, underscores to hyphens, everything escaped, nothing for a falsy event; the sweep pin `python/djust/tests/test_component_typed_events_sweep_adr033.py` refuses a hand-written trigger or `data-value=` in any shipped component and checks every rendered example (typed numbers, `dj-value-name` on every trigger of a named instance).

**D5 — An instance carries a `name`** (*decided: `name`, the HTML and `theme_input` / `toggle_group` word, over Django forms' `prefix`*). `Component(name="service")` makes `event_attrs` add `dj-value-name="service"` to every trigger, so the handler signature convention is `def set_rating(self, value, name=None, **kwargs)` and one handler serves any number of instances — the formset case (`for row in rows: Rating(name=f"row-{row.pk}")`). `event=` stays as what it is, a rename of the verb, documented as such. *Amended (S3):* the earlier sentence making `name` the `dj-if` marker namespace was dropped — that namespace (`_dj_if_id_namespace`, `components/base.py:104`) belongs to the `template_name` render path, which a plain component's inline `template` and `_render_custom` never take; there are no markers to namespace. If a plain component ever renders through that path, `name` is the value to hand it.

**D6 — Component state persists like descriptor state.** `_capture_snapshot_state` (`live_view.py:941`) drops a plain component today because the instance is not JSON-serializable; after D1 it persists `{"__djust_component__": "djust.components.components.rating.Rating", "state": {...}}` and rehydrates on restore, exactly as a descriptor's `State` dict does. *Implemented (S2):* the tag is written by `StateRoundtripJSONEncoder` (signed snapshot) and by `normalize_django_value(..., state_roundtrip=True)` (session), and read back by `decode_state_roundtrip`, the one decode point every restore already calls. The class is resolved only among modules the process has already imported and must subclass `Component`; the instance is rebuilt through its own constructor with the saved state as kwargs. Anything that cannot be honoured leaves the tag as a dict and logs, the Decimal tag's fail-soft rule. Without this the streamlined form would lose `self.rating.value` on reconnect — the status quo, but the two models must behave alike.

**D7 — When to use which (the docs rule).** A fixed widget with behaviour of its own (tabs, accordion, modal) is a class-level descriptor: per-view State, its own handlers, routed by attribute name — fields on a Form. Anything data-driven or repeated (a rating per row, a chart, a table) is a plain component held by the view: state in its kwargs, written to by the view's handlers, identified by `name` — a formset. Rebuilding a component in a handler still works; it is no longer taught.

## Security

No new surface. `name` is server-assigned and rendered escaped like every attribute; a client-supplied `name` in an event is a string parameter the handler validates as it validates `value`. Typed params are parsed by the existing client code and validated by `validate_handler_params` exactly as `dj-value-id:int` is today. Persisted component state goes through the same `StateRoundtripJSONEncoder` and the same signed snapshot as assigns.

## Consequences

- The natural code works (M3 → `patch`). The storybook's generated usage becomes the three-line form in the summary, with `name=` shown when the page has more than one instance.
- Every snapshot of a view holding large components pays a walk it did not pay before, bounded by D3. Components known to hold data (`data_table`, `data_grid`, charts, `virtual_list`) ship with `fingerprint_fields` set in the same change.
- Two mental models remain and are named (D7). The storybook's usage block shows the descriptor form for descriptors and the state-backed form for plain components, and links to D7.
- `event=` renaming keeps working; no existing view breaks. Views that rebuilt components every render render less (D1).
- The wire protocol, `client.js`, the Rust engine and the VDOM are untouched.

## Sequencing

- **S1 (framework)** — D1, D2, D3 in `Component`; the shared write-through test with `BoundComponent`; `fingerprint_fields` on the data-holding components. M1–M3 become pins: the in-place write answers `patch`.
- **S2 (framework)** — D6, persistence of component state, with the restore round-trip test.
- **S3 (components)** — D4 and D5 through `Component.event_attrs`; adopt in the 88 emitters in **one PR** (a scripted sweep, verified by the storybook's event scan: every `dj-click` on the page carries typed params and, when named, `dj-value-name`), so the convention lands whole rather than half the components typing their values.
- **S4 (storybook + docs)** — usage snippets become the state-backed form; the `event` row in the parameters table says "rename; identity is `name`"; the storybook's own demo handlers write to the component instead of rebuilding it (removing the coercion the storybook added for M5); `core-concepts/components.md` and `guides/components.md` carry D7.

## Verification

- `python/djust/tests/test_storybook_ux.py` today pins M3's *workaround*; S1 flips it to pin the direct write.
- A component-per-row view with three `Rating(name=…)` and one handler; a click on the second changes only the second (wire test on a `WebsocketCommunicator`).
- A data table with 10 000 rows and its `fingerprint_fields`: the snapshot pair stays under 1 ms — measured 15 µs (M8).
- Reconnect after `self.rating.value = 5`: the restored view renders five stars.

## Non-goals

- Changing how descriptors work (ADR-031) or routing plain-component events to the component.
- A `phx-target`-style routing for plain components; `name` as a parameter is enough, and it composes with descriptors for the fixed-widget case.
- Deriving handler bodies from component semantics (the storybook's demo table is storybook code, not a framework feature).
