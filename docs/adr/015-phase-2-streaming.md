# ADR-015: Phase 2 Streaming — Async Render Path + Lazy Children

**Status**: Accepted
**Date**: 2026-04-26
**Target version**: v0.9.0 P2 (shape C)
**Related**: #1043, retro #1122 split-foundation rule, retro #116 doc-claim
verification rule, ADR-014 (slot-replace pattern), `AsyncWorkMixin.assign_async`

---

## Pre-flight finding

Reading `python/djust/mixins/template.py:_split_for_streaming` and
`python/djust/mixins/request.py:_make_streaming_response` reveals that
Phase 1 (v0.6.1) is a regex-split-after-render. By the time the first byte
hits the wire, `mount() → get_context_data() → render_full_template()`
has already finished. **TTFB is unchanged from non-streaming.** Retro #116
already documented this as "Phase 1 streaming guide overclaimed".

So Phase 2 is not "completing the arc" — it is **introducing actual
streaming for the first time**, with the Phase 1 split as a vestigial
cosmetic that we keep for backwards compat.

## Recommended 3-PR split (per retro #1122)

### PR-A — Foundation: async render path + chunk emitter (~600 LoC, 1.5 days)

Rebuild the GET path so that rendering produces an async iterator of HTML
chunks. `streaming_render = True` actually delivers shell-then-body chunks
before the children render. This PR alone ships a real TTFB win for any
view with a slow `get_context_data()`, even before lazy-children land.

Files:
- `python/djust/http_streaming.py` (new) — `ChunkEmitter` class. ~250 LoC.
- `python/djust/mixins/request.py` — `async def aget()` parallel to `get()`. ~150 LoC.
- `python/djust/mixins/template.py` — `arender_chunks()` async generator. ~150 LoC.
- `python/djust/middleware.py` — buffering middleware compat docs. ~30 LoC.
- `tests/unit/test_async_render_path.py` — chunk semantics, sync/async fallback, cancellation. ~250 LoC.
- `docs/website/guides/streaming-render.md` rewrite — close retro #116 doc-claim debt.

### PR-B — Capability 1: lazy-child render (~500 LoC, 2 days)

`{% live_render "X" lazy=True %}` opt-in. Tag emits `<dj-lazy-slot>`
placeholder + registers thunk on `parent._chunk_emitter`. Chunk emitter
runs thunk after parent shell flushes; emits `<template id="djl-fill-X">`
+ inline `<script>` that calls `window.djust.lazyFill('X')`.

Resolution: HTTP GET only. WS path keeps `assign_async` for the same UX.

`lazy="visible"` opts into IntersectionObserver-triggered fill (Option C).
Default `lazy=True` is server-flushed (Option A).

Sticky + lazy: forbidden (`TemplateSyntaxError` + system check A075).

Files:
- `python/djust/templatetags/live_tags.py` — `lazy=` kwarg branch. ~120 LoC.
- `python/djust/static/djust/src/16-lazy-fill.js` — slot-fill reconciliation. ~150 LoC.
- `python/djust/checks.py` — A075 system check. ~40 LoC.
- `tests/unit/test_live_render_lazy.py` — tag emit, thunk register, error envelope. ~250 LoC.
- `tests/integration/test_lazy_streaming_flow.py` — full Django ASGI client. ~200 LoC.
- `tests/js/lazy_fill.test.js` — slot replacement, idempotent double-fire. ~100 LoC.

### PR-C — Capability 2: true server overlap (~300 LoC, 0.5 days)

Replace sequential `await` over thunks with `asyncio.as_completed()`.
Per-task timeout. Sentinel-based cancellation propagates via
`request_token` from the emitter on ASGI scope `disconnected`.

Files:
- `python/djust/mixins/template.py` — `as_completed()` refactor in
  `arender_chunks` Phase 5. ~80 LoC. (Originally planned for
  `http_streaming.py` but the loop lives where it consumes thunks.)
- `tests/integration/test_chunks_overlap.py` — slow vs fast child
  timing assertions + mid-stream cancellation propagation
  (T-PRC-4). ~200 LoC.

## Total estimate

4 days end-to-end with the split. ~1400 LoC core + ~1150 LoC tests + ADR + demo + docs.

Matches ROADMAP shape C estimate (3-5 days for #1043).

## Why split

- High blast-radius foundation (`aget()` refactor) shipping with new user API in one diff = the PR #1092 sync-callback failure mode (retro #1122).
- Old-client + new-server compat unverifiable in single CI run if API + transport ship together.
- PR-A alone is releasable as v0.9.0rc1 and gives users a TTFB win.

## Locked decisions

- Lazy trigger: `lazy=True` (parent-flush) default, `lazy="visible"` IntersectionObserver opt-in.
- Cancellation: emitter-level `request_token`, `task.cancel()` on disconnect, `start_async` chains continue (their lifecycle unchanged).
- Errors: `<template data-status="error">` envelope, default fallback rendered, response stays open. `lazy={"on_error": "close"}` opts into stream-close.
- Sticky + lazy = `TemplateSyntaxError` (incompatible).
- Backpressure: bounded queue N=8 default, `DJUST_LAZY_CHUNK_QUEUE_MAX` setting.
- Scope: HTTP GET only (WS uses `assign_async`).
- Wire format: `<dj-lazy-slot>` placeholder; `<template id="djl-fill-X">` + inline `<script>` for fill.

## Top risks (full register in agent output)

- Cancellation leak via `sync_to_async` shield boundary.
- Partial-render error pages (status code locks at first chunk).
- Backpressure with slow client.
- WSGI deployment falls back to Phase-1 cosmetic chunks (no TTFB win); startup check warns.
- `Django.test.Client` collapses streaming responses; tests need ASGI client.
- Old client viewing `<dj-lazy-slot>` renders empty without bootstrap script.
- Reverse-proxy buffering eats TTFB win (nginx default `proxy_buffering on`).

## Out of scope

- WS lazy children (use `assign_async`).
- POST handler lazy responses (JSON, not HTML).
- Per-chunk gzip framing.
- Cross-tab `dj-lazy-slot` deduplication.
- LiveComponent-level lazy.

## Deferred from PR-B (tracked for follow-up)

- **System check A075 (sticky+lazy template-scan)**. The tag itself
  raises `TemplateSyntaxError` at eval, which is the load-bearing
  enforcement. A075 was originally planned as defense-in-depth so the
  collision is caught at `manage.py check` time, before first render.
  Defer to a follow-up — the runtime check covers the user-visible
  failure mode.
- **CSP-nonce-aware activator script**. The wire format uses an inline
  `<script>window.djust.lazyFill('X')</script>` activator. Strict CSP
  without `unsafe-inline` blocks this; the auto-scan on
  `DOMContentLoaded` (in `50-lazy-fill.js`) is the documented
  fallback. Future work: propagate the request's CSP nonce into the
  activator's `nonce=...` attribute. Tracked separately.
