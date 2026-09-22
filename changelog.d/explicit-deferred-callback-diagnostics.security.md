- **Redact deferred-callback failures for staged explicit exposure.** When a
  `self.defer(...)` callback raised, the runtime, the WebSocket consumer and the
  SSE transport logged the exception and traceback, and, for a callable without
  a qualified name such as `functools.partial`, its `repr` including bound
  arguments. A nonlegacy owner now gets the value-free line. Legacy logging,
  level and traceback are unchanged.
