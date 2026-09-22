- **Redact consumer hook failures for staged explicit exposure.** The
  WebSocket consumer's `presence_heartbeat`, `cursor_move` and `server_push`
  catches logged the exception raised by application code —
  `update_presence_heartbeat`, `handle_cursor_move`, or a pushed handler — without
  a policy check, `server_push` with its traceback. A nonlegacy owner now gets the
  value-free line `handle_exception` uses, checked against both the hook's view and
  the current owner. Legacy logging, including the `server_push` traceback, is
  unchanged; explicit exposure is still gated by the remaining ADR-038 acceptance
  work.
