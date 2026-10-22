- **A closing socket's teardown no longer races a render of its view still in
  flight** (`clear_live_handles` / `set_raw_py_values failed during teardown:
  Already borrowed`, most often with `worker_threads` on). The teardown dropped
  the Rust view's live handles on the event loop while a render of the same
  view (a tick, a deferred push or a background result already queued on its
  sync thread) could still hold the view; the handles then stayed. The
  WebSocket and runtime teardowns now use `arelease_root_view`, which clears the
  handles as a hop on the view's sync thread, after that render. 4 cases in
  `python/djust/tests/test_teardown_waits_for_render.py`, with the pool off and
  on.
