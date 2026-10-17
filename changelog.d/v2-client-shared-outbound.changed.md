- **The WebSocket and SSE client transports build mount and event frames in one
  place (#3500).** The mount frame, the event request (register, send, release
  on failure), and remounting or unmounting the views mounted beside the page
  view move into `python/djust/static/djust/src/02c-frame-dispatch.js`.
  Behaviour and wire frames are unchanged.
