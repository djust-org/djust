- **`dj-auto-recover` targets are found in included and parent templates
  too (ADR-037).** The class-level scan that forces recovery handlers onto
  the legacy parameter policy (ADR-036 decision R1) now uses the template
  binding scan. It follows `{% include %}` and `{% extends %}` and keeps every
  `{% if %}` branch, so more handlers are legacy-forced from mount. For
  example, a recovery form inside a conditional include was strict until an
  event rendered it; now it is legacy from mount. Computed targets are still
  seen only in the render. 17 cases in
  `python/djust/tests/test_recovery_handler_policy.py`.
