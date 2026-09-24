- **Redact value-quoting framework log lines for staged explicit exposure.**
  Three fail-soft catches logged application values: `LiveComponent` assign
  validation (`Cannot coerce <value>`), the `dj_suspense` fallback's template
  error, and a stream `dom_id=` factory failure on delete (the row's repr, with
  a traceback). Inside a nonlegacy view's turn each now logs the value-free
  line; legacy logging is unchanged.
