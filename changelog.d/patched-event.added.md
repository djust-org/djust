- **The client dispatches `djust:patched` after every frame that changed the
  page**: a server push, a tick, an async result or the page's own event, with
  `detail.source` saying which (`"broadcast"`, `"tick"`, `"async"`, `"event"`).
  `djust:rendered` skips push frames, because it confirms a view's own state, so
  client code that re-syncs after any patch had nothing to listen to for a
  pushed render. `djust:rendered` is unchanged. 10 cases in
  `tests/js/patched-event.test.js`, over WebSocket and SSE.
