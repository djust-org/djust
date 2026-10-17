- **The HTTP POST fallback no longer drops `push_event`, `set_layout` and
  `defer()` (#3500).** An event answered over HTTP carried only navigation,
  flash and page metadata. Its JSON answer now also carries `_push_events`
  (`{event, payload}` per `push_event` / `push_commands`) and `_layout`
  (`{path, html}` for `set_layout`), and `defer()` callbacks run after the
  answer is built, as on the WebSocket, including for an embedded child's
  event. The client applies `_push_events` and `_layout` once its
  `applyHttpSideChannels` learns them. 10 regression cases in
  `python/tests/test_http_side_channels_push_layout_defer.py`.
