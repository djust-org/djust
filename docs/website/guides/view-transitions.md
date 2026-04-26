---
title: View Transitions
nav_order: 36
---

# View Transitions API integration

djust integrates the browser's [View Transitions API][mdn] so every
server-driven VDOM patch can animate between states with a single body
attribute and zero JS animation code.

[mdn]: https://developer.mozilla.org/en-US/docs/Web/API/View_Transitions_API

## Quickstart — global cross-fade on every patch

Add `dj-view-transitions` to `<body>`:

```html
<body dj-view-transitions>
  ...
</body>
```

Every patch from a LiveView event now cross-fades the affected DOM
between the pre- and post-state. No per-component opt-in, no JS
animation library, no FLIP plumbing.

## Browser support

| Browser | Version | Behavior |
|---|---|---|
| Chrome | 111+ | Full support |
| Edge | 111+ | Full support |
| Safari | 18+ | Full support |
| Firefox | (in dev) | Graceful degrade — patches apply, no animation |

About 85% of djust users today see the polish; the remaining 15%
(Firefox) see the same instant patches as before. No regression.

## Accessibility — `prefers-reduced-motion`

Users who set `prefers-reduced-motion: reduce` automatically bypass the
animation. Patches apply instantly. Honored by djust internally — no
config required.

## Shared-element transitions via `view-transition-name`

Animate matching named elements between two completely different DOM
trees (the "card flies into hero on detail page" pattern). Two CSS
declarations are all you need:

```css
.user-card .avatar {
    view-transition-name: var(--avatar-name);
}
.user-detail .hero-image {
    view-transition-name: var(--avatar-name);
}
```

Then in your view:

```python
def get_context_data(self, **kwargs):
    return {
        "user": self.user,
        "avatar_view_transition_name": f"avatar-{self.user.id}",
    }
```

```html
<style>
    .user-card .avatar { --avatar-name: {{ avatar_view_transition_name }}; }
    .user-detail .hero-image { --avatar-name: {{ avatar_view_transition_name }}; }
</style>
```

The browser captures the avatar's pre-state position/size, runs the
patch (which replaces the card subtree with the detail subtree), and
animates the avatar morphing from card position to hero position.
Other elements cross-fade by default.

## Custom animation timing/easing

Override the default cross-fade via the
`::view-transition-old(name)` and `::view-transition-new(name)`
pseudo-elements:

```css
::view-transition-old(root),
::view-transition-new(root) {
    animation-duration: 0.4s;
    animation-timing-function: cubic-bezier(0.65, 0, 0.35, 1);
}
```

Per-element timing for shared transitions:

```css
::view-transition-old(my-named-element),
::view-transition-new(my-named-element) {
    animation-duration: 0.6s;
}
```

## `await window.djust.applyPatches(...)` for third-party JS

Since v0.8.5rc1 (PR-A), `applyPatches` returns `Promise<boolean>`.
Third-party JS that wants to coordinate with djust's render loop can
await the promise:

```javascript
// Custom JS in a hook or extension that needs to act AFTER a patch:
await window.djust.applyPatches(patches);
// Now the DOM reflects the patches; safe to measure or read state.
const newWidth = document.querySelector('.measured').offsetWidth;
```

Returns `true` on full success, `false` if any patch failed (caller may
trigger a full re-render fallback). Same return value the framework
already branches on at `02-response-handler.js:109`.

## Testing View Transitions in JSDOM

The View Transitions API is browser-only. JSDOM doesn't ship a
`document.startViewTransition` — vitest tests must stub it:

```javascript
const transitionStub = (callback) => {
    const transition = {
        // CRITICAL: yield to a microtask before invoking the callback.
        // Real browsers run the callback in a microtask after capturing
        // the pre-state frame. Sync invocation is the bug PR #1092 shipped.
        updateCallbackDone: (async () => {
            await Promise.resolve();
            callback();
        })(),
        skipTransition: () => {},
    };
    return transition;
};
document.startViewTransition = transitionStub;
```

The microtask yield is load-bearing — without it, the stub lies about
real-browser semantics and any test that relies on post-callback state
will pass under stub but fail in production.

## See also

- ADR-013 (`docs/adr/013-view-transitions-api-integration.md`) — design
  decision for the async signature; covers the alternatives that were
  considered.
- `applyPatches` reference: `python/djust/static/djust/src/12-vdom-patch.js`.
