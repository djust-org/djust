- **`start_async()` queued in `handle_params()` now runs on a URL change.**
  Browser Back/Forward and `dj-patch` call `handle_params()` through the
  `url_change` path, which rendered and flushed every queue but never
  dispatched the background work `handle_params()` queued, so the read silently
  did not happen. It is dispatched after the frame, as on mount and events.
  Covered in `python/djust/tests/test_url_change_async_and_mount_query.py`,
  which also pins that the socket mount's `request.GET` carries the page's
  query string.
