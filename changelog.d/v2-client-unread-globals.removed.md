- **Unread client bootstrap globals and the unused `debug-panel.min.js`
  (#3500).** The injected bootstrap script no longer sets
  `window.DJUST_DEBUG_VDOM`, `window.DJUST_WS_COMPRESSION` or
  `window.DJUST_LOADING_GROUPING_CLASSES`: no client code read them. The
  `debug_vdom` setting still drives Rust VDOM tracing. `scripts/build-client.sh`
  no longer minifies the debug panel, and the committed `debug-panel.min.js`
  (138 KB, never served: DEBUG pages load `debug-panel.js`) is deleted.
