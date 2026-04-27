# Streaming Initial Render

> **Phase 1 (v0.6.1)** — transport-layer chunked transfer, regex-split-after-render.
> **Phase 2 PR-A (v0.9.0)** — async render path; the shell chunk now flushes
> to the wire BEFORE `get_context_data()` runs (real TTFB win on ASGI).
> **Phase 2 PR-B (v0.9.0)** — `{% live_render lazy=True %}` opt-in lazy children.
> **Phase 2 PR-C (v0.9.0)** — `asyncio.as_completed()` parallel render across
> lazy children.

## Honest Phase-1 vs Phase-2 caveat (closes retro #116)

The original v0.6.1 release notes called this feature "streaming initial
render" but the shipped path was actually a regex-split applied to the
**already-fully-rendered** HTML string. The chunks landed on the wire
*after* the entire view had completed `get_context_data()` and template
render. Time-to-first-byte was unchanged from `HttpResponse`. Phase 2 PR-A
(v0.9.0, ADR-015) delivers the actual shell-flush-before-render
semantic via `async def aget()` + `python/djust/http_streaming.py`'s
`ChunkEmitter`. Phase 1 is retained as the WSGI-deployment fallback —
WSGI cannot push real chunks before the response is assembled, so on
WSGI the user gets the cosmetic 3-chunk split with no TTFB win.

djust can return a LiveView page as an **HTTP/1.1 chunked-transfer
response** instead of a single buffered response. **On ASGI deployments
running v0.9.0 or later**, the browser receives the shell chunk
(`<!DOCTYPE>` + `<head>` + `<body>` open) as soon as the parent view's
template is *parsed*, not when it's fully rendered. Intermediate proxies
that honor chunked encoding relay each chunk as it arrives.

## Deployment requirements

* **ASGI** (Daphne, Uvicorn, Hypercorn) — full Phase-2 PR-A streaming.
  The shell chunk reaches the wire while body chunks are still being
  prepared by `arender_chunks()` on the same event loop.
* **WSGI** — falls back to Phase-1 cosmetic chunked response. The chunks
  are correct but TTFB is unchanged from non-streaming. `aget()` detects
  the missing event loop via `_is_asgi_context()` and routes to `get()`
  via `sync_to_async`.
* **Reverse proxies** — nginx default `proxy_buffering on` eats the
  TTFB win by buffering the entire response before relaying. To preserve
  Phase-2 streaming end-to-end:
  - nginx: `proxy_buffering off` in the location block, OR set
    `X-Accel-Buffering: no` on the response (djust does not set this
    by default — add it via middleware if needed).
  - Cloudflare: chunked transfer is supported; no extra config.
  - AWS ALB / GCP LB: chunked transfer supported.

## Foundation for lazy children (PR-B preview)

Phase 2 PR-B introduces `{% live_render "..." lazy=True %}` opt-in. The
tag emits a `<dj-lazy-slot>` placeholder synchronously and registers a
render thunk on `parent._chunk_emitter`. After the parent shell
flushes, the emitter runs each thunk and emits a
`<template id="djl-fill-X">` chunk + inline `<script>` that the browser
parses and the client uses to `replaceWith` the slot.

PR-A ships ONLY the foundation (`aget()`, `ChunkEmitter`,
`arender_chunks()`). PR-B ships the user-facing `lazy=True` API. PR-C
adds parallelization via `asyncio.as_completed()`. This split-foundation
shape follows retro #1122.

---

> **Original Phase-1 caveat (kept for archival reference):** Phase 1
> doesn't do true server-side overlap — rendering the main content
> *while* the browser is parsing the shell. Phase 2 PR-A introduces
> that capability.

This is the djust analog of Next.js
[`renderToPipeableStream`](https://nextjs.org/docs/app/building-your-application/routing/loading-ui-and-streaming):
opting in flips the HTTP response type from `HttpResponse` to
`StreamingHttpResponse` with no other API changes. The full Next.js
experience (shell-first paint during component render) arrives with
Phase 2.

---

## Quick start

```python
from djust import LiveView

class DashboardView(LiveView):
    template_name = "dashboard.html"
    streaming_render = True   # ← opt in

    def mount(self, request, **kwargs):
        # Slow work here delays Chunk 2, but Chunk 1 has already
        # arrived at the browser — CSS is loading, fonts are warming.
        self.rows = fetch_expensive_rows()
```

That's it. No JS changes, no new template tags, no new URL routing —
the existing `path("/dashboard/", DashboardView.as_view())` just works.

> **PR-A foundation status (v0.9.0, in flight):** the async-streaming
> render path (`aget()`, `ChunkEmitter`, `arender_chunks()`) lands as
> PR-A. **Dispatch wiring** that auto-routes GET → `aget()` when
> `streaming_render = True` lands together with PR-B
> (`{% live_render lazy=True %}`), because the user-visible TTFB win
> arrives at the same time as the user-facing API. Until PR-B merges,
> setting `streaming_render = True` continues to take the Phase-1
> regex-split-after-render path documented at the top — the ASGI shell-
> flush behavior described below activates with PR-B.

---

## How it works

When `streaming_render = True`, `LiveView.get()` splits the rendered
HTML into three chunks at well-defined boundaries and yields each chunk
to the wire as soon as it's ready:

| Chunk | Contents | Browser behavior |
| --- | --- | --- |
| **1. Shell-open** | Everything before `<div dj-root>` — `<!DOCTYPE html>`, `<head>`, `<link rel="stylesheet">`, `<body>` open, top chrome | Starts parsing `<head>`, fires CSS + JS downloads, paints page background |
| **2. Main content** | The `<div dj-root>...</div>` block — the entire LiveView body | Inserts the view's DOM; `djust` client script runs on `DOMContentLoaded` |
| **3. Shell-close** | `</body></html>` + trailing markup | Finishes document parse |

Browsers begin DOM construction the moment Chunk 1 arrives, so linked
stylesheets and `<script defer>` tags are already in-flight while your
Python code is still computing the view state.

The response omits the `Content-Length` header (HTTP chunked transfer
is implicit) and sets `X-Djust-Streaming: 1` as an observability marker
so you can verify the feature is active from your browser's Network
panel.

---

## When to use it

**Good fit:**

- Pages where `mount()` or `get_context_data()` make slow external
  calls (database aggregations, REST APIs, S3 lookups, LLM calls).
- Dashboards with large query fan-out — each row-count query adds to
  time-to-first-byte under the non-streaming path.
- Public landing pages where `<link rel="stylesheet">` in `<head>`
  determines Largest Contentful Paint — flushing the head early is a
  measurable LCP win.

**Not worth it:**

- Small, fast pages where the server renders in < 50 ms. The fixed
  overhead of chunked transfer (extra bytes per chunk, proxy buffering
  risk) can exceed the benefit on sub-frame renders.
- Pages served behind a reverse proxy that **buffers** responses by
  default (see caveats).

---

## Caveats

- **No `Content-Length`.** Some reverse proxies (notably default
  nginx + `proxy_buffering on`) buffer chunked responses into a single
  write, defeating the streaming benefit. Set
  `proxy_buffering off;` on the nginx location block, or switch the
  proxy to HTTP/2 (which handles streaming natively).
- **Middleware that inspects the response body must be
  streaming-aware.** Middleware reading `response.content` on a
  `StreamingHttpResponse` raises `AttributeError: ... content`. If you
  have custom middleware, guard body reads with
  `isinstance(response, StreamingHttpResponse)`. All of djust's
  built-in middleware is streaming-safe as of v0.6.1.
- **CSP nonces** generated by `django-csp` work fine — nonces are
  produced during template render (before any chunk is sent) and the
  `Content-Security-Policy` response header is set once on the
  `StreamingHttpResponse`, not per-chunk.
- **HTML without a `<div dj-root>`** (edge case — raw body fragments)
  falls back to a single-chunk response equivalent to
  `HttpResponse(html)`. Streaming is a no-op in that case.
- **Literal `</body>` tokens inside `<style>` blocks or HTML comments.**
  The chunk-splitter masks `<script>...</script>` content so a literal
  `</body>` inside a JavaScript string does not create a false split
  boundary, but it does **not** currently mask `<style>` or `<!-- ... -->`
  blocks. If your template inlines `</body>` as literal string content
  inside `<style>` or an HTML comment, the split may fire at the wrong
  position. In practice this is extremely rare — for almost all apps it
  is not a concern. If your template does this legitimately, verify the
  streamed chunks via your browser's Network panel.

---

## Comparison

| Feature | `HttpResponse` (default) | `streaming_render = True` | Next.js `renderToPipeableStream` |
| --- | --- | --- | --- |
| Response type | `HttpResponse` | `StreamingHttpResponse` | `ReadableStream` |
| Transfer encoding | `Content-Length: N` | `Transfer-Encoding: chunked` | `Transfer-Encoding: chunked` |
| Time-to-first-byte | After render complete | After shell-open ready (~ms) | After shell-open ready (~ms) |
| Chunks | 1 | 3 (shell / main / close) | N (per Suspense boundary) |
| Out-of-order render | No | No (Phase 1) | Yes (React Suspense) |
| Opt-in per view | n/a | `streaming_render = True` | `<Suspense>` wrapping |
| Client-side code needed | None | None | React runtime |

Phase 1 matches the **first-paint** win of `renderToPipeableStream`
without the Suspense machinery. Phase 2 (planned for v0.6.2) adds
out-of-order rendering via lazy-child placeholders that stream in after
the main chunk.

---

## Future work — Phase 2 (v0.6.2)

**Lazy-child streaming** extends this to `{% live_render %}` children
marked `lazy=True`:

1. Parent yields a `<div dj-view dj-lazy>` placeholder inside Chunk 2.
2. After Chunk 3, the parent continues streaming each lazy child as a
   `<template data-target="dj-lazy-N">...</template>` + inline
   `<script>djust.streamFill('dj-lazy-N')</script>` sequence.
3. A new client module adopts the template content into the target
   container — equivalent to React Suspense's streaming resolution.

This lets you ship an instant shell with placeholder UI and stream in
heavy children (charts, tables, LLM output) as they become ready —
closer parity with `renderToPipeableStream` and React Server
Components. Tracked on the ROADMAP as v0.6.2 scope.
