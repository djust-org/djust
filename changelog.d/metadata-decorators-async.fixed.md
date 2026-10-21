- **`@debounce`, `@throttle`, `@cache`, `@client_state`, `@rate_limit`,
  `@permission_required` and `@optimistic` keep an `async def` handler a
  coroutine function.** They wrapped every handler in a plain `def`, so djust
  ran an async handler as sync code, got an un-awaited coroutine back and the
  body never ran, with no error. The wrapper is now `async` when the handler is.
  22 cases in `python/djust/tests/test_metadata_decorators_async.py`, including
  a WebSocket run of decorated async handlers.
