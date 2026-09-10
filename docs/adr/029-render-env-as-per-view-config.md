# ADR-029: The Render Environment as Per-View Config (ADR-027 Addendum)

**Status**: Proposed
**Date**: 2026-09-09
**Citations**: `file:line` pinned to `main` at `1631e0ec`; every one asserted against its expected token at write time
**Deciders**: Project maintainers
**Related**:
- [ADR-027](027-template-variable-resolution-follows-django.md) — live-handle resolution; **Step 5** (`:439`) already decides the sidecar's fate
- [ADR-024](024-template-callable-auto-call.md) — `template_auto_call`, the per-view-field precedent this ADR copies
- `docs/architecture/VALUE_BOUNDARY.md` — the boundary map (§5 threading, §6.7 "read, not exercised")
- Issues/PRs: #2741 (the finding), #2751 (its probe, merged), #2728 (an instance of the class), #2743, #2744

---

## 1. Context — one finding, derived twice

**An actor render ignores the configured render environment.** Under `use_actors = True` (default `False`, `python/djust/live_view.py:359`), the `ViewActor` is spawned onto a tokio worker at `crates/djust_live/src/actors/session.rs:259`. That thread never runs the Python push (`_apply_render_env()`, `python/djust/mixins/rust_bridge.py:657`), so it reads each `thread_local!` cell at its Rust default. PR #2751 observed it: with `set_resolve_lazy(false)` on the calling thread, a `ViewActor` render on a proven-distinct worker returned the lazy answer, and the worker's own `resolve_lazy()` read `true`.

**Why it is a class and not a flag.** The `ViewActor`'s render entry `render_rust` (`crates/djust_live/src/lib.rs:1574`) is a three-line wrapper around `self.render()` (`:1575`), and `render()` *does* apply per-view config — `context.set_auto_call(self.template_auto_call)` at `:695`, and likewise at `:736` / `:1161` for the other two entries. `template_auto_call` survives the thread hop because it is a **field on the backend** (`:265`, set from Python at `:368`). The render environment does not survive it because it is a set of **`thread_local!` cells**. So the precise statement is:

> Per-view config that lives as a field on `RustLiveViewBackend` reaches every render entry on every thread. Per-view config that lives in a thread-local reaches only the thread that pushed it.

`ComponentActor::render` is worse: it builds a bare `Context::from_dict` (`crates/djust_live/src/actors/component.rs:378`) and applies no per-view config at all — not `auto_call`, not the dj-if markers, not the environment.

**The cells.** Derived from every `thread_local!` block in `crates/` (excluding `djust_vdom`'s id counter and a `cfg(test)` counter):

| cell | where |
|---|---|
| `RESOLVE_LAZY` | `crates/djust_core/src/lib.rs:2821` |
| `CONVERSION_DEPTH` | `crates/djust_core/src/lib.rs:3902` |
| `NUMBER_FORMAT`, `UNLOCALIZED_NUMBER_FORMAT` | `crates/djust_core/src/locale.rs:55`, `:56` |
| `ACTIVE_TZ` | `crates/djust_templates/src/timezone.rs:46` |
| `USE_L10N_STACK` | `crates/djust_templates/src/renderer.rs:1935` |
| `ACTIVE_LOOP_CACHE` | `crates/djust_templates/src/loop_cache.rs:879` |
| registry namespace (`CURRENT`/`NEXT`) | `crates/djust_templates/src/registry_scope.rs:8` |

**The cells are SET, not scoped.** `python/djust/render_env.py:188` documents this as the contract: every framework render entry re-pushes, nothing restores. Four RAII guards exist — `DepthGuard` (`djust_core/src/lib.rs:3908`), `UseL10nGuard` (`renderer.rs:1943`), `ActiveTimezoneGuard` (`renderer.rs:1955`), `LoopCacheGuard` (`loop_cache.rs:884`) — but they scope **mid-render** changes made by `{% timezone %}` / `{% language %}` / `{% localtime %}` / `{% localize %}` (`render_env.py:333`, `renderer.rs:1403`) back to the pre-render push; none scopes the push itself, because the push happens in Python before any Rust frame exists.

**#2728 is this class observed a second way.** `NUMBER_FORMAT` (a bare setter, no guard) survives `reset_djust_globals()`; a test rendering under `translation.override("fr")` leaves a comma decimal separator on the xdist worker, and a later test calling `_rust.render_template` directly — bypassing the framework push — inherits it: `'12,3'`. Production is unaffected because every framework entry pushes (pinned by `test_timezone_render_2209.py`); the leak is at the test-isolation boundary and is fixed there.

**Not a bug on the default path.** With config `False` and the thread-local deliberately poisoned `True`, a real render across `sync_to_async(thread_sensitive=True)` returns the configured answer — `_sync_state_to_rust` pushes per render (#2743 review). The ambient cell is a design smell there; it is a bug only where the push is skipped.

## 2. Decision

Carry the render environment the way `template_auto_call` is already carried, and make every render entry — including the actor ones — apply it.

1. **A `RenderEnv` value** in `djust_core` holding what survives ADR-027 Step 5: timezone, the two number formats, the l10n stack. (`RESOLVE_LAZY` rides on it only until Step 5 deletes the flag; `CONVERSION_DEPTH`, the loop cache and the registry namespace are per-render scratch, not configuration, and stay as they are.)
2. **A field on `RustLiveViewBackend`**, set from Python beside `set_template_auto_call` (`:368`), applied by each render entry beside `set_auto_call` (`:695`, `:736`, `:1161`). Because `render_rust` delegates to `render()`, the `ViewActor` gets it for free. `ComponentActor::render` (`component.rs:378`) is changed to apply per-view config — this is the #2741 fix, and it fixes `auto_call` and the markers on that path too.
3. **An explicit parameter on the plain entry.** `render_template` (`lib.rs:2137`) already takes `auto_call` (`:2140`), `string_if_invalid`, `autoescape`; the environment belongs beside them.
4. **An entry guard.** Each Rust render entry installs the environment into the existing cells under an RAII guard that restores the previous values on drop — the idiom the four existing guards already use — so a render leaves the cells as it found them. This is the "set, not scoped" fix, done inside the frame that finally exists once the environment arrives as a parameter.

The eight `resolve_lazy()` readers (six conversion-time: `stated_len_is_too_large_to_enumerate`, `list_repr_is_this_objects_own_spelling`, `opaque_gate` ×3, `opaque_value`; two render-time: `context.rs:1800`, `renderer.rs:5461`) keep reading the cell. Nothing in this ADR threads an environment through `FromPyObject` or the `extract::<Value>()` sites; after Step 5 deletes the six conversion-time arms, conversion needs no environment at all.

## 3. Sequencing against ADR-027 Step 5

ADR-027 `:435` settles that Step 5 deletes `template_resolve_lazy` **and** its enumeration arms together, in the first minor after a full release has soaked (1.3.0). (`:443` still says "the flag stays one release as a kill-switch, removal at 2.0" — a sentence the settled decision superseded and did not update; it should be tidied.)

1. **Now, as a bugfix (#2741):** the field, the actor entries applying it, the `ComponentActor` fix, the entry guard. #2751's probe is written to go red at this point — flip its expectation, do not delete it; its thread-distinctness harness is the proof the fix reached the worker.
2. **1.3.0, Step 5 as written:** delete the flag from the field. The field shrinks; no signature unwinds.

## 4. Non-goals

- **No channel consolidation.** The sidecar's narrowing to top-level models, the alias-fallback deletion, and `_protect_sidecar_tree`'s removal are ADR-027 Step 5 (`:439`) — already decided. `Context::aliases` **stays**: `is_safe` (`crates/djust_core/src/context.rs:1202`) resolves a `mark_safe` grant through it, and deleting it would regress the #2375/#2334 XSS-parity fix. Carrying a model as an `Encoded` (a live handle on a model) would reverse ADR-027 (b)(1) and needs its own ADR arguing against that ADR's table on the client JSON payload, the msgpack rolling-deploy pin and container change detection.
- **No second serialization floor.** No Python-side dotted-path helper with a denylist. The floor has one authority — `_attr_is_serializable` in Python, the free `protect_sidecar_strict` in Rust — and #2734 / FINDING-14 / FINDING-15 are what happens when a second one appears.
- **No performance claim.** Neither the field nor the guard is a performance change. The per-render costs in this area were measured and fixed elsewhere (#2733, #2744); `deep_fingerprint` (`python/djust/change_detection.py:90`) remains O(context) in Python regardless, which is why no Rust-side laziness makes an unread context free.

## 5. Verification

- The #2751 probe flips from asserting the defect to asserting the configured answer on the worker.
- A `ComponentActor` parity test: `auto_call`, markers and environment reach a component render.
- The #2728 hygiene test (a `fr` push followed by a direct render) stays green, and the entry guard makes it structurally green rather than reset-green.
- `test_timezone_render_2209.py` keeps pinning the set of framework entries that push.

## 6. Record of what the previous draft got wrong

Kept so the corrections are findable. Phase 2 re-decided ADR-027 Step 5 and added the `Context::aliases` deletion (a security-parity regression) and an un-argued Option A. Phase 1 threaded an immutable `&RenderEnv` through conversion — a field scheduled for deletion, and a shape that cannot represent `{% timezone %}` changing the environment mid-render. "28 render dispatches" was 6 `sync_to_async` sites and 15 awaited render calls in `runtime.py`. `view.rs:560` is the child `ComponentActor` spawn, `supervisor.rs:111` the `SessionActor`; the `ViewActor` spawn is `session.rs:259`. Two of four gates defended claims the document did not make. The "observable bug" was, at the time, inferred; #2751 then observed it.
