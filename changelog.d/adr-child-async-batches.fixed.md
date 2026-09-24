- Track staged explicit-child background work as owned batches so loading waits
  for all tasks and background failures do not cancel newer foreground requests.
  Use actual child view wrappers, rather than per-control routing hints, when
  restoring loading state after DOM updates. Explicit exposure remains gated.
