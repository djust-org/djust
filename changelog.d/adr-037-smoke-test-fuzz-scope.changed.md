- **`LiveViewSmokeTest` fuzzes exactly the handlers dispatch resolves
  (ADR-037).** It no longer fuzzes undecorated public methods, which the
  server refuses to call. So a smoke suite sends fewer events, and a failure
  it reported on such a method no longer appears. A strict-policy handler is
  fuzzed with its contract's parameter metadata. Components and server
  functions stay excluded.
