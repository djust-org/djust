- **Turn-gate the template backend and PWA sync log lines for staged explicit
  exposure.** `DjustTemplate`'s JIT serialization fallbacks and the PWA sync
  endpoint, batch sync and custom conflict-resolver catches logged exception
  text that can quote model data. Inside a nonlegacy view's turn they now log
  the value-free line; outside a LiveView turn, and for legacy views, logging
  is unchanged.
