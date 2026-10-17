- **One builder for the `djust:error` detail of a server error (#3500).** The
  WebSocket and SSE `error` frames and the HTTP fallback's error body now build
  the shared fields (`error`, `code`, `transient`, `view`, `target_id`,
  `traceback`) in one function in
  `python/djust/static/djust/src/02c-frame-dispatch.js`. Each transport reports
  exactly the fields it did before; the WebSocket still adds `event` and
  `validation_details`.
