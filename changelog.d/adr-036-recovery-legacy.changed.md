- **`dj-auto-recover` handlers always run under the legacy parameter policy
  (ADR-036 decision R1).** A handler that a `dj-auto-recover` binding
  targets is dispatched, and advertised to the browser, as legacy, even in a
  project using the staged strict policy, so its `_form_values` /
  `_data_attrs` dictionaries keep working. Targets are read from the HTML the
  server rendered (including `{% include %}`, `{% extends %}`, conditional
  and dynamic bindings), so a client cannot claim the downgrade. An explicit
  `parameter_policy="strict"` on such a handler is reported at startup as
  the warning `djust.V019`. 12 collected cases in
  `python/djust/tests/test_recovery_handler_policy.py`.
