- **A WebSocket frame over `max_message_size` now gets an actionable error.**
  The server announces the limit in its `connect` frame, and the client no
  longer sends a larger frame: it reports an `error` with code
  `message_too_large` (logged and dispatched as `djust:error`) and settles the
  event's loading state. A frame that reaches the server anyway is answered with
  the same code, the limit, and a pointer to `UploadMixin` and the setting. The
  `Message too large (N bytes)` prefix that clients parse is unchanged. The limit
  is now documented in the security and uploads guides. 2 cases in
  `python/tests/test_message_size_limit.py`, 4 in
  `tests/js/message_size_limit.test.js`.
