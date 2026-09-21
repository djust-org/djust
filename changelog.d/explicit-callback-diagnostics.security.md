- **Protect callback exception diagnostics for staged explicit exposure.**
  Runtime waiter, time-travel and deferred-drain catches respect both initial
  and current owners. Native waiter predicates and activity queues also redact
  protected errors internally while retaining pending waiters and continuing
  queued events. Legacy logging and callback arguments remain unchanged;
  explicit exposure is still gated by the remaining ADR-038 acceptance work.
