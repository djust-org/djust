- **The HTTP POST fallback now logs when it drops background work.** A handler
  that calls `start_async` or is decorated `@background` queues work that the
  HTTP fallback never runs, because it has no channel to deliver the result.
  The tasks used to be discarded with no sign. The fallback now logs one
  WARNING per view class, naming the handler. Behaviour is otherwise
  unchanged; what the fallback should do instead is an open 2.0 design
  question (#3500). Regression cases in
  `python/tests/test_http_fallback_background_warning.py`.
