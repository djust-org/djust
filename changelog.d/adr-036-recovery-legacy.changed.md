- **`dj-auto-recover` handlers always run under the legacy parameter policy
  (ADR-036 decision R1).** A handler that a literal `dj-auto-recover` in the
  view's own template targets is dispatched, and advertised to the browser,
  as legacy, even in a project using the staged strict policy, so its
  `_form_values` / `_data_attrs` dictionaries keep working. An explicit
  `parameter_policy="strict"` on such a handler is reported at startup as
  the warning `djust.V019`. 7 collected cases in
  `python/djust/tests/test_recovery_handler_policy.py`.
