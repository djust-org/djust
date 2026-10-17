- **Client compatibility branches for old servers and deprecated attributes
  (#3500).** The client ships with its own server, so 2.0 drops the code that
  only served older ones or long-deprecated markup: the per-view mount fallback
  for servers without `mount_batch` (pre-0.6.0); the `data.type` fallback for
  `navigation` frames without an `action`; the JSON `dj-params` attribute (use
  `dj-value-*`; it is also gone from the AI schema and the static binding
  checks); the pre-1.0 `[data-djust-root]` marker in `dj-layout` swaps (use
  `dj-root`); the `window.LiveViewWebSocket`, `window.setCacheConfig` and
  `window.globalLoadingManager` globals (use the same names on `window.djust`);
  and the no-op `initTodoItems` stub.
