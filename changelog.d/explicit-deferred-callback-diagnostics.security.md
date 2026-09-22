- **Redact deferred-callback failures for staged explicit exposure.** When a
  `self.defer(...)` callback raised, both the runtime and the WebSocket consumer
  logged the exception and traceback, and, for a callable without a qualified
  name such as `functools.partial`, its `repr` including bound arguments. A
  nonlegacy owner now gets the value-free line. Legacy logging, level and
  traceback are unchanged.
