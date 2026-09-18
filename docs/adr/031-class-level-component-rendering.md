# ADR-031: A class-level LiveComponent is bound per view — it renders with `{{ component }}` and receives its own events

**Status**: Proposed
**Date**: 2026-09-17
**Citations**: `file:line` pinned to `main` at `476cedf0`; every one asserted against its expected token at write time. Measurements in §Measured were run on that tree (see §7 for what the first draft got wrong, and how).
**Deciders**: Project maintainers
**Related**:
- [ADR-020](020-island-attribute-component-interop.md) — the attribute↔component boundary
- Issues: #2501 (closed — the escaping fix is landed, §Docs item 1), #2894 (the `theme_card` block-tag gap, adjacent but separate)
- `docs/website/guides/components.md:227`, `:252`, `:236-266`, `:643` — four claims this ADR measures and finds stale
- `python/djust/components/mixins/base.py:39-42` — the render-cache contract, declared and unread

---

## Summary (plain language first)

A djust developer can declare a component two ways. Assigned to `self` in
`mount()`, it renders with `{{ counter }}` and receives events, because the
framework registers it in `view._components` and routes `component_id` events to
it. Declared as a **class attribute**, it becomes a *descriptor*: the framework
gives it state that is correctly per-view — and then hands that *state* back
instead of the component. So `self.nav.active` works, `{{ nav }}` prints a dict,
the component is invisible to snapshots and session save, and it can receive
exactly one event, bolted onto the view by `Meta.event`.

The two halves of the gap — rendering and events — are one missing thing: a
**per-view object that owns both**. Instance components have it (the instance
itself). Class-level components have only the shared descriptor and a bare
`State` dict.

**Decision:** `__get__` returns a per-view **bound component**: the shared
descriptor plus this view's `State` plus its id. It registers itself in
`view._components` under the attribute name, so it is snapshotted, saved and
event-routed exactly like an instance component. Its handlers are ordinary
`@event_handler` methods on the component class, called with the bound
component as `self`, so `self.state.active` is this view's state. `str()` of it
renders the component's template when one is declared. Attribute access is
forwarded to the state, so existing view code keeps working.

What this means for a developer:

```python
class Tabs(LiveComponent):
    template = '<div class="tabs">{% for t in tabs %}<button dj-click="select" dj-value-tab="{{ t }}">{{ t }}</button>{% endfor %}</div>'

    class State(TypedState):
        active: str = "overview"
        tabs: list = ["overview", "settings"]

    @event_handler()
    def select(self, tab: str = "", **kwargs):
        self.state.active = tab          # this view's state


class Dashboard(LiveView):
    nav = Tabs()                          # nothing to register in mount()

    @event_handler()
    def save(self, **kwargs):
        if self.nav.active == "settings": ...   # attribute access still works
```

```django
{{ nav }}            {# the Tabs template with this view's state #}
{{ nav.active }}     {# overview #}
```

- **No behaviour change for shipped code.** The eight `djust.components.descriptors`
  classes declare no template (M6) and use `Meta.event`, which becomes an alias
  for a handler. Their state, serialization and the theming tags that draw them
  are untouched.
- **One visible change.** `isinstance(view.nav, Tabs.State)` becomes `False`;
  `view.nav.state` is the `State`. Reads and writes through `view.nav.active`
  behave as before.
- **Two PRs.** Events and registration first (nothing renders differently);
  rendering second (opt-in by declaring a template).

## Context

### The two forms, and what each gives

`__set_name__` (`components/base.py:683`) registers a class-level component in
`_component_descriptors` and, if `Meta.event` is set, attaches **one** handler
to the owner view (`:750`) that looks the state up by attribute name and calls
the descriptor's single `_handle_event` (`:772`; `descriptors/tabs.py:26-29`).
`__get__` (`:709`) returns the component itself when there is no `State` class
(`:721`) and otherwise a per-view `State` stored at `obj.__dict__[_component_<name>]`,
rehydrated from a plain dict after deserialization.

Instance components take a different path: `_assign_component_ids`
(`mixins/components.py:131`) walks `self.__dict__` for `Component`/`LiveComponent`
instances (`:138`), they live in `view._components` (a dict, `live_view.py:556`),
and an event carrying `component_id` — which the client reads off the
`data-component-id` wrapper (`static/djust/src/09-event-binding.js:536`) — is
resolved there (`runtime.py:3705-3709`) and dispatched to the component's own
handler methods.

So the class-level form has per-view state and none of the plumbing; the
instance form has the plumbing and no per-view state without `mount()`.

### What the tree already declares about rendering it

`TypedState` (`components/mixins/base.py:22`) documents a render cache
(`:39-42`): `_dirty`, `_cached_html`, `_render_hash`. Only one of the three is
read anywhere: `_dirty` is how the bridge detects an in-place mutation of a
`TypedState` between events (`mixins/rust_bridge.py:837`, `:861`) and it is
cleared after every sync (`:907-908`). `_cached_html` and `_render_hash` are
written and never read (M5). The dirty flag is therefore load-bearing for
**change detection**, not for caching, and any render cache must leave it alone.

### Why `str()` on the State cannot be the render hook

The first draft proposed giving `TypedState` a `__str__`. Measured (M9): the Rust
engine converts every `dict` — subclasses included — into an engine map
(`mixins/rust_bridge.py:174`) and displays the map itself; `__str__` on the
subclass is never called, on `render_template` or on the LiveView HTTP path.
A `Component` renders (M1-M3) only because it is *not* a dict: it crosses as an
opaque object whose `str()` the engine takes as safe. Rendering therefore needs
a non-dict object on the context, which is the same object events need.

## Measured

| # | Measurement | Result |
|---|---|---|
| M1 | `{{ c }}` for a `Component`, plain Django `Engine()` | `<div class="dj-progress">…`, unescaped |
| M2 | `{{ c }}` through `djust._rust.render_template` | unescaped |
| M3 | `{{ c }}` through the real LiveView HTTP path | unescaped |
| M4 | `{{ nav }}` where `nav = Tabs(active="overview")` is a class attribute | `{'active': 'overview', 'component_id': 'nav'}` — the engine's map display |
| M5 | readers of `_cached_html` / `_render_hash` outside `mixins/base.py` | **0**; readers of `_dirty`: `rust_bridge.py:837`, `:861`, `:907` |
| M6 | the eight `components/descriptors/*.py` declare a `template`? | **0 of 8** |
| M7 | `str()` of a `SafeString`-returning `__str__` | stays a `SafeString` — why M1-M3 do not escape |
| M8 | `Counter.descriptor()` on a `LiveComponent` | `AttributeError` — `components/base.py:572` and `components.md:252` are the only mentions |
| M9 | `{{ nav }}` with `State.__str__` returning `mark_safe("<b>…</b>")`, both engine paths | the dict repr; `__str__` never called |
| M10 | `{{ nav }}\|{{ nav.active }}` with a non-dict object forwarding attribute access and rendering on `str()`, both engine paths | `<nav>overview</nav>\|overview` |
| M11 | `view._components` type; the documented workaround `self._components.append(...)` (`components.md:258`) | `dict` (`live_view.py:556`) — the workaround raises `AttributeError` |
| M12 | the M10 object as a class attribute on a `LiveView`, rendered through `render_with_diff` (PR 1) | dropped: `mixins/context.py:259-262` keeps a class-level value only if it is JSON-serializable (#694), and `serialization.py:2155` (`return str(value)`, warning at `:2104`) stringifies an unknown object before Rust sees it |

## The four things the docs say that this tree does not

1. **`components.md:643`** — "markup is currently escaped on all four render paths … add `|safe` until the second half of #2501 lands." False: M1-M3 render unescaped and #2501 is closed.
2. **`components.md:227`** — the same `|safe` instruction, scoped to `LiveComponent`.
3. **`components.md:252` and `components/base.py:572`** — `GreetingWidget.descriptor()` is documented as the class-level pattern; the method does not exist (M8).
4. **`components.md:236-266`** — the "auto-promotion gap" section's workaround appends to `_components`, which is a dict (M11). The gap is real; the workaround never worked.

All four are cheap to fix independently (§Sequencing S0).

## Decision Drivers

1. **Two ways to declare a component must not differ in what works.** Per-view state is the descriptor's whole point; it must not be the form that cannot render or receive events.
2. **One object, one registry.** Everything that walks `_components` — event routing, time-travel, session save — should see a class-level component the same way it sees an instance one.
3. **Additive for the eight shipped descriptors.** `Meta.event` keeps working; no template means no render change.
4. **Explicit state.** Inside a handler, `self.state.active` says what is per-view. Forwarding on the *view* side (`view.nav.active`) is kept for compatibility; forwarding on the *handler* side is not offered.
5. **Leave `_dirty` to the bridge.** A render cache must not touch the flag change detection reads.
6. **The documented contract is part of the change.**

## Options Considered

**A. A render proxy at the engine boundary only.** `__get__` keeps returning the
`State`; a non-dict wrapper is substituted when context is handed to the engine.
Fixes `{{ component }}` (M10) and nothing else: events stay one-per-component
via `Meta.event`, snapshots stay blind. Rejected as the end state; it is a
subset of B.

**B. A per-view bound component from `__get__`.** *Chosen.* One object owns
state, id, rendering and dispatch, and registers in `_components`. The cost is
`isinstance(view.nav, Tabs.State)`; attribute forwarding keeps every other read
and write the same.

**C. Events first through a dispatch entry, rendering via A.** Two objects for
one component and two places to keep in sync; converges on B. Kept only as
B's *staging order* (§Sequencing).

**D. Return the descriptor itself, with state resolved from the owner.** The
descriptor is one shared object per class; giving it a view back-reference is a
cross-request leak. Rejected.

**E. `TypedState.__str__` renders.** The first draft. Unimplementable: M9.

## Decision

**D1 — `__get__` returns a `BoundComponent`.** For a descriptor with a `State`
class, `__get__` creates, on first access, a per-view object holding the shared
descriptor, this view's `State`, and the attribute name as `component_id`, stores
it at the existing `obj.__dict__[_component_<name>]` slot, and returns it
thereafter. After a round trip (the slot holds a plain dict) it rebuilds the
bound component around the rehydrated `State`, as `__get__` rehydrates today.
Descriptors without a `State` class keep returning the component itself (`:721`).

**D2 — The bound component registers in `view._components`** under its
`component_id`, on creation and on rebuild, so `runtime.py:3709` resolves it,
and time-travel and session save see it.

**D3 — Attribute access forwards to the state.** `bound.active` reads
`state["active"]`; `bound.active = x` writes it through `TypedState.__setitem__`
(dirty tracking intact). `bound.state` is the `State` itself. Unknown names raise
`AttributeError`.

**D4 — Handlers are `@event_handler` methods on the component class**, invoked
with the bound component as `self`. `self.state` is the per-view state. An event
naming a method that is not an event handler raises, as it does on a view.
`Meta.event` is preserved: it registers the same view-level alias as today
(`:750`), now implemented as a call into D4's dispatch, so the eight shipped
descriptors keep their `_handle_event` unchanged.

**D5 — `str(bound)` renders when the component declares a template.**
`template` or `template_name` → render with `dict(state) + component_id`
through `_render_template_with_fallback` (`components/base.py:42`), wrapped in
`<div data-component-id="…">` exactly as `render()` wraps (`:842-857`), so a
`dj-*` event inside it carries `component_id`. No template → `str(state)`, the
dict repr, unchanged. `get_context_data()` is not called on this path: **State
is the context** for a class-level component, a documented contract.

**D6 — Render cache on `_render_hash` and `_cached_html` only.** A render whose
state hash matches returns the cached HTML. `_dirty` is not written by rendering
(driver 5).

**D7 — `_extract_component_state` and session save** treat a bound component
as its `State` (a plain dict on the wire). The bound object is never serialized;
nothing on it is a dict key.

**D8 — Docs.** `components.md` items 1-4 corrected (S0); the class-level form
documented with the example in §Summary and the State-is-the-context contract
(S3).

## Security

- Rendering inserts the component template's output as safe, as `render()`
  does. Values inside the template are escaped by the engine like any context
  value; the component author's `mark_safe` surface is unchanged.
- The bound component holds a view reference. It lives only in the view's
  `__dict__` and `_components`, never on the shared descriptor, so no
  cross-request reference exists. Session save writes the `State` only (D7);
  a test asserts the saved payload has no bound-object leak.
- Event dispatch uses the same `@event_handler` gate as views: a method without
  the decorator is not callable from the client.

## Consequences

**Positive.** Class-level components render, receive any number of events, are
snapshotted and saved, with no `mount()` boilerplate. One spelling,
`{{ component }}`, for both forms. Four stale doc claims corrected. Two unread
fields become load-bearing; the one that was already load-bearing is left alone.

**Negative, accepted.** `isinstance(view.nav, Tabs.State)` is `False` after PR 1.
A search of the tree for that check is part of PR 1; downstream code doing it
gets a documented `bound.state`.

**Neutral.** Instance components are untouched.

## Sequencing

- **S0 — docs corrections** (items 1-4), independent, first.
- **S1 (PR 1) — events and registration**: `BoundComponent` with D1-D4 and D7,
  no render branch. Tests: per-view isolation across two views; a `component_id`
  event reaching a handler with `self.state` bound; `Meta.event` alias; the
  bound component present in a time-travel snapshot and in session save; the
  eight descriptors' behaviour byte-identical; an undecorated method refused.
- **S2 (PR 2) — rendering**: D5 and D6. Tests: `{{ nav }}` on the HTTP path and
  the WebSocket path renders the template with this view's state; a
  template-less descriptor still yields the dict repr (the opt-in gate-off);
  a state whose hash is unchanged is not re-rendered (the cache gate-off);
  a `dj-click` inside the rendered markup reaches the component's handler.
- **S3 — docs** for the class-level form.

No Rust step. M10 measured the bare engine; the LiveView path is different
(M12): `get_context_data` drops a class-level value that is not JSON-serializable
and `normalize_django_value` stringifies unknown objects, so PR 1 adds a
`BoundComponent` arm to each (the same arms `Component` has) — the bound
component crosses as its State until PR 2 gives it a rendered form.

## Verification

- The end-to-end oracle is a served page and a WebSocket round trip: a class-level
  `Tabs` renders, a click on a tab changes `self.state.active` on that view only,
  and the re-rendered markup reflects it.
- Each gate-off named in §Sequencing must fail when its mechanism is removed
  (#1468).
- `make test` stays green; the `LiveComponent` and descriptor suites are the
  regression net for instance components and the eight descriptors.

## Non-goals

- No change to the eight first-party descriptors' markup; the theming tags keep
  drawing them.
- No `{{ component.render }}` spelling.
- No `get_context_data()` on the class-level path.
- Not #2894.

## 7. Record of what the first draft got wrong

Kept in the style of ADR-029 §7.

1. **The chosen mechanism was never tried against the engine.** `TypedState.__str__` cannot render because a dict crosses the boundary as a map (M9). One `render_template` call would have shown it; the draft reasoned from Django's `str()` behaviour (M7) and assumed the engine matched it.
2. **A measurement was scoped to the claim it supported.** M5 said `_dirty` was read nowhere; `git grep` finds three readers in `rust_bridge.py`. The proposed cache would have cleared a flag change detection depends on.
3. **The rejected option was the working one.** A non-dict proxy (Option A) was rejected on `isinstance` and hot-path grounds; measured, it is the only shape the engine renders (M10). The cost was real but belonged in the trade-off, not in a rejection.
4. **The documented workaround was not tried.** `self._components.append(...)` fails because `_components` is a dict (M11). The draft cited the section without running its code.
5. **Rendering and events were treated as separate problems.** They share one missing object; solving rendering alone would have left the event and snapshot gaps and required a second design.
6. **M10 was measured on the wrong path.** `render_template` takes the object; `render_with_diff` never hands it to the engine (M12). Found by PR 1's first real-path test, which rendered `{{ nav.active }}` empty.
