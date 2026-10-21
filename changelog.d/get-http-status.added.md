- **`LiveView.get_http_status()` sets the HTTP status of the first render.**
  Return `404`, `403` or any status (default `None`, which keeps 200); it is
  read after `mount()` and the render, so it can depend on what `mount()` found,
  and the page body is kept. The WebSocket path never consults it. The
  `djust.V013` hint (an override of `get()` never runs on the socket mount) now
  points to it. The async/streaming `aget` path keeps the status too. 4 cases in
  `python/djust/tests/test_http_status_hook.py`.
