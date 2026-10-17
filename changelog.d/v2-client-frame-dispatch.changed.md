- **The WebSocket and SSE client transports share one inbound frame path
  (#3500).** The receipt-time routing (page-shell fallback, mount metadata,
  signed snapshot, frames for views mounted beside the page view) and the
  side-channel frames both transports applied the same way now live once, in
  `python/djust/static/djust/src/02c-frame-dispatch.js`. Behaviour is
  unchanged, except that the SSE transport now also applies `layout` frames,
  the fix #3527 makes on `main`.
