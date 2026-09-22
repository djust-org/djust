- **Redact post-event state-save failures for staged explicit exposure.** An
  explicit view's save projects its declared `persist="server"` values, and
  storage exceptions propagate, so a failed save could log server-only data in
  the exception and traceback. The runtime now logs the value-free line for a
  nonlegacy owner, for the view and for sticky children. Legacy logging is
  unchanged.
