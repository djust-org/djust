- **Contain protected failures at the shared inbound-message boundary.**
  Staged explicit-view failures no longer escape runtime-owned messages into
  WebSocket diagnostics or Django's SSE HTTP error pages. Generic event errors
  retain numeric request correlation; failed delivery attempts a value-free
  transport close. Restrictions survive nested exceptional unwinding and reset
  at turn exit. Legacy behavior and cancellation remain unchanged. Explicit
  exposure still requires the remaining ADR-038 activation gates.
