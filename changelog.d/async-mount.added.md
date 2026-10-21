- **`mount()` and `handle_params()` may be `async def`.** A sync `mount()` runs
  on asgiref's one shared sync thread, so when many pages open at once every
  mount queues behind the other sessions' database reads. An `async def mount`
  is now awaited on the event loop over WebSocket and SSE, so its awaits (the
  async ORM, an async service) overlap instead; the HTTP render, the page-POST
  fallback, `{% live_render %}` children, lazy containers and
  `LiveViewTestClient` run it to completion. Before, an `async def mount` raised
  `TypeError` over the socket and never ran its body over HTTP. A sync `mount()`
  runs as before. 7 cases in `python/djust/tests/test_async_mount.py`, including
  an overlap test: eight async mounts are all inside `mount()` at once, while
  the same pages with a sync mount run one at a time.
