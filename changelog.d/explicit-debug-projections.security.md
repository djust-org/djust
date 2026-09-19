- **Route staged explicit-policy debug output through bounded projections.**
  Observability assigns, debug-panel variables, runtime context diagnostics and
  time-travel recording no longer reflect arbitrary view values for this policy.
  Redacted records cannot restore state or replay handlers. Explicit LiveView
  construction remains disabled pending the remaining ADR-038 runtime boundaries;
  legacy behavior is unchanged.
