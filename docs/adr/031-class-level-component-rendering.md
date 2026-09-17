# ADR-031: A class-level LiveComponent's State renders, and the caching it already declares becomes load-bearing

**Status**: Proposed
**Date**: 2026-09-17
**Citations**: `file:line` pinned to `fix/theming-gallery-render-and-styling` at `f1f1ef23`; every one asserted against its expected token at write time. Measurements in §Measured were taken on this tree, not reasoned about.
**Deciders**: Project maintainers
**Related**:
- [ADR-020](020-island-attribute-component-interop.md) — the attribute↔component boundary
- Issues: #2501 (closed — the escaping fix §The four things the docs say that this tree does not measures as **already landed**), #2894 (the `theme_card` block-tag gap, adjacent but separate)
- `docs/website/guides/components.md:227-233`, `:252`, `:643` — three claims this ADR measures and finds stale
- `python/djust/components/mixins/base.py:39-42` — the render-caching contract, declared and unread

---

## Summary (plain language first)

A djust developer can write a component two ways. Assigned to `self` in
`mount()`, it holds per-request state and renders with `{{ component }}`. Declared
as a **class attribute**, it becomes a *descriptor*: the framework gives it state
that is correctly per-view-instance — and then hands that state back to you
instead of the component. So `self.nav.active` is `"overview"` as intended, and
`{{ nav }}` prints `{'active': 'overview', 'component_id': 'nav'}` instead of a
tab strip. **The class-level form cannot be rendered at all.**

That is the whole gap, and it is not a design that was never considered. The
state object is a `TypedState`, and `TypedState`'s own docstring describes a
render cache — `_dirty`, `_cached_html`, `_render_hash` — with the sentence *"The
render caching system checks this flag to skip re-rendering unchanged
components"* (`components/mixins/base.py:39-42`). The three fields are written on
every mutation and **read nowhere in the package** (§Measured, M5). The rendering
half of the descriptor pattern was designed, half-built, and left.

**Decision:** a `LiveComponent` that declares a `template` (or `template_name`)
becomes renderable from its descriptor. `__get__` binds the state it returns to
its component and its view, and `str(state)` renders when — and only when — that
component declares a template. All eight first-party descriptors declare none, so
nothing in the tree changes behaviour; the change is opt-in by construction.

What this means for a developer:

- **One spelling, both forms.** `{{ component }}` renders whichever way the
  component was declared. `|safe` is not needed — and has not been since #2501
  landed (§The four things the docs say that this tree does not, item 1).
- **No behaviour change for existing code.** A descriptor without a template
  keeps today's `str()` exactly: the dict repr. The eight `djust.components.descriptors`
  classes are untouched.
- **Cost when it is used.** The render cache that already exists becomes
  load-bearing, so a re-render whose state did not change is skipped.

## Context

### The two forms, and what each gives

```python
class A(LiveView):                      # instance form — renders, state per instance
    def mount(self, request, **kwargs):
        self.counter = CounterWidget(initial=10)
```

```python
class B(LiveView):                      # class form — per-view state, renders nothing
    counter = CounterWidget(initial=10)
```

`__set_name__` (`components/base.py:683`) registers the class-level object in
`_component_descriptors` and auto-wires its `Meta.event` handler onto the owner.
`__get__` (`:709`) then decides what attribute access yields:

- no nested `State` class → `return self` (`:721`) — the component, renderable
- nested `State` class → a per-view `State` instance, **not** renderable

The second branch is the one that matters. It is deliberate: it is how a
descriptor gets state that two concurrent requests do not share. The state is
stored at `obj.__dict__[_component_<name>]` and rehydrated from a plain dict
after djust's serialization (`:724-735`). None of that is in question. What is
missing is only that the thing returned cannot render.

### What the tree already declares about rendering it

`TypedState` (`components/mixins/base.py:22`) is a `dict` subclass with typed
properties. Its docstring documents dirty tracking in full
(`mixins/base.py:39-42`), and `__init__`/`__setitem__` maintain all three fields
(`:64-76`, `:89`). A package-wide grep for readers of `_cached_html`,
`_render_hash` and `_dirty` returns **zero hits outside the file that writes
them** (§Measured, M5).

This is the failure mode the repository's own canon names — a pin that is
decorative (#1859), a mechanism declared and not wired (#1860). The fields are
load-bearing for nothing today. This ADR either wires them or the docstring is a
lie; leaving both is not an option the tree should keep.

## Measured

Every row below was run on this tree. M1-M4 establish the problem, M5-M8 are
the supporting facts each decision rests on.

| # | Measurement | Result |
|---|---|---|
| M1 | `{{ c }}` for a `Component`, plain Django `Engine()` | `<div class="dj-progress">…`, **escaped? No** |
| M2 | `{{ c }}` through `djust._rust.render_template` | unescaped |
| M3 | `{{ c }}` through the **real LiveView HTTP path** (a `LiveView` whose `get_context_data` returns the component, fetched with `django.test.Client`) | `<div class="dj-progress">…`, **escaped? No** |
| M4 | `{{ nav }}` where `nav = Tabs(active="overview")` is a class attribute | `{'active': 'overview', 'component_id': 'nav'}` — a dict repr |
| M5 | readers of `_cached_html` / `_render_hash` / `_dirty` outside `mixins/base.py` | **0** |
| M6 | the eight `components/descriptors/*.py` declare a `template`? | **0 of 8** |
| M7 | `str()` of a `SafeString`-returning `__str__` | stays a `SafeString` — this is *why* M1-M3 do not escape (Django does `str(value)` then `conditional_escape`, and `str()` does not downcast a `str` subclass) |
| M8 | `Counter.descriptor()` on a `LiveComponent` | `AttributeError: type object 'Counter' has no attribute 'descriptor'` — the two mentions in the tree (`components/base.py:572`, `docs/website/guides/components.md:252`) are the only two |

## The four things the docs say that this tree does not

Found while measuring, each independently actionable:

1. **`components.md:643`** — *"A component's markup is currently escaped on all four render paths … Add it — `{{ component|safe }}` — until the second half of #2501 lands."* **False today.** M1, M2 and M3 all render unescaped, including the LiveView path the note calls out. #2501 is closed (`gh issue view 2501`). The note is stale and instructs developers to add a filter that is not needed.
2. **`components.md:227-233`** — the same claim, scoped to `LiveComponent`: *"`|safe` is needed until #2501's escaping fix lands."* Same finding.
3. **`components.md:252` and `components/base.py:572`** — both document `GreetingWidget.descriptor()` as *the* class-level pattern. **The method does not exist** (§Measured, M8). The two occurrences in the tree are the only two.
4. **`components.md:236-266`** — the "Descriptor-pattern auto-promotion gap" section, whose stated workaround is item 3's non-existent method. The gap it describes is real; the workaround is not.

Items 1-3 are correctness defects in shipped documentation and are cheap to fix
independently of this ADR (§Sequencing S0).

## Decision Drivers

1. **Two ways to declare a component must not differ in whether they work.** The
   framework cannot recommend the descriptor pattern for its per-view state and
   then make it the one form that cannot be drawn.
2. **Additive, or not at all.** Eight shipped descriptors and every downstream
   theme pack use the current `__get__` contract. An opt-in that leaves them
   byte-identical is the only version with no migration.
3. **Name the mechanism honestly.** `TypedState` documents a render cache. Either
   it is wired here or the docstring is corrected; a declared-and-unread field is
   the decorative-pin class the repo already has a rule about (#1859).
4. **One spelling for the developer.** `{{ component }}` — the documented,
   recommended spelling (`components.md:231`) — must work for both forms. Not
   `{{ component.render }}`, which renders empty (#2501's fourth row) and which
   the docs already tell people not to use.
5. **Per-view state is not negotiable.** Whatever renders must render *this
   view's* state. A design that reaches renderability by sharing one component
   instance across requests trades a display bug for a data-leak bug.

## Options Considered

**A. Return a per-view proxy from `__get__` that forwards attribute access to the state and renders.**
Rejected as the primary shape: `isinstance(self.nav, Tabs.State)` becomes False,
and the tree's own walkers test types — `_assign_component_ids` is
`isinstance(value, (Component, LiveComponent))` (`mixins/components.py:138`), and
third-party code testing for the state class would break silently. It also adds
an object per access on a hot path.

**B. Give `TypedState` a `render()` and make `__str__` consult it, opt-in on the component declaring a template.** *Chosen.* The object `__get__` already returns is the one that gains rendering, so every existing access pattern (`state.active`, `state["active"]`, rehydration, serialization) is untouched, and the opt-in leaves the eight shipped descriptors byte-identical.

**C. Make `__get__` return the component, with state resolved from the owner.**
The descriptor is a **single shared object** — one per class, not one per view — so
it cannot hold a back-reference to "the" view without cross-request bleed. Making
it per-view means copying the component per access. Rejected on both counts.

**D. Leave the framework; document the instance form as the supported one.**
Viable, and the honest status quo. Rejected because the per-view state the
descriptor provides is the property the instance form *lacks* — the two forms
give different things, so "use the other one" does not answer the request. (It
also leaves M5's three fields unread.)

**E. Make every descriptor renderable, including the eight first-party ones.**
Rejected: those eight are pure state, and their markup is owned by the theming
tags (`{% theme_tabs %}` and friends) which render from the same state.
Giving them templates would create two sources for one markup.

## Decision

**D1 — Opt-in is "the component declares a template."** A `LiveComponent` whose
class declares `template` or `template_name` is renderable from its descriptor.
One that declares neither keeps today's behaviour exactly. All eight shipped
descriptors declare neither (M6), so this decision changes nothing already
shipped.

**D2 — `__get__` binds the state it returns.** When it creates or rehydrates a
`State`, it records the owning component and the view on the state with
`object.__setattr__` under `_`-prefixed names, which keeps them out of the dict
and out of every path that serializes it — `_extract_component_state` already
filters `not key.startswith("_")` (`mixins/components.py:98`), and
`TypedState.__setitem__` already exempts `_` keys from dirty tracking
(`mixins/base.py:65`). Nothing is stored on the component, so the shared
descriptor object holds no per-request reference.

**D3 — `TypedState.__str__` renders only when bound to a renderable component.**
Unbound, or bound to a component with no template, it returns `super().__str__()`
— the dict repr, unchanged. This is the single point where the two behaviours
diverge, and it is a two-line guard.

**D4 — Rendering does not go through `LiveComponent.render()`.** That method
raises for an unmounted component (`components/base.py:840`), and a descriptor-path
component is never mounted. A state-bound render builds its context from the
state (`dict(state)` plus `component_id`) and renders `template` /
`template_name` through the same `_render_template_with_fallback` entry the
mounted path uses. **`get_context_data()` is not called on this path** — it reads
instance attributes the descriptor does not have, and a component that declares
both a `State` and a `get_context_data()` reading `self.x` would silently render
defaults. That split is a documented contract, not an implementation detail:
*State is the context on the descriptor path.*

**D5 — The declared render cache becomes load-bearing.** `_cached_html` and
`_render_hash` are consulted and written by D3's render, and `_dirty` is
cleared on a successful render. A state whose hash matches the cached one
returns the cached HTML without re-rendering. This is what the docstring at
`mixins/base.py:39-42` already promises, and M5 is the evidence that nobody has
been keeping that promise.

**D6 — Registration in `_components` is in scope, but sequenced last.** The
documented auto-promotion gap (`components.md:236-266`) means descriptor
components are invisible to time-travel snapshots and session save/restore. A
renderable class-level component with per-view state that no snapshot can capture
is a support burden. `_assign_component_ids` (`mixins/components.py:131`) walks
`self.__dict__`, which is exactly where `__get__` stores the state — the walk
needs to consult `_component_descriptors` as well. This ships in S2, separately,
so a regression there cannot implicate the render path.

## Security

The rendered markup is a component template rendered through the standard entry
point, so escaping is inherited and unchanged: the Rust engine escapes the
state's values as it does for any other context value, and the result is marked
safe exactly as `render()` marks its output (`components/base.py:842-872`). The
state holds UI state only — which tab is active — by the stated contract
(`mixins/base.py:12-14`).

Three things to verify rather than assume in review:

1. **`_dj_view` is a back-reference from a serialized object to a view.** D2 relies
   on the `_`-prefix filter at `mixins/components.py:98` to keep it out of session
   payloads, and on `TypedState.__setitem__`'s `_`-exempt dirty tracking
   (`mixins/base.py:65`). Both are asserted by test, not by reading. A path that
   serializes the state dict wholesale — `normalize_django_value` is one candidate
   — must be checked, because `_`-prefixed *keys* are still keys.
2. **`__str__` is consulted in more places than `{{ }}`.** It is what every log
   line and error message that interpolates the state gets. D3's guard limits the
   change to renderable components, but the guard's default branch is the
   security-relevant one: it must be the dict repr, never an empty string.
3. **`mark_safe` on this path.** The rendered template is marked safe because the
   engine escaped it. D4's context is built from `dict(state)`, whose values are
   developer-set and may be `mark_safe`-wrapped by the developer. That is the same
   surface `render()` already has; it must not widen.

## Consequences

**Positive.** The class-level form renders, with per-view state, so the pattern
the framework recommends for state is no longer the one that cannot be drawn.
`{{ component }}` is one spelling for both forms. Three documented claims that
measure false are corrected. Three fields stop being decorative.

**Negative, accepted.** `str(state)` for a renderable descriptor changes meaning
from "the state" to "the markup". That is the point, and it is opt-in, but any
project that declared a `template` on a `State`-bearing component *before* this
change has no such component today — D1's opt-in cannot be reached accidentally,
because until this ADR nothing read the template on that path.

**Neutral.** The state dict gains two `_`-prefixed entries. `len(state)` is
unchanged; `state["_dj_view"]` is reachable but is not a key any serializer
should see (Security §1).

## Sequencing

- **S0 — documentation corrections, independent, may land first.** Fix items 1-3
  of §The four things the docs say that this tree does not: the stale `|safe`
  requirement in two places, and the `descriptor()` reference that has no
  implementation. No code change; `components.md` only.
- **S1 — D1-D5.** `TypedState.__str__` and its guard, `__get__`'s binding, and the
  state-bound render with the cache wired. Tests first.
- **S2 — D6**, the `_components` registration, separately, so its own regression
  cannot implicate S1.
- **S3 — `components.md`**: document the class-level form as renderable and the
  State-is-the-context contract from D4.

No Rust step. The rendering entry point is the existing one.

## Verification

- **The end-to-end oracle is a real page.** A `LiveView` with a class-level
  component that declares a template, fetched over HTTP, renders the component's
  markup — not a dict repr — with the state applied. Asserted on the served
  bytes, in the shape M3 uses, because M1/M2 alone would not have caught the
  LiveView path's own behaviour.
- **D1's opt-in is load-bearing.** Gating it off (render unconditionally) must
  make a test fail that asserts a template-less descriptor still yields the dict
  repr. Without that test, "opt-in" is a claim, not a property (#1468).
- **D5's cache is load-bearing.** Gating the hash comparison off must make a test
  fail that counts renders across two events with unchanged state. This is the
  test M5 says does not exist today; writing it is part of S1.
- **Per-view state survives.** Two view instances, one mutating its state, the
  other unchanged — the M4-style probe extended, asserting isolation. This is the
  property that rules out option C.
- **The instance form is unchanged.** `{{ counter }}` on an instance-assigned
  component renders as before; the whole existing `LiveComponent` suite stays
  green.
- **The eight descriptors are byte-identical.** A test asserting `str(state)` for
  each of the eight still yields the dict repr.

## Non-goals

- **Not a change to the eight first-party descriptors.** Option E.
- **Not a new spelling.** `{{ component }}` only; `{{ component.render }}` stays
  as broken as #2501 left it, and the docs already steer away from it.
- **Not a fix for `get_context_data` on the descriptor path.** D4 defines the
  contract as State-is-the-context; making both work is a separate question with
  its own ambiguity about precedence.
- **Not `theme_card`.** #2894 is adjacent (a container component that cannot take
  a body of template tags) and has its own decision to make about the tag's kind.
- **Not a performance project.** D5 removes a re-render that already should not
  happen; it is not a VDOM or engine change.
