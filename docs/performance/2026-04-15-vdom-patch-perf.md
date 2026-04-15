# VDOM Patch Performance Investigation (2026-04-15)

## Problem

On the djust.org `/examples/` page (974-line template, 17 demo sections, 304KB rendered HTML), clicking the "Increment" button on the counter demo took **~250ms** end-to-end. The counter also failed on the first click, falling back to full HTML recovery.

Two issues were identified:

1. **VDOM patch failure**: The Rust VDOM parser drops regular HTML comments (`<!-- Hero Section -->`) from the tree, but the JS patcher was counting them during path traversal, causing index misalignment. Every patch failed and triggered full HTML recovery.

2. **Slow post-patch DOM scanning**: After every patch, `bindLiveViewEvents()` scanned the entire DOM with `querySelectorAll('*')` to find and bind `dj-*` interactive elements — O(all_elements) per update on a page with 5000+ nodes.

## Investigation

### Profiling methodology

Added `Instant::now()` timing instrumentation to `render_with_diff()` in `crates/djust_live/src/lib.rs`, measuring four phases:
- Template render (`render_with_loader`)
- HTML parse (html5ever `parse_html_continue`)
- VDOM diff + `sync_ids`
- HTML serialization (`to_html`)

Timings are exposed to Python via `get_render_timing()` and propagated to the WebSocket response `performance` metadata, visible in the browser console.

Client-side timing measured via WebSocket `send`/`onmessage` instrumentation in the browser.

### Server-side breakdown (Rust)

| Phase | Time | % of Rust total |
|-------|------|-----------------|
| Template render | 1.0ms | 5% |
| **html5ever parse** | **16.0ms** | **73%** |
| VDOM diff | 0.4ms | 2% |
| HTML serialize | 4.0ms | 18% |
| **Rust total** | **22ms** | |

Key insight: The VDOM diff itself is fast (0.4ms). The bottleneck is html5ever parsing 304KB of HTML on every event, even when only one text node changed. Template rendering and diffing are negligible.

### Client-side breakdown (before optimization)

| Phase | Time |
|-------|------|
| `bindLiveViewEvents()` DOM scan | ~25ms |
| `updateHooks()` + `bindModelElements()` | ~15ms |
| Patch application | ~5ms |
| Focus save/restore | ~5ms |
| Other (loading manager, scroll) | ~6ms |
| **Client total** | **~56ms** |

## Fixes

### PR #728: VDOM comment node path fix

**Root cause**: `getNodeByPath()` in `12-vdom-patch.js` included ALL comment nodes when filtering children for path traversal (line 160: `if (child.nodeType === Node.COMMENT_NODE) return true`). The Rust VDOM parser only preserves `<!--dj-if-->` placeholder comments — all other HTML comments are dropped (`parser.rs:381`).

**Fix**: Changed the comment filter to only count `dj-if` placeholders:
```js
if (child.nodeType === Node.COMMENT_NODE) {
    return child.textContent.trim() === 'dj-if';
}
```

**Impact**: Patches work correctly on any page with HTML comments. Previously, every page with comments in `dj-root` had broken VDOM patching and fell back to full HTML recovery.

### PR #731: Event delegation + instrumentation

Three changes:

#### 1. Rust timing instrumentation

Added per-phase `Instant::now()` measurements to `render_with_diff()` and `render_binary_diff()` in `crates/djust_live/src/lib.rs`. Exposed via `get_render_timing()` Python method and WebSocket `performance.timing` metadata.

#### 2. Event delegation

Replaced per-element event binding with event delegation. Instead of scanning the DOM after every VDOM patch to find and bind `dj-*` elements, install ONE listener per event type on the `dj-root` element at startup.

**Delegated events** (via `e.target.closest()`):
- click (`dj-click`, `dj-copy`)
- submit (`dj-submit`)
- change (`dj-change`)
- input (`dj-input` — per-element rate limiting via WeakMap)
- keydown/keyup (`dj-keydown`, `dj-keyup`)
- paste (`dj-paste`)
- focusin/focusout (`dj-focus`, `dj-blur`)

**Still per-element** (non-delegable):
- `dj-poll` (custom interval timer)
- `dj-mounted` (fire-once lifecycle)
- `dj-window-*`/`dj-document-*`, `dj-click-away`, `dj-shortcut` (already delegated)

**Teardown**: Uses `AbortController` to cleanly remove old listeners before reinstalling after TurboNav navigation.

**Body guard**: Delegation only installs on `[dj-view]`/`[dj-root]` elements, never on `document.body` fallback. This prevents double events when navigating from a non-LiveView page back to a LiveView page via TurboNav (body persists across page swaps).

#### 3. Targeted CSS selectors (intermediate step, superseded by delegation)

Replaced `querySelectorAll('*')` with targeted attribute selectors for the remaining per-element scanning (scoped listeners, poll, mounted). This only affects the non-delegated events.

## Results

### End-to-end performance (djust.org /examples/ counter, localhost, 20 samples)

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| **Total E2E (avg)** | **250ms** | **71ms** | **3.5x** |
| Total E2E (p50) | — | 76ms | — |
| Total E2E (p95) | — | 82ms | — |
| E2E range | 45–250ms | 70–84ms | **17x tighter** |
| Server total | 177ms* | 34ms | 5.2x |
| Client handling | 56ms | 30ms | 1.9x |

*The 177ms "before" measurement included cold-start overhead. Stable server time was ~50ms before optimization, reduced to 34ms after.

### Server-side breakdown (after)

| Phase | Time |
|-------|------|
| Handler (`self.count += 1`) | 0.5ms |
| Python context prep | 0.5ms |
| Rust render+parse+diff+serialize | 22ms |
| Python overhead (serialization, sync_to_async) | ~12ms |
| **Server total** | **34ms** |

### Client-side breakdown (after)

| Phase | Time |
|-------|------|
| Patch application | ~5ms |
| Post-patch hooks/model/loading scans | ~15ms |
| Focus save/restore | ~5ms |
| Other | ~5ms |
| **Client total** | **~30ms** |

The `bindLiveViewEvents()` DOM scan cost dropped from ~25ms to ~0ms (delegation = no scanning).

## Remaining optimization targets

For further improvement beyond 71ms:

| Target | Current cost | Potential savings | Complexity |
|--------|-------------|-------------------|------------|
| html5ever parse (skip `dj-update="ignore"` sections) | 16ms | ~8ms | Medium |
| HTML serialize (skip unchanged subtrees) | 4ms | ~2ms | Medium |
| Python context serialization | 12ms | ~5ms | Low |
| Post-patch hook/model scanning | 15ms | ~10ms | Medium |
| **Total potential** | | **~25ms** | |

Target achievable E2E: **~45ms** (from current 71ms).

## Files modified

### PR #728
- `python/djust/static/djust/src/12-vdom-patch.js` — Comment node filter fix
- `python/djust/static/djust/client.js` — Rebuilt
- `tests/js/vdom_patch_errors.test.js` — 2 new regression tests

### PR #731
- `crates/djust_live/src/lib.rs` — Rust timing instrumentation
- `python/djust/mixins/template.py` — Capture `_rust_render_timing`
- `python/djust/websocket.py` — Propagate `rust_timing` to response metadata
- `python/djust/static/djust/src/09-event-binding.js` — Event delegation refactor
- `python/djust/static/djust/client.js` — Rebuilt
- `tests/js/double_bind.test.js` — Updated for delegation model
- `tests/js/event-listener-dedup.test.js` — Updated for delegation model
- `tests/js/dj-copy.test.js` — Updated for delegation model

## Issues

- #729 — VDOM patch fails on pages with HTML comments in templates
- #730 — VDOM render+diff takes 176ms for single-variable change on large templates
