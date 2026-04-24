---
title: "Intent-Based Prefetch (`dj-prefetch`)"
slug: prefetch
section: guides
order: 6.9
level: intermediate
description: "Hover- and touch-driven link prefetch for fast in-app navigation"
---

# Intent-Based Prefetch (`dj-prefetch`)

**New in v0.7.0.** Mark a link with `dj-prefetch` and djust will prefetch
its destination the moment the user signals intent — hovering for ~65 ms
or tapping on mobile. By the time they actually click, the browser has
already warmed its HTTP cache, and the navigation feels instant.

This layers on top of the existing service-worker-mediated prefetch
shipped with the PWA/service-worker module. The SW path (also in
`static/djust/src/22-prefetch.js`) fires `PREFETCH` messages for *every*
same-origin link the pointer enters and caches via the service worker.
The new intent path is smaller, SW-independent, and opt-in per link —
use it on the handful of links that dominate your in-app navigation.

---

## Quick start

```html
<!-- Link warms the browser cache on hover / touchstart. -->
<a dj-prefetch href="/dashboard/">Dashboard</a>

<!-- Explicit opt-out (useful to disable on a specific <a> inside a
     section that blanket-enables prefetch via a JS hook). -->
<a dj-prefetch="false" href="/logout/">Log out</a>
```

No Python change. No handler. No WebSocket frame. The browser does the
work.

---

## How it works

1. **`mouseenter` with 65 ms debounce.** The user moves the pointer
   into the `<a>`. A `setTimeout(65)` is armed. If the pointer leaves
   (`mouseleave`) before 65 ms elapses, the timer is cancelled — we
   never fire a request.
2. **`touchstart` (mobile) — no debounce.** Mobile users commit to a
   tap within a few frames; debouncing would just eat those frames.
   The prefetch fires immediately on `touchstart`.
3. **`<link rel="prefetch" as="document">` injection.** djust
   `createElement('link')`s a prefetch hint and appends it to
   `document.head`. The browser handles cache lifecycle, priority, and
   eviction — we never hold a `Response` object ourselves.
4. **Fallback to low-priority `fetch`.** If `link.relList.supports(
   'prefetch')` is false (some older mobile browsers), djust falls
   back to `fetch(href, {credentials: 'same-origin', priority: 'low'})`
   and attaches an `AbortController` so `mouseleave` can cancel the
   in-flight request.
5. **Per-URL dedupe.** A `Set` stores every URL we've already
   prefetched; subsequent hovers on the same link are no-ops.
   `window.djust._prefetch.clear()` wipes the set on `live_redirect`
   SPA transitions so the new page's links are eligible again.
6. **Same-origin guard.** `new URL(href, location.origin)` is compared
   to `location.origin`. Cross-origin hrefs are silently rejected.
7. **`navigator.connection.saveData` honored.** If the user has Data
   Saver turned on, every prefetch path short-circuits to no-op.

---

## When to use it

**Good fit:**

- **Dashboard nav menus** — the top 3–5 routes every authenticated
  user hits. A 65 ms head start on the next page's HTML is the
  difference between "feels instant" and "feels like SPA loading
  spinner."
- **Article / search-result lists** — the next row the user is
  hovering is almost certainly the one they'll click.
- **Wizards / stepped flows** — the "next step" link when the current
  step is near-complete.

**Not worth it:**

- **Tiny marketing sites** where every page is already pre-rendered
  into the HTML at page load via a top-level
  `<link rel="prefetch" href="/about/">` in `<head>`. The `dj-prefetch`
  attribute does the *same thing lazily*; if you're fine doing it
  eagerly, you don't need this module.
- **Single-page apps with no cross-page navigation.** If every route
  is a `live_patch`, prefetching the full HTML shell is wasted
  bandwidth.
- **Links that trigger state-changing GETs** — logout, "mark as read,"
  etc. Prefetch ≈ a speculative GET; if your GET mutates, the user's
  state drifts every time they hover. Don't opt those in.

---

## Caveats

- **Same-origin only.** Cross-origin prefetch is blocked by the
  same-origin guard above, regardless of what the browser would
  actually allow. If you need cross-origin prefetch, use a plain
  `<link rel="prefetch">` in `<head>`.
- **`javascript:` / `data:` URLs are blocked.** The URL-parsing
  `try/catch` rejects non-HTTP schemes, and the same-origin check
  rejects anything that manages to parse but isn't from
  `location.origin`.
- **Prefetch is the HTML shell only.** The browser may *additionally*
  preload subresources it discovers via the prefetched document
  (Speculation Rules / Link headers), but djust doesn't call handlers
  or warm any server-side data. A LiveView's `mount()` still runs on
  actual navigation — the win is purely HTTP-level.
- **Prefetch hints are advisory.** Browsers may ignore the hint under
  memory pressure, on cellular networks, or when Data Saver is on
  (we already short-circuit the last case, but the browser may add
  more).
- **Don't put it on state-changing links.** `dj-prefetch` is for
  author-controlled navigation links only. See the module header in
  `python/djust/static/djust/src/22-prefetch.js` for the full safety
  contract.

---

## Comparison with service-worker prefetch

Both prefetch modes live in the same JS module but serve different
shapes of app:

| Feature             | SW hover prefetch (pre-v0.7.0)       | `dj-prefetch` (v0.7.0)                        |
| ------------------- | ------------------------------------ | --------------------------------------------- |
| Trigger             | `pointerenter` on **any** `<a>`      | `mouseenter` (debounced) / `touchstart` on opt-in `<a>` |
| Opt-in surface      | All same-origin links                | Links with `dj-prefetch` attribute             |
| Debounce            | None                                 | 65 ms on hover, 0 ms on touch                  |
| Transport           | SW `postMessage({type: 'PREFETCH'})` | `<link rel="prefetch">` injection              |
| Requires SW         | Yes — no SW → no-op                  | No — works standalone                          |
| Cache lifecycle     | SW-owned (Cache API)                 | Browser HTTP cache                             |
| Cancellable         | No (SW fires-and-forgets)            | Yes (`AbortController` on the `fetch` fallback path) |
| Data-saver respected | Yes                                  | Yes                                            |

Most apps will want both: the SW path as a blanket win for users on
the PWA, and `dj-prefetch` on the 3–5 links that dominate navigation
— the belt-and-braces approach.

---

## Testing & diagnostics

`window.djust._intentPrefetch` exposes the internal state for tests:

```js
djust._intentPrefetch._prefetched           // Set of prefetched URLs
djust._intentPrefetch.HOVER_DEBOUNCE_MS    // 65
djust._intentPrefetch.clear()               // Reset dedupe set
```

Set `globalThis.djustDebug = true` in the console to see
`[djust] Intent prefetch: <url>` log lines fire as links are
hover-committed. Matching debug output exists in the SW-hover module.
