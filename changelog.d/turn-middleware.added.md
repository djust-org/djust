- **Turn middleware: `LIVEVIEW_MIDDLEWARE` wraps every LiveView turn (ADR-044).**
  An ordered list of `async def mw(turn, next)` callables, shaped like Django
  middleware, runs around every turn on every transport (WebSocket, SSE, the
  HTTP page GET / POST fallback and the auto-exposed HTTP API): mount and
  reconnect (page, slot, `mount_batch`, `live_redirect`, lazy), client events
  and deferred `dj_activity` replays, `handle_params`, server push / `db_notify`
  / tick / presence / `push_event` delivery, `start_async` / `@background`
  starts and results, and the recovery render. `turn.kind` / `turn.source` /
  `turn.transport` say which. A middleware refuses with `djust.turns.Refuse`
  (`deny`, `login`, `redirect`, `flash`, `rate_limited`) or `PermissionDenied`,
  mapped to each transport's established frames and responses. Context
  variables it sets reach the handler and the background work the turn starts.
  Built-ins (`error_boundary`, `reauth`) run outermost and cannot be removed.
  Per-turn timings at `/_djust/observability/turn_timings/`; system check
  `djust.C026` rejects a sync or unimportable entry. Guide:
  `docs/website/guides/turn-middleware.md`. Tests in
  `python/djust/tests/test_turn_middleware_core.py`,
  `python/djust/tests/test_turn_middleware_transports.py` and
  `tests/js/turn-reconnect-hint.test.js`.
