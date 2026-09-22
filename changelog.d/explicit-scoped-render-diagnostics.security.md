- **Redact scoped component render failures for staged explicit exposure.**
  When an ADR-032 scoped component render failed before falling back to the
  full render, the runtime logged the exception with its traceback at DEBUG for
  any policy. A nonlegacy owner now gets the value-free line; legacy logging is
  unchanged.
