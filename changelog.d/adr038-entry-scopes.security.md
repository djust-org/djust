- **Explicit-view errors on the HTTP, SSE and WebSocket entry points (ADR-038
  D-a):** For a view whose `exposure_policy` is not `"legacy"`, an exception
  from its constructor, `on_mount` hooks, `mount()`, `get_context_data()` or a
  recovery render no longer reaches the client, the log or the traceback ring
  with its message in production (`DEBUG = False`). The HTTP GET (including
  `streaming_render`), the SSE stream GET and SSE navigation answer with the
  project's generic 500 page and log a static line. Under `DEBUG` these errors
  show Django-like detail, the technical 500 page included.
  `got_request_exception` still fires, with a value-free `ExposureError`. The
  WebSocket `receive` catch-all (`request_html`, `live_redirect_mount`,
  `mount_batch`, uploads, presence, time travel) and constructor failures on
  every transport send the generic error frame. Legacy views are unchanged.
