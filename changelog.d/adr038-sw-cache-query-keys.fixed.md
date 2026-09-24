- **Service-worker state and VDOM cache entries are keyed by pathname plus
  query string (ADR-038 E3-8).** `/orders?page=1` and `/orders?page=2` used to
  share one entry, so Back could restore the wrong page's snapshot or HTML.
  Capture and lookup both normalize through `djust._sw.cacheKey`.
