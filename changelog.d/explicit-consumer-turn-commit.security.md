- **Explicit views: tick, push, NOTIFY and released-event turns are
  authorized and persisted.** A staged explicit root's server-originated turns
  ran with the mount-time principal and never saved declared state:
  `handle_tick`, `server_push` handlers, `db_notify` → `handle_info`,
  NOTIFY-released activity events and the `start_async` work they start. A
  revoked session kept receiving renders, and a reconnect restored stale state.
  Each turn is now authorized against a freshly loaded session before its
  application hook runs (a revoked turn gets the foreground denial), and
  declared server state is committed before its frame. Presence heartbeats and
  cursor moves never render or persist, so they are unchanged. Legacy views are
  unchanged.
