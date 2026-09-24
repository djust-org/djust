- **Explicit views show Django-like error detail under `DEBUG`.** With
  `DEBUG = True`, an explicit view's failures read like Django's own
  development output: the technical 500 page, detailed WebSocket and SSE error
  frames and dev overlay, full log lines with tracebacks, and traceback-ring
  entries. With `DEBUG = False` they stay value-free. Debug tooling projections
  (debug panel, time travel, bug capture) and SQL parameter capture keep their
  redaction in both modes.
