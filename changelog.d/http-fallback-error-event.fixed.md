- **The HTTP fallback now reports failed events.** When an event failed over
  the HTTP fallback, the client wrote only to the browser console, so an
  HTTP-only page could not show the failure. It now dispatches `djust:error`
  with the server's error message, as the WebSocket and SSE transports do. The
  DEBUG error overlay and application listeners see it.
