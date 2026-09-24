- **Stale HTTP responses:** HTTP fallback ignores outgoing-page responses when
  the root, URL or navigation generation changes while awaiting headers or
  parsing the response body, preventing stale metadata and render effects.
