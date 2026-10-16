- **The lifecycle contract says what a failed mount leaves behind.** If any
  step of a live mount raises, no view stays mounted; a `PermissionDenied`
  from `mount()`, `handle_params()` or `connected()` sends a
  `permission_denied` error frame and closes the WebSocket with code 4403,
  and any other exception sends the usual error frame. Stated in the
  LiveView API reference and the best-practices guide.
