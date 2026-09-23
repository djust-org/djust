# Streaming Initial Render

> **Phase 1 (v0.6.1)** — transport-layer chunked transfer, regex-split-after-render.
> **Phase 2 PR-A (v0.9.0)** — async render path (`aget()` + `ChunkEmitter`)
> on ASGI. The page is still fully rendered before the first chunk is sent;
> the TTFB win comes from lazy children (PR-B).
> **Phase 2 PR-B (v0.9.0)** — `{% live_render lazy=True %}` opt-in lazy children.
> **Phase 2 PR-C (v0.9.0)** — `asyncio.as_completed()` parallel render across
> lazy children.
> **v0.9.1 (#1145)** — `{% live_render lazy=True %}` is now supported on
> the Rust template engine path. Production users on `RustLiveView` who
> opted into the faster Rust-rendered template engine can now use lazy
> children without falling back to the Django template engine. Behaviour
> is byte-for-byte identical between the two paths — the Rust handler
> delegates to the same Python implementation.

## Honest Phase-1 vs Phase-2 caveat (closes retro #116)

The original v0.6.1 release notes called this feature "streaming initial
render" but the shipped path was actually a regex-split applied to the
**already-fully-rendered** HTML string. The chunks landed on the wire
*after* the entire view had completed `get_context_data()` and template
render. Time-to-first-byte was unchanged from `HttpResponse`.

That is still true of the parent view today. On ASGI, `async def aget()`
first runs the whole sync GET pipeline (`mount()`, `get_context_data()`
and the full template render) and only then splits the HTML and streams
it through `python/djust/http_streaming.py`'s `ChunkEmitter`. What does
overlap the flushed shell is `{% live_render lazy=True %}` children: they
render *after* the shell and main chunks are on the wire. Phase 1 is
retained as the WSGI-deployment fallback, where the user gets the
cosmetic 3-chunk split with no TTFB win.

djust can return a LiveView page as an **HTTP/1.1 chunked-transfer
response** instead of a single buffered response. The parent view is
fully rendered first; the response is then streamed in chunks, and on
ASGI any `{% live_render lazy=True %}` children render *after* the shell
and main chunks are flushed. Slow work in the parent's `mount()` /
`get_context_data()` still delays the first byte, so move it into a lazy
child to get a TTFB win. Intermediate proxies that honor chunked encoding
relay each chunk as it arrives.

## Deployment requirements

* **ASGI** (Daphne, Uvicorn, Hypercorn) — Phase-2 streaming. After the
  parent render, `arender_chunks()` emits the shell and main chunks, then
  renders lazy children while those chunks are already on the wire.
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

## Lazy children

`{% live_render "..." lazy=True %}` is opt-in. The tag emits a
`<dj-lazy-slot>` placeholder synchronously and registers a render thunk
for the emitter. After the parent's chunks flush, the emitter runs each
thunk and emits a
`<template id="djl-fill-X">` chunk + inline `<script>` that the browser
parses and the client uses to `replaceWith` the slot.

Several lazy children render in parallel (`asyncio.as_completed()`).

---

This is the djust analog of React's
[`renderToPipeableStream`](https://react.dev/reference/react-dom/server/renderToPipeableStream)
(what Next.js streaming uses): opting in flips the HTTP response type
from `HttpResponse` to `StreamingHttpResponse` with no other API
changes. Lazy children play the role of Suspense boundaries.

---

## Quick start

```python
from djust import LiveView

class DashboardView(LiveView):
    template_name = "dashboard.html"
    streaming_render = True   # ← opt in

    def mount(self, request, **kwargs):
        # The parent is fully rendered before the first byte is sent, so
        # slow work here still delays Chunk 1. Put slow sections in a
        # {% live_render "..." lazy=True %} child to stream them in later.
        self.rows = fetch_rows()
```

That's it. No JS changes, no new template tags, no new URL routing —
the existing `path("/dashboard/", DashboardView.as_view())` just works.

---

## How it works

When `streaming_render = True`, the rendered HTML is split into three
chunks at well-defined boundaries and sent in order (followed, on ASGI,
by any lazy-child fills):

| Chunk | Contents | Browser behavior |
| --- | --- | --- |
| **1. Shell-open** | Everything before `<div dj-root>` — `<!DOCTYPE html>`, `<head>`, `<link rel="stylesheet">`, `<body>` open, top chrome | Starts parsing `<head>`, fires CSS + JS downloads, paints page background |
| **2. Main content** | The `<div dj-root>...</div>` block — the entire LiveView body | Inserts the view's DOM; `djust` client script runs on `DOMContentLoaded` |
| **3. Shell-close** | `</body></html>` + trailing markup | Finishes document parse |

Browsers begin DOM construction the moment Chunk 1 arrives, so linked
stylesheets and `<script defer>` tags are already in flight while lazy
children are still rendering on the server.

The response omits the `Content-Length` header (HTTP chunked transfer
is implicit) and sets `X-Djust-Streaming: 1` as an observability marker
so you can verify the feature is active from your browser's Network
panel.

---

## When to use it

**Good fit:**

- Pages with slow sections (database aggregations, REST APIs, S3
  lookups, LLM calls) that you can move into `{% live_render lazy=True %}`
  children. Slow work left in the parent's `mount()` or
  `get_context_data()` still delays the first byte.
- Dashboards with large query fan-out, split into lazy children that
  render in parallel.
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
  `StreamingHttpResponse`, not per-chunk. **Lazy-child fills** also
  honor the nonce: when `request.csp_nonce` is set, both the
  `<template id="djl-fill-X">` element and the inline `<script>`
  activator emitted by `{% live_render lazy=True %}` carry a matching
  `nonce="..."` attribute, so strict-CSP deployments
  (`script-src 'nonce-...'`, no `'unsafe-inline'`) accept the
  activator. When `request.csp_nonce` is absent (no CSP middleware
  installed, or the request hasn't been processed by it), no `nonce`
  attribute is emitted — backward-compatible for non-CSP sites.
  See #1147.
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

## Startup checks for `{% live_render %}` misuse

djust's system-check framework includes a startup-time guard for
`{% live_render %}` tags. Run via `manage.py check` (or any Django
process startup):

* **`djust.A075` (Warning)** — fires when a template contains
  `{% live_render "..." sticky=True lazy=True %}`. The two kwargs
  are mutually exclusive: sticky preservation requires the slot
  to exist at mount-frame time so the WebSocket reattach can
  `replaceWith` the stashed subtree, while `lazy=True` defers slot
  rendering until after the parent shell flushes — the stash target
  doesn't exist when reattach runs. The runtime tag-eval path
  already raises `TemplateSyntaxError` on collision, but A075 surfaces
  the misuse before any request hits.
  - The check skips `{% verbatim %}...{% endverbatim %}` regions, so
    docs/marketing pages that show the anti-pattern as a literal
    example don't false-positive.
  - To silence (e.g. for documentation projects that aren't actually
    using djust):

    <!-- doc-snippet-check: skip -->
    ```python
    DJUST_CONFIG = {"suppress_checks": ["A075"]}
    ```

---

## Strict CSP support for `lazy=True` (#1147)

Sites that deploy strict Content-Security-Policy headers without
`'unsafe-inline'` (i.e. `script-src 'nonce-<nonce>'`) need every
inline `<script>` element to carry a matching `nonce` attribute.
djust's lazy-fill activator integrates with the standard Django
convention (`request.csp_nonce`, set by `django-csp` middleware or
any compatible CSP package):

* **When `request.csp_nonce` is set** — the framework emits
  `nonce="..."` on the lazy-fill `<template>` element AND on the
  inline `<script>` activator that calls
  `window.djust.lazyFill(...)`. The browser's CSP enforcer accepts
  the activator at parse time without any client-side intervention.
* **When `request.csp_nonce` is absent or empty** — no `nonce`
  attribute is emitted. Sites without CSP middleware see no change
  from previous behavior.

Implementation detail: the framework reads
`getattr(request, 'csp_nonce', None)` via the existing
`djust.utils.get_csp_nonce` helper. Any CSP middleware that follows
the same convention (the de-facto Django standard) is supported
out of the box — no additional configuration is required.

---

## Comparison

| Feature | `HttpResponse` (default) | `streaming_render = True` | React `renderToPipeableStream` |
| --- | --- | --- | --- |
| Response type | `HttpResponse` | `StreamingHttpResponse` | `ReadableStream` |
| Transfer encoding | `Content-Length: N` | `Transfer-Encoding: chunked` | `Transfer-Encoding: chunked` |
| Time-to-first-byte | After render complete | After the parent render (lazy children excluded) | After shell-open ready (~ms) |
| Chunks | 1 | 3 (shell / main / close) + one per lazy child | N (per Suspense boundary) |
| Out-of-order render | No | Yes, for `{% live_render lazy=True %}` children (ASGI) | Yes (React Suspense) |
| Opt-in per view | n/a | `streaming_render = True` | `<Suspense>` wrapping |
| Client-side code needed | None | None | React runtime |

Out-of-order rendering comes from lazy children: see
[Lazy children](#lazy-children) above.
