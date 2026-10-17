- **SSE pages ignored `set_layout()`.** The server sends the `layout` frame
  from the shared turn end, so it reaches SSE pages too, but the SSE transport
  had no case for it and dropped it as an unknown frame. It now swaps the
  layout as the WebSocket transport does. Covered by
  `tests/js/sse_layout_frame.test.js`, which runs on both transports.
