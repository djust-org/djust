# Client bundle: core and on-demand chunks

djust injects its client runtime into every LiveView page; you never add a
`<script>` tag. Since #4007 the runtime is split in two:

- **Core** (`djust/client-core.min.js`, or the readable `client-core.js` under
  `DEBUG`): events, patching, transport, navigation, hooks and the rest of the
  `dj-*` attributes. Every LiveView page loads it.
- **Chunks** (`djust/chunks/<name>.min.js`): optional modules the core loads
  only when the page uses them.

| Chunk | Loaded when the page has |
|---|---|
| `uploads` | `dj-upload` or `dj-upload-drop`, or the view declares uploads (`allow_upload`) |
| `virtual-list` | `dj-virtual` |
| `cursor-overlay` | `dj-hook="CursorOverlay"` |
| `tutorial` | the `{% tutorial_bubble %}` element (`#dj-tutorial-bubble`) |
| `service-worker` | a service worker controls the page, or your code calls `djust.registerServiceWorker()` |
| `component-behaviors` | a `djust.components` widget that needs client behaviour: `.rich-select`, `.combobox`, `.dj-theme-toggle`, `.popover-wrapper`, `.dj-popconfirm-wrapper`, `.collapsible`, `.ctx-wrapper`, `.code-block-copy`, `.dropzone`, `.kanban`, `.rte`, `.multi-select`, `.split-pane` |
| `keyboard-nav` | `role="dialog"`, `role="tablist"`, `.dj-modal`, `.dj-accordion`, `.dj-dropdown` or `.dj-dropdown-menu` |
| `error-overlay` | Django `DEBUG` (`window.DEBUG_MODE`); never loaded in production |
| `dj-style` | strict CSP styles (`csp_strict_styles`: the `<meta name="djust-styles" content="attrs">` in the head) |
| `flip` | `dj-flip` |
| `transitions` | `dj-transition`, `dj-remove` or `dj-transition-group` |
| `infinite-scroll` | `dj-viewport-top` or `dj-viewport-bottom` |
| `prefetch` | `<a dj-prefetch>`, or a service worker controls the page |
| `track-static` | `dj-track-static` (usually on a `<script>` or `<link>` in the head) |
| `dialog` | `<dialog dj-dialog>` |

`client-sizes.json` (next to the bundles) lists the current gzipped size of the
core, the full bundle and every chunk; `make sizes` prints them.

## How loading works

When the page loads, the core scans the document for the attributes above. If
it finds none, it starts at once, exactly as the single bundle did. If it finds
some, it loads those chunks first and only then binds events, mounts hooks and
connects, so a page that uses a chunk behaves as before. The chunks a page
needs are requested together, in parallel.

The server knows what the first render contains, so it puts a preload hint
for each chunk that render uses in the page's `<head>`:

```html
<link rel="preload" as="script" href="/static/djust/chunks/dialog.min.js?v=…" nonce="…">
```

The browser fetches the chunk while it parses the page, alongside the core,
and the core's loader then reuses that response; a page that uses a chunk
costs no extra round trip before it starts. The hint carries the request's CSP
nonce and the same URL the loader requests. It covers the element markers in
the table, a view that declares uploads, `DEBUG` (the error overlay) and strict
CSP styles; a service worker's control of the page is known only in the
browser, so `service-worker` (and the worker half of `prefetch`) is never
preloaded. A page without a `<head>` gets no hints and loads its chunks as
described above.

There is no combined "several chunks in one file" request: chunks are plain
static files (hashed by `ManifestStaticFilesStorage`, servable from a CDN),
and a combination per set of features would need a server endpoint or a
build output per combination. The preload hints, which start every chunk the
first render needs at once, remove the round trip a combined file would save.

A TurboNav page swap does the same scan for the new page.

A feature that first appears **after** the page has loaded (a patch, a tab
switch, an `{% if %}` that turns true, or a `live_redirect` that renders the
first `dj-virtual` list, `dj-hook="CursorOverlay"` or tutorial bubble) loads its
chunk then: the scan runs over every patched subtree, and once the chunk has
loaded the client sets the new element up (`dj-virtual` is virtualized, the
hook is mounted, a `dj-dialog="open"` dialog opens, a `dj-viewport-*` list is
observed, a `dj-transition` runs). Until then the element shows as the server
rendered it. A
view that declares uploads loads the uploads chunk when it mounts, and an
upload push event (`djust:upload_progress`) that arrives while that chunk is
loading is held and delivered once it has run.

A chunk request that fails, or gets no answer within 10 seconds, is reported
once in the console; the page then starts without that feature, and later
patches do not request it again.

`djust.uploads` and `djust.registerServiceWorker()` exist from the start. Until
their chunk has loaded they are stubs: `djust.uploads.uploadFile()`,
`uploadPresigned()`, `cancelUpload()` and `djust.registerServiceWorker()`
load the chunk and then forward the call, returning a promise as before. The
synchronous helpers of `djust.uploads` (`activeUploads`, `fileHintKey`, the
resumable-upload store) appear once the chunk has loaded; to use them on a page
without upload inputs, wait for it first:

```js
await djust.loadClientChunk('uploads');
djust.uploads.activeUploads.size;
```

`djust.resetViewport()` and `djust.teardownInfiniteScroll()` exist from the
start too; before the `infinite-scroll` chunk has loaded no container is
tracked, so they do nothing. `djust.initInfiniteScroll` appears with the
chunk. The objects the other chunked modules expose for tests and devtools
(`djust.keyboardNav`, `djust.flip`, `djust.djDialog`, `djust.djTransition`,
`djust.maybeDeferRemoval`, `djust.djTrackStatic`, `djust.djStyle`,
`window.djustErrorOverlay`, ...) exist once their chunk has run. The
deferred-save status (the "Saving your change…" notice of a transient
`state_error`) is core and shows in every mode; only the DEBUG overlay is a
chunk.

A feature that first appears in a patch loads its chunk then, so it is set up
a moment later than with the full bundle: a `dj-remove` element that a patch
inserts and the very next patch removes, before the chunk has arrived, is
removed without its exit transition, and the first modal a patch renders on a
page that had none gets its focus trap once `keyboard-nav` has run.

`djust.loadClientChunk(name)` loads any chunk by name and resolves once its
code has run (at once if it is already loaded, or under the full bundle).

## What stays in core

The SSE transport (`03b-sse.js`, ~3 KB gzipped) is not a chunk, on purpose:

- Its trigger is transport configuration (`use_websocket: False`) or a
  WebSocket that fails, not anything on the page, and the transport is chosen
  before any page scan. A chunk there would put a network fetch on the path
  that runs exactly when the network is failing, and delay the first
  connection of every SSE page by a round trip the page scan cannot predict.
- It reads about 30 core bindings (frame dispatch, view slots, request
  correlation, form recovery, reconnect state): chunking it means either
  publishing most of the transport internals on the private chunk bridge or
  cutting a new module boundary through the transport, for a saving of ~3 KB
  on WebSocket pages.

The modules every page uses (events, patching, the WebSocket transport,
navigation, hooks, loading states, forms) are core too.

## CSP

Each chunk is loaded with a `<script>` element that carries the same nonce as
the client's own script tag, so a strict `script-src 'nonce-…'` policy needs
nothing more. The chunk URLs come from `static()`, passed to the client in the
tag's `data-djust-chunks` attribute, so `ManifestStaticFilesStorage` /
WhiteNoise hashed names and a CDN `STATIC_URL` are used as for any other
static file. A policy that lists script hosts must allow the host that serves
your static files, as it already does for the client.

## Service worker precache

`python manage.py generate_sw` precaches the bundle the LiveView actually
injects (`client-core.min.js`, or `client.min.js` with `client_bundle:
"full"`; the readable files under `DEBUG`) and every chunk, at the exact URLs
the page requests (the `static()` name plus the `?v=` version query), so an
offline page finds the core and any chunk it needs in the cache. The DEBUG-only
`error-overlay` chunk is left out unless `DEBUG` is on. The command resolves
all of this (`DEBUG`, `DJUST_CLIENT_JS_MINIFIED`, `client_bundle` and the
`ManifestStaticFilesStorage` hashed names) from the settings it runs with, so
run it with your production settings, after `collectstatic`.

## The full bundle

The single bundle contains every module and loads nothing on demand. Use it
when you want one file and no on-demand loading at all, for example with a
hand-written service worker that precaches only the client:

```python
LIVEVIEW_CONFIG = {
    "client_bundle": "full",  # inject client.min.js (client.js under DEBUG)
}
```

A template that includes the client itself,
`<script src="{% static 'djust/client.js' %}">`, gets the full bundle as
before. If both a full and a core bundle end up on one page, the first one to
run is used and the other does nothing.
