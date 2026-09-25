- **Strict pages kept working after a hot reload or `push_state()`
  (staged ADR-036).** The hot-reload patch frame and
  `StreamingMixin.push_state()` sent DOM updates without the public
  parameter-contract snapshot. That made a strict-policy client invalidate
  its contracts and refuse strict events until the next render, and a
  reload that changed handler declarations could advertise stale rules.
  Both now capture the snapshot in the same operation as the render; a hot
  reload whose contracts cannot be discovered falls back to a full page
  reload. Legacy sessions keep their frame shape.
