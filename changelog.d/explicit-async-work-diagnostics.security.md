- **Redact consumer background-work failures for staged explicit exposure.**
  When a `start_async` callback or `handle_async_result` failed on the
  WebSocket consumer's background runner (the NOTIFY-released activity path,
  reachable since #2946), the exception and traceback were logged for any
  policy. A nonlegacy view now gets the value-free line; legacy logging is
  unchanged.
