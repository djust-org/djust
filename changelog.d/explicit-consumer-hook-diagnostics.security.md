- **Redact consumer hook failures for staged explicit exposure.** The
  WebSocket consumer logged exceptions raised by application hooks without a
  policy check: `update_presence_heartbeat`, `handle_cursor_move`, a
  `server_push` handler, `handle_tick` and a `db_notify` `handle_info`, the last
  three with their traceback. A nonlegacy owner now gets the value-free line
  `handle_exception` uses, checked against both the hook's view and the current
  owner. Legacy log output is unchanged at every converted site; explicit
  exposure is still gated by the remaining ADR-038 acceptance work.
