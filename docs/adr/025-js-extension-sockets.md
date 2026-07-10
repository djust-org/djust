# ADR-025: JS extension sockets — `JS.ext.*` custom commands + hardened `dj-hook` values/targets

**Status**: Proposed
**Date**: 2026-07-10
**Deciders**: Project maintainers
**Related**:
- [ADR-020](020-island-attribute-component-interop.md) — `dj-island` opaque-subtree interop; ADR-025 provides the *behavioral* extension sockets, ADR-020 the *rendering-boundary* one. The future "official adapters" milestone (explicitly out of scope here) would compose both.
- `docs/website/guides/js-commands.md` — the existing 11-command `JS.*` system this ADR opens up
- `docs/website/guides/hooks.md` — the existing `dj-hook` lifecycle system this ADR hardens

## Context

djust already provides three JavaScript extension mechanisms: `dj-hook` lifecycle
hooks (`19-hooks.js`, Phoenix-hooks/Stimulus-controller equivalent with
`pushEvent`/`handleEvent`), colocated hooks (`32-colocated-hooks.js`,
Phoenix 1.1 parity), and JS Commands (`26-js-commands.js` +
`python/djust/js.py`, the `Phoenix.LiveView.JS` analog with 11 chainable
ops). A survey against Phoenix / Livewire / Hotwire plus the known downstream
pain points (#1724 widget teardown, #1848 inline-script-in-dj-root) identified
five gaps; this ADR addresses the two "build now" ones:

- **B — the command set is closed.** `COMMAND_TABLE` in `26-js-commands.js`
  is a hard-coded map of 11 ops. Users cannot add `scroll_to`,
  `clipboard_copy`, `confetti`, or a wrapper around any third-party library
  action, and there is no escape hatch short of writing a full `dj-hook`.
- **D — hooks lack a data-passing and targeting contract.** A hook gets
  `this.el` and raw `dataset` strings; every hook re-invents attribute
  parsing (`data-values="1,2,3"` + manual `split`/`parseInt`), and there is
  no scoped element lookup. There are also no TypeScript definitions for the
  public `window.djust` surface, hurting editor/AI autocomplete
  ("AI-Ready by Design").

Gaps explicitly deferred or rejected (see Alternatives): a client reactivity
layer (the Alpine gap), official library adapters, and the #1848 page-JS
lifecycle fix (a bug, tracked separately, not an extension).

## Decision

Ship two independent, PR-sized features that open the two systems djust
already owns. No new paradigm, no new dependency, no build step, no eval.

### Feature B — `JS.ext.*` namespaced dynamic custom commands

**Python surface.** The `JS` factory gains one attribute, `JS.ext`, whose
`__getattr__` accepts any snake_case name and returns a chainable op builder:

```python
self.jump = JS.ext.scroll_to(to="#top", smooth=True).add_class("flash")
```

Built-ins are untouched: `JS.shwo()` still raises `AttributeError` at mount
with a real traceback. The `.pyi` declares `ext` as a dedicated factory type
whose `__getattr__` returns `Callable[..., JSChain]` — the mypy "any attribute
is valid" hole is confined to `ext.*`; the 11 built-ins stay strictly typed
(ADR-023 posture preserved).

**Templates.** Unchanged. Custom ops ride the same interpolation as built-ins
(`dj-click="{{ jump }}"`, or inline `dj-click="{{ JS.ext.confetti() }}"`) on
any `dj-*` event attribute.

**Wire format.** Custom ops serialize into the existing ops array with a
dotted prefix:

```json
[["ext.scroll_to", {"to": "#top", "smooth": true}], ["add_class", {"names": ["flash"]}]]
```

The prefix makes the namespace explicit on the wire, keeps the targeting
kwargs (`to` / `inner` / `closest`) top-level so custom commands inherit
target resolution unchanged, and makes collision with any future built-in
structurally impossible.

**Client surface.** A user registry alongside `COMMAND_TABLE`:

```javascript
// static/app.js — the single declaration site
window.djust.commands.register('scroll_to', (targets, args, originEl) => {
    targets.forEach(el => el.scrollIntoView({behavior: args.smooth ? 'smooth' : 'auto'}));
});
```

- `fn(targets, args, originEl)` — `targets` is the already-resolved element
  list (same `to`/`inner`/`closest` semantics as built-ins; defaults to
  `[originEl]`). A returned Promise is awaited in chain order (matching
  `execPush`).
- `register(name, fn)` **throws** if `name` is a current built-in or contains
  a dot. If djust later promotes a name to built-in, the user's `register()`
  call starts throwing at page load — a loud upgrade failure, never a silent
  behavior change. Re-registering an `ext` name overwrites with a
  `djustDebug`-gated warn (hot-reload friendly).
- Dispatch: `executeOps` routes op names starting with `ext.` to the user
  registry. Unknown `ext.` op at execution time → DEBUG error overlay
  (`36-error-overlay.js`) with a did-you-mean suggestion (edit distance over
  the registry); `console.error` in production.
- Parity surfaces: `this.js().ext('scroll_to', {...})` in hooks and
  `window.djust.js.ext('scroll_to', {...})` for direct JS chains.

**Security/CSP.** No eval anywhere; command implementations are user-authored
static JS. Op names and args are template-author/server-controlled — the same
trust level as `dj-click` today. The registry does not widen the injection
surface: invoking a registered command requires the same attribute-injection
capability that already implies XSS.

### Feature D — `dj-hook` typed values, targets, and `djust.d.ts`

**Typed values.** `dj-hook-value-<kebab-name>` attributes are exposed as
`this.values.<camelName>` inside the hook, coerced JSON-first with raw-string
fallback:

```html
<canvas dj-hook="Chart"
        dj-hook-value-points="[1,2,3]"
        dj-hook-value-animated="true"
        dj-hook-value-title="Sales"
        dj-hook-value-code='"007"'></canvas>
```

```javascript
mounted() {
    this.values.points     // [1, 2, 3]   (Array — JSON.parse)
    this.values.animated   // true        (Boolean — JSON.parse)
    this.values.title      // "Sales"     (string fallback: JSON.parse failed)
    this.values.code       // "007"       (explicit JSON string beats numeric coercion)
}
```

`this.values` is a **live Proxy** reading the element's current attributes on
each access — after a server morph updates `dj-hook-value-*`, `updated()`
observes fresh values with zero staleness machinery. Coercion rule (documented,
never throws): try `JSON.parse`; on failure the raw attribute string. A
literal string that looks like JSON is written JSON-quoted
(`dj-hook-value-code='"007"'`). `this.values` is **read-only** in v1 — the
Proxy `set` trap throws a `TypeError` (Stimulus-style attribute write-back is
deferred). Per-value `<name>ValueChanged` callbacks (Stimulus parity) are
likewise deferred — `updated()` + live reads cover the need.

The `dj-hook-value-*` prefix is used because bare `dj-value-*` is already
taken by the event-params system (`09-event-binding.js` merges `dj-value-*`
into event payloads) and `dj-target` by scoped updates.

**Targets.** Descendants marked `dj-hook-target="canvas"` are reachable as
`this.target("canvas")` (first match) / `this.targets("canvas")` (all
matches), queried live within `this.el`'s subtree. Nested hooks are not
excluded from the query scope in v1 (documented caveat, Stimulus-style
scoping deferred).

**TypeScript definitions.** A hand-maintained `djust.d.ts` is added to the
wheel's static dir (`python/djust/static/djust/djust.d.ts`) covering the
public `window.djust` surface: the hook definition shape (lifecycle methods,
`this.el/values/target/targets/pushEvent/handleEvent/js`), `commands.register`,
and the JS chain builder. Users reference it from `jsconfig.json`/`tsconfig.json`
or a `/// <reference path>` — no build step required to benefit (editors and
AI assistants pick it up for autocomplete/checking).

## Build vs. integrate an existing library

The design was explicitly weighed against adopting existing libraries; the
decision is to integrate the **ideas**, not the **artifacts**:

- **For D the off-the-shelf equivalent is Stimulus** (~11 KB gz). But djust
  already ships a controller system (`dj-hook`) that cannot be removed, so
  adoption means two overlapping systems; Stimulus has no server bridge, and
  the bridge (`pushEvent`/`handleEvent`/`js()`/morph lifecycle) is precisely
  the part djust had to build and must maintain anyway. D is ~0.7 KB extending
  what exists, and deliberately copies Stimulus's proven *vocabulary*
  (values/targets, kebab→camel) so the concepts transfer.
- **For B there is no off-the-shelf equivalent** — server-composable client
  command chains are a LiveView-ism, and no external library can open djust's
  own `COMMAND_TABLE`. B copies Phoenix's `LiveView.JS` vocabulary.
- **Bundling a reactivity framework (Alpine) is rejected.** Livewire+Alpine
  and Hotwire+Stimulus work because one maintainer owns both halves and
  co-develops morph-awareness in lockstep. djust's Rust VDOM differ knows
  nothing of a third-party lib's client-owned DOM, and every recent hard
  client bug (#1988/#1989, #2033, #1724) was exactly the
  client-owned-DOM-vs-server-morph class. Phoenix faced the same choice and
  built hooks + `JS` commands rather than adopting Stimulus. Also contra the
  manifesto ("Own Your Stack": no npm, no CDN, no registry to trust).
- **Marginal cost decides it**: ~2 KB gz total extending two systems djust
  owns and maintains regardless, vs ≥11 KB adding a parallel system that
  still needs a bespoke bridge, upstream release coupling, and double
  documentation.

## Non-goals

- **No client reactivity engine** (`dj-data`/`dj-show` state layer) — the
  ~15 declarative `dj-*` behaviors + JS commands + hooks cover the field's
  common cases; the remainder does not justify a second source of truth.
- **No official library adapters yet** (Chart.js/Sortable/editor glue).
  Deferred to a follow-up milestone once these sockets prove out; B/D are the
  substrate adapters would plug into ("user brings the library, djust ships
  the morph-safe glue").
- **No Python-side registration** for custom commands (rejected — see
  Alternatives) and **no bare `JS.<name>` passthrough**.
- **#1848** (inline `<script>` in dj-root not executing after mount morph) is
  a bug with its own issue, not part of this ADR.

## Alternatives considered

1. **Explicit Python registration** (`register_js_command("scroll_to")` →
   `JS.scroll_to()`): mount-time `AttributeError` for all typos, but the name
   is declared twice, and class-body chains (`class V: jump = JS.scroll_to()`)
   evaluate at import time — a registration-ordering trap (apps-ready vs
   import order). Rejected for ceremony + the ordering footgun.
2. **Bare dynamic passthrough** (`JS.scroll_to()` with no namespace): cleanest
   reading, but it *removes* the existing `AttributeError` protection from
   built-in typos (`JS.shwo()` would silently become a custom op), forces a
   `.pyi` `__getattr__` that blinds mypy across the whole factory, and a
   future built-in named like a user command silently changes meaning on
   upgrade. Rejected; `JS.ext.*` keeps the zero-ceremony authoring while
   fencing all three hazards.
3. **`JS.exec("name", ...)` only**: impossible to typo into silence, but
   chains and inline-template usage read second-class. Rejected on
   ergonomics; note `ext('name', args)` *is* retained as the JS-side chain
   surface where dynamic attributes are less natural.
4. **Adopt Stimulus / bundle Alpine**: see "Build vs. integrate" above.
5. **Declared value types (Stimulus parity) or hybrid**: declared-only adds
   per-hook ceremony; hybrid doubles the mechanism surface. JSON-first
   auto-coercion chosen, with JSON-string quoting as the disambiguation
   escape hatch.

## Consequences

**Budget.** B ≈ 0.5–1 KB gz (dispatch + registry + overlay hint; reuses
`executeOps`); D ≈ 0.7 KB gz (Proxy + coercion + targets). Each feature is
measured against the ≤2 KB-gz-per-feature client budget rule; `djust.d.ts` is
types-only (never shipped to browsers by templates).

**Failure modes.**
- Built-in typo in Python → mount-time `AttributeError` (unchanged).
- `ext` typo → first-interaction DEBUG error overlay + did-you-mean;
  `console.error` in prod. This is the accepted cost of dynamic passthrough,
  identical to the guarantee Alpine/Stimulus give for their registries.
- `register()` collision with a built-in → throw at registration (loud at
  page load, including after upgrades).
- Unparseable `dj-hook-value-*` JSON → raw string fallback, never a throw.

**Testing** (repo canon applies):
- Wire-format pin for `ext.*` ops — the ops array is a Python↔JS contract
  (#1448 class).
- JS tests: registry register/dispatch/unknown-op + did-you-mean/async chain
  order/target resolution; values coercion matrix (array/bool/number/string
  fallback/JSON-quoted string); live re-read after a simulated morph;
  targets first/all/subtree scoping.
- Integration tests drive real DOM events through the delegated listener
  (#1196), not method invocation only; each new test carries a gate-off
  sibling (#1468).
- Python tests: `JSChain` serialization of `ext` ops; built-ins still raise
  `AttributeError`; `.pyi` strictness pinned (mypy sees `JS.shwo` as an
  error, accepts `JS.ext.anything`).
- Bundle-size measurement recorded in the PR (gzipped delta per feature).

**Rollout.** Two independent PRs — B then D, no shared foundation, low blast
radius each. Docs ride along (`js-commands.md` gains a "Custom commands"
section; `hooks.md` gains "Typed values & targets"; both `feat:` PRs update
CHANGELOG). The follow-up adapters milestone is unblocked but not committed.
