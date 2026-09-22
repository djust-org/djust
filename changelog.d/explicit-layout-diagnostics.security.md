- **Redact `set_layout` render failures for staged explicit exposure.** When the
  layout requested with `set_layout` failed to render, the runtime and the
  WebSocket consumer logged the exception and its traceback for any policy. A
  nonlegacy owner now gets the value-free line `handle_exception` uses, through
  a shared `log_failure` gate that keeps the original message, level and
  traceback wherever diagnostics are allowed. Legacy logging, and the `DEBUG`
  re-raise, are unchanged.
