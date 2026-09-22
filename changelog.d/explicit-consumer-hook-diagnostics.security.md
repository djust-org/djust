- **Redact presence and cursor hook failures for staged explicit exposure.**
  The WebSocket consumer's `presence_heartbeat` and `cursor_move` catches
  logged the exception raised by `update_presence_heartbeat` or
  `handle_cursor_move` without a policy check. A nonlegacy owner now gets the
  value-free line `handle_exception` uses, checked against both the hook's view
  and the current owner. Legacy logging is unchanged; explicit exposure is still
  gated by the remaining ADR-038 acceptance work.
