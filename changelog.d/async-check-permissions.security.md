- **An `async def check_permissions` no longer allows every request.** It was
  called without being awaited; the coroutine is truthy, so the check passed
  for everyone on every transport (fail open). It is now awaited everywhere the
  check runs: on the event loop over WebSocket and SSE, and from the request
  thread on the HTTP GET, the page-POST fallback, the API and embedded
  children. A synchronous caller on the event loop's own thread refuses with
  an error instead.
