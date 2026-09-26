- **One dispatch-context rule for the staged ADR-036 strict parameter
  policy.** A strict handler now receives the same application arguments
  whichever transport delivered the event. The transport keys
  `_cacheRequestId` and `_activity` are dropped before binding. Before, a
  strict event carrying either one was rejected over the HTTP fallback, the
  exposed API, server functions, the test client, replay and actor views. An unconsumed `view_id` or `component_id` fails closed instead of
  reaching the root handler. Unknown `_` keys in the flat HTTP body are
  rejected instead of silently discarded. Strict contracts can declare
  framework-supplied (trusted) parameters that no client key or positional
  value can fill, and the staged ADR-034 output callbacks now bind their
  payload through that contract with the source component supplied by the
  framework. Legacy handlers are unchanged. 68 collected cases in
  `python/djust/tests/test_trusted_dispatch_context.py`.
