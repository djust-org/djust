- **Render-bound client parameter contracts.** WebSocket and SSE clients refresh
  staged parameter metadata after applying the matching DOM response and before
  binding initialization. Buffered frames retain their transport and receipt
  order; older replay cannot replace a newer snapshot. Invalid metadata fails
  closed without preventing child-request acknowledgement. Native strict event
  binding remains disabled pending complete delivery and owner-lifetime checks.
