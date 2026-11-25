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

`client-sizes.json` (next to the bundles) lists the current gzipped size of the
core, the full bundle and every chunk; `make sizes` prints them.

## How loading works

When the page loads, the core scans the document for the attributes above. If
it finds none, it starts at once, exactly as the single bundle did. If it finds
some, it loads those chunks first and only then binds events, mounts hooks and
connects, so a page that uses a chunk behaves as before; it costs one extra
request for each chunk the page uses.

A TurboNav page swap does the same scan for the new page.

A feature that first appears **after** the page has loaded (a patch, a tab
switch, an `{% if %}` that turns true, or a `live_redirect` that renders the
first `dj-virtual` list, `dj-hook="CursorOverlay"` or tutorial bubble) loads its
chunk then: the scan runs over every patched subtree, and once the chunk has
loaded the client sets the new element up (`dj-virtual` is virtualized, the
hook is mounted). Until then the element shows as the server rendered it. A
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

`djust.loadClientChunk(name)` loads any chunk by name and resolves once its
code has run (at once if it is already loaded, or under the full bundle).

## CSP

Each chunk is loaded with a `<script>` element that carries the same nonce as
the client's own script tag, so a strict `script-src 'nonce-…'` policy needs
nothing more. The chunk URLs come from `static()`, passed to the client in the
tag's `data-djust-chunks` attribute, so `ManifestStaticFilesStorage` /
WhiteNoise hashed names and a CDN `STATIC_URL` are used as for any other
static file. A policy that lists script hosts must allow the host that serves
your static files, as it already does for the client.

## The full bundle

The single bundle contains every module and loads nothing on demand. Use it
when one extra request per feature is not wanted, for example on a page that
must work offline from a service worker that precaches only the client:

```python
LIVEVIEW_CONFIG = {
    "client_bundle": "full",  # inject client.min.js (client.js under DEBUG)
}
```

A template that includes the client itself,
`<script src="{% static 'djust/client.js' %}">`, gets the full bundle as
before. If both a full and a core bundle end up on one page, the first one to
run is used and the other does nothing.
