- **`djust.V007` ("event handler missing `**kwargs`") is retired (ADR-037).**
  A closed handler signature is now encouraged: a catch-all hides a
  misspelled parameter. djust no longer emits V007 and never reuses the ID.
  Existing V007 suppressions have no effect and can be removed.
