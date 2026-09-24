- **Embedded background work:** Legacy child events now run and render their
  own background tasks instead of draining the parent's queue. Removed child
  instances cannot deliver stale results, and batch completion releases the
  originating loading state.
