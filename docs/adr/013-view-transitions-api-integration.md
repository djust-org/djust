# ADR-013: View Transitions API Integration in `applyPatches`

**Status**: Proposed
**Date**: 2026-04-26
**Deciders**: Project maintainers
**Target version**: v0.8.5 candidate
**Related**: [ROADMAP](../../ROADMAP.md) "View Transitions API" parity-tracker row + Quick Win #23
**Withdrawn PR**: [#1092](https://github.com/djust-org/djust/pull/1092) — first attempt; Stage 11 review found load-bearing design flaw, full re-design needed before re-attempting

---

## Summary

The browser's [View Transitions API](https://developer.mozilla.org/en-US/docs/Web/API/View_Transitions_API) (`document.startViewTransition()`) lets pages animate between two DOM states with a single call: the browser captures a pre-state snapshot, runs a callback that mutates the DOM in place, captures the post-state, and animates the difference. Per-element `view-transition-name` CSS gives shared-element transitions ("hero image flies to detail page") for free. djust's WebSocket-driven `applyPatches` mutates DOM in place after every server message — exactly the shape `startViewTransition` expects.

The first attempt (PR #1092) wrapped `applyPatches` in `startViewTransition()` opt-in via `<body dj-view-transitions>`. Stage 11 review found that **the wrap pattern straddles sync/async wrong**: the W3C spec runs the callback in a microtask (async), not synchronously. PR #1092's `let innerResult = true; document.startViewTransition(() => { innerResult = _applyPatchesInner(...); }); return innerResult;` returns `true` before the inner call has actually happened — silently breaking the failure-detection contract that real callers (`02-response-handler.js:109`) branch on. JSDOM tests passed because the test stub invoked the callback synchronously, lying about real-browser behavior.

This ADR re-designs the integration around the actual async semantics, picks one of three viable shapes, and locks the test strategy.

## Context

### What djust already does in `applyPatches`

`python/djust/static/djust/src/12-vdom-patch.js:1501` — `applyPatches(patches, rootEl = null)` is a **synchronous** function called from `02-response-handler.js:109`:

```javascript
const success = applyPatches(data.patches);
if (!success) {
    // Caller branches on return value: triggers a full re-render request
    // upstream when patches fail to apply (path-mismatch, missing dj-id, etc.)
}
```

The function returns `true` on success and `false` when one or more patches fail. The boolean is consumed by the response handler to decide whether to fall back to a full HTML replace.

### What `document.startViewTransition()` actually does

Per W3C CSS View Transitions Module Level 1 ([spec](https://drafts.csswg.org/css-view-transitions-1/#dom-document-startviewtransition)):

1. Returns a `ViewTransition` object **immediately**.
2. Schedules an animation frame.
3. Captures the old DOM state snapshot.
4. **In a microtask after the next animation frame**, invokes the user-supplied callback.
5. Awaits the callback's returned Promise (if any).
6. Captures the new DOM state snapshot.
7. Animates between the two snapshots; resolves `transition.finished` when done.

The critical mismatch with PR #1092: **the callback runs after `startViewTransition()` returns**, not synchronously inside it. So `let innerResult = true; document.startViewTransition(() => { innerResult = ... }); return innerResult;` returns `true` deterministically — `innerResult` is never updated before the function exits.

PR #1092's vitest stub at `tests/js/view-transitions.test.js:79-89` invokes the callback synchronously inside `vi.fn`, masking the bug. Stage 11 reviewer caught it via spec read-through.

### Browser support

- Chrome 111+ (Mar 2023)
- Edge 111+ (Mar 2023)
- Safari 18+ (Sep 2024)
- Firefox: in active development as of 2026-04 (no stable release yet)

Plus `window.matchMedia('(prefers-reduced-motion: reduce)')` users — should bypass.

### Why opt-in (not default)

Wrapping every WS update in a transition would animate keystrokes, cursor presence, streaming markdown, autosave indicators — every per-keystroke patch. The animation overhead (typically ~250ms cross-fade) would visibly stutter rapid updates. Opt-in via `<body dj-view-transitions>` matches existing djust attribute conventions (`dj-cloak`, `dj-prefetch`) and lets users scope the feature to navigation-style transitions where it adds value.

## Options considered

The core problem: `applyPatches` is sync (callers branch on its boolean return), but the View Transitions API runs its callback asynchronously. Three shapes can resolve this.

### Option A: Make `applyPatches` async

Convert the entry-point signature to `async function applyPatches(...)` returning `Promise<boolean>`. Callers `await` it. The wrap is straightforward:

```javascript
async function applyPatches(patches, rootEl = null) {
    if (shouldUseViewTransition()) {
        let innerResult = true;
        const transition = document.startViewTransition(() => {
            innerResult = _applyPatchesInner(patches, rootEl);
        });
        await transition.updateCallbackDone;
        return innerResult;
    }
    return _applyPatchesInner(patches, rootEl);
}
```

**Pros:** Correct return contract preserved. Failures propagate. Simple mental model.

**Cons:** Large blast radius. Every caller of `applyPatches` becomes async. Greppable callers (`02-response-handler.js:109`, `03-websocket.js:498-700` for sticky/child-update paths, `13-lazy-hydration.js`, `15-uploads.js` for upload-progress patches, plus internal patcher recursion via the InsertHTML/morph paths). All become `await applyPatches(...)`. Risk: missing one site silently fire-and-forgets the patch. Also: returning a Promise from a function that historically returned boolean breaks any third-party hook code that assumes sync semantics (`window.djust.applyPatches` is reachable in the public surface).

### Option B: Fire-and-forget wrap; return `true` always when wrapped; lose failure-detection inside the transition

```javascript
function applyPatches(patches, rootEl = null) {
    if (shouldUseViewTransition()) {
        // Wrap path: failure inside the transition is not observable to the
        // sync caller. The wrapper logs failures via a side channel
        // (CustomEvent) but the sync return value is always true.
        const transition = document.startViewTransition(() => {
            const ok = _applyPatchesInner(patches, rootEl);
            if (!ok) {
                document.dispatchEvent(new CustomEvent('djust:patches-failed', {
                    detail: { patches }
                }));
            }
        });
        // Track unhandled rejection so the transition doesn't leak.
        transition.updateCallbackDone.catch(err => {
            console.error('[djust] applyPatches threw inside View Transition:', err);
            transition.skipTransition();
        });
        return true; // optimistic
    }
    return _applyPatchesInner(patches, rootEl);
}
```

**Pros:** Sync signature preserved. Caller code unchanged. Smallest blast radius.

**Cons:** Failure handling is downgraded inside the wrap path. The full-re-render fallback at `02-response-handler.js:109` never fires when patches fail under View Transitions — users see a half-animated broken DOM. The `djust:patches-failed` event is a side channel that no caller currently listens for; would need to wire up at least one consumer or document it as observability-only. Failure recovery becomes opt-in instead of default — regression vs the no-wrap path.

### Option C: `transition.updateCallbackDone` side channel; wrap returns optimistically but exposes a Promise hook

```javascript
function applyPatches(patches, rootEl = null) {
    if (shouldUseViewTransition()) {
        let innerResult = true;
        const transition = document.startViewTransition(() => {
            innerResult = _applyPatchesInner(patches, rootEl);
        });
        // Expose the inner-result Promise so callers that NEED failure
        // detection can await it. Default callers (sync path) get true.
        transition.updateCallbackDone
            .then(() => {
                if (!innerResult) {
                    document.dispatchEvent(new CustomEvent('djust:patches-failed', {
                        detail: { patches, transitionId: transition.id }
                    }));
                }
            })
            .catch(err => {
                console.error('[djust] applyPatches threw inside View Transition:', err);
                transition.skipTransition();
                document.dispatchEvent(new CustomEvent('djust:patches-failed', {
                    detail: { patches, error: err }
                }));
            });
        return true;
    }
    return _applyPatchesInner(patches, rootEl);
}
```

**Pros:** Sync signature preserved (Option B's win) AND failure-detection survives via the event channel. Caller code unchanged for the common case. Specialized callers (`02-response-handler.js:109` could subscribe once) get a hook.

**Cons:** Still optimistic by default. The event-channel subscription is async — the full-re-render fallback fires *after* the broken DOM is already animated, so the user sees the stutter regardless. Two ways to learn about failure (return value for the sync path, event for the wrap path) is a forking surface.

### Option D (rejected): Skip the wrap entirely, document the API as "set `view-transition-name` CSS and call `document.startViewTransition()` yourself"

User implements wrap in their own dj-hook. djust does nothing. Already supported today — `view-transition-name` works through djust's renderer because it's just a CSS property.

**Pros:** Zero djust code. Zero risk.

**Cons:** Misses the feature's value proposition (zero-config animation between server-driven states). Users would have to wire WS-event listeners + `startViewTransition` in app code, defeating the "djust handles transport, you handle UI" pattern. Equivalent to "we'll never ship this."

## Decision

**Option A — make `applyPatches` async.**

Rationale:
1. **Failure detection is load-bearing.** The full-re-render fallback at `02-response-handler.js:109` exists because real production apps hit patch-application failures (path mismatches, dj-id collisions, race conditions). Downgrading that to optimistic-then-async-event under the wrap path is a real regression — half-animated broken DOM is worse than no animation. Options B and C both accept this regression for smaller blast radius; the trade is wrong.
2. **Blast radius is bounded and discoverable.** `grep -rn "applyPatches(" python/djust/static/djust/src/` returns a finite set of call sites (< 10). Each one becomes a single `await` addition. The pre-commit `eslint` hook catches any missed `await` via `no-floating-promises` if we enable that rule.
3. **The public-surface concern is mitigated.** djust does expose `applyPatches` via the bundle, but no third-party code in scrape-able example apps consumes it directly. Documenting the signature change in CHANGELOG is sufficient for the public-surface migration story.
4. **Future-proofing.** Async patches open the door to features that need it: streaming patch application, abort on user input, transition-aware WS-update buffering. Options B/C close those doors prematurely.

The async-cost is one extra microtask per patch — negligible relative to the existing patch loop.

## Design sketch

```javascript
// 12-vdom-patch.js

/**
 * Apply VDOM patches to the DOM. Returns true on success, false if any
 * patch failed (caller may trigger a full re-render fallback).
 *
 * Async because the View Transitions API integration awaits the inner
 * patch loop to run inside `document.startViewTransition()`'s callback,
 * which the spec executes asynchronously after a frame capture. Direct
 * (no-wrap) callers see the same boolean return as before; the function
 * just resolves to it instead of returning it synchronously.
 */
async function applyPatches(patches, rootEl = null) {
    if (!patches || patches.length === 0) {
        return true;
    }

    if (_shouldUseViewTransition()) {
        let innerResult = true;
        const transition = document.startViewTransition(() => {
            innerResult = _applyPatchesInner(patches, rootEl);
        });
        try {
            await transition.updateCallbackDone;
        } catch (err) {
            console.error('[djust] applyPatches threw inside View Transition:', err);
            transition.skipTransition();
            return false;
        }
        return innerResult;
    }

    return _applyPatchesInner(patches, rootEl);
}

function _shouldUseViewTransition() {
    if (typeof document === 'undefined') return false;
    if (typeof document.startViewTransition !== 'function') return false;
    if (!document.body) return false;
    if (!document.body.hasAttribute('dj-view-transitions')) return false;
    // Honor user motion preference.
    if (typeof window !== 'undefined' && window.matchMedia &&
        window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        return false;
    }
    return true;
}

// _applyPatchesInner is the existing patch loop body; unchanged
function _applyPatchesInner(patches, rootEl = null) { /* ... */ }
```

Caller migration (mechanical):

```javascript
// Before:
const success = applyPatches(data.patches);
if (!success) { /* fallback */ }

// After:
const success = await applyPatches(data.patches);
if (!success) { /* fallback */ }
```

Every call site is inside an `async function` already (handlers, event listeners, etc.) — no `Promise` plumbing changes.

## Consequences

### Positive

- **Correctness.** Failure detection survives the wrap. Full-re-render fallback fires before broken DOM is animated.
- **Future-proofing.** Streaming-patches, user-input-abort, transition-aware buffering all unblocked.
- **Reduced-motion respected.** Users who set `prefers-reduced-motion: reduce` bypass the wrap automatically.

### Negative

- **API signature change.** `applyPatches` returns `Promise<boolean>` instead of `boolean`. Documented in CHANGELOG as a breaking-but-trivial migration; users with hooks calling it directly add `await`. Pre-bundled `client.js` consumers don't notice (the bundle's internal callers are migrated in the same PR).
- **Test infrastructure update.** JSDOM tests can't validate the actual transition animation — the existing test stub at `tests/js/view-transitions.test.js:79-89` will need to be redesigned to test the async correctness without the spec lie. Real-browser smoke test via MCP `djust-browser` ([list at top of session]) for the animation itself.

## Open questions (resolved in implementation PR)

- **Hook into `reinitAfterDOMUpdate` timing.** Currently `02-response-handler.js:1051` runs `reinitAfterDOMUpdate()` immediately after `applyPatches` returns. Under the new async signature, it runs after the inner patch loop completes (correct). Verify dj-hook callbacks see a fully patched DOM.
- **Nested `startViewTransition` calls.** Spec: only one transition at a time. A second `startViewTransition` while one is in flight skips the first. With `start_async` completions and PR #1091's deferred callbacks, rapid back-to-back patches in an opted-in app may see visible animation cancellation. Implementation: add `console.debug` log when `transition.skipTransition()` fires due to an in-flight transition, leave behavior as spec-default (skip), document trade-off.
- **Focus state save/restore inside the wrapped callback.** With async timing, `saveFocusState` runs *inside* `_applyPatchesInner` after the snapshot is captured. The focused-element styling (caret, focus ring) may differ between the two snapshots if focus moves. Implementation: real-browser test with focused-input patch; if visual regression, save focus *before* the wrap kicks in.
- **Test strategy.**
  - JSDOM unit tests: cover the boolean-return contract under async (await-then-assert), `_shouldUseViewTransition` decision matrix (API present, attribute present, `prefers-reduced-motion`, `document.body === null`).
  - Real-browser smoke test via `mcp__djust-browser__navigate` + `mcp__djust-browser__benchmark_event`: visit a fixture page with `<body dj-view-transitions>`, trigger a patch, screenshot the mid-transition state. Manual verification one-time.
- **Public-surface migration note.** Add a one-line CHANGELOG breaking-change note: "`window.djust.applyPatches` now returns `Promise<boolean>`. Code that awaited the old sync return must add `await`."

## Out of scope

- **Cross-document transitions (View Transitions Level 2).** Different spec, different APIs, applies to MPA navigation — not WS-driven SPA patches. ROADMAP "Investigate & Decide" already lists this as a v0.9.0+ candidate.
- **Per-handler animation opt-out.** A `dj-no-transition` attribute on the patched element (skip transition for THIS update) is plausible but adds API surface. Defer until user demand surfaces.
- **`transition.ready` hook.** Exposing the `ViewTransition` object to user code is a feature in itself. Out of scope for the integration ADR.

## Alternatives briefly considered

- **`requestAnimationFrame` polyfill for non-supporting browsers.** Pure CSS transitions can approximate the effect on Firefox. Rejected: re-implementing the spec inside djust contradicts manifesto principle #1 ("Complexity Is the Enemy"). Wait for Firefox.
- **Server-side opt-in via a header.** Backend tells client "wrap this patch in a transition." Rejected: animation is a client-side concern; the body attribute is the right declarative surface.

## Implementation plan (after ADR approval)

1. Branch `feat/view-transitions-api-v2` from `main`.
2. Refactor `applyPatches` per the design sketch (~30 lines including JSDoc).
3. Migrate all internal callers to `await applyPatches(...)`. Greppable list:
   - `02-response-handler.js:109`
   - any other site found via `grep -n "applyPatches(" python/djust/static/djust/src/`
4. Add `_shouldUseViewTransition` helper with `prefers-reduced-motion` check.
5. Rewrite `tests/js/view-transitions.test.js` to test the async correctness (~12 cases): boolean return under success/fail, `_shouldUseViewTransition` decision matrix, exception-in-callback path, `prefers-reduced-motion` opt-out, `document.body === null` early-render guard.
6. CHANGELOG `[Unreleased]` — `### Changed` for the async signature; `### Added` for the View Transitions opt-in.
7. ROADMAP — Quick Win #23 + Phoenix LV Parity Tracker `View Transitions API` row marked shipped.
8. Real-browser smoke test via MCP `djust-browser`.
9. Stage 11 review (subagent), Stage 13 re-review.

## Changelog

| Date | Change |
|---|---|
| 2026-04-26 | Initial draft after PR #1092 Stage 11 review found the sync-callback bug |

🤖 Drafted via Stage 11 escalation from PR #1092
