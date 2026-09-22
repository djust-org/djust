- **Explicit-exposure child work queued at mount or by a parent turn now runs
  (ADR-038 E3-3).** `start_async` called in an explicit child's `mount()`, or
  queued on a child by a parent handler, used to wait for that child's next
  routed event (and ran under that event's batch), or never ran. The runtime now
  dispatches it after the mount frame or the parent acknowledgement, on the
  child's own batch and re-authorized completion path. Legacy children are
  unchanged.
