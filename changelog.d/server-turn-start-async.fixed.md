- **`start_async` work queued from a tick, `server_push` or `db_notify` turn now
  runs.** `handle_tick`, `server_push` handlers and `handle_info` run on the
  WebSocket consumer's own turns, and none of those turns dispatched queued
  background work. A `start_async` call there sat unrun until some later event
  happened to drain it, or never ran. Each turn now dispatches its queued work
  once its hook succeeds, and explicit child work queued there runs under the
  child's own authorized path. This affects legacy views too.
