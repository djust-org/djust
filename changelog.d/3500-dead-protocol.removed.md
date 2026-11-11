- **Removed dead wire-protocol paths (#3500).**
  - The `use_binary` msgpack patch branch in `LiveViewConsumer`: the flag was
    always `False` and the client could not decode binary patches. Inbound
    binary frames (uploads, and msgpack-encoded inbound messages) are
    unchanged.
  - The inbound `presence_heartbeat`, `cursor_move`, `debug_panel_open` and
    `debug_panel_close` WebSocket frames, which no client sends; they now get
    the usual "Unknown message type" error. The client `ping` is still the
    presence heartbeat, and `LiveCursorMixin.handle_cursor_move()` is still
    called from the app's own event handler. With the panel frames gone, the
    `_debug_panel_active` gate on the DEBUG-only `_debug` payload went too.
  - `djust.sse.DjustSSEEventView` and its `sse/<session_id>/event/` route
    (`djust-sse-event`). Post `{"type": "event", ...}` to
    `sse/<session_id>/message/` (`DjustSSEMessageView`), which the client has
    used since #1237.
  - The readers of the legacy single-task `_async_pending` attribute, which
    nothing set; `start_async` queues into `_async_tasks`.
  - The `_push_events_flush_callback` fallback in `flush_push_events()`;
    the flush resolves through `view._ws_consumer`, which the WebSocket
    transport always sets alongside it.
