- **`LiveViewSmokeTest` fuzzes exactly the handlers dispatch resolves
  (ADR-037).** It no longer fuzzes undecorated public methods. With the
  default `event_security = "strict"` the server refuses to call them; under
  `"warn"` or `"open"` they are still callable but are no longer fuzzed, so
  decorate them (or test them directly) to keep that coverage. So a smoke
  suite sends fewer events, and a failure it reported on such a method no
  longer appears. A strict-policy handler is
  fuzzed with its contract's parameter metadata. Components and server
  functions stay excluded.
