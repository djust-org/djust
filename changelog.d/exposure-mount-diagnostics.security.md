- **Redact staged nonlegacy mount diagnostics at every covered destination.**
  Runtime initialization, authorization, mount, URL-parameter and initial-render
  failures now use a shared value-free error mode: no exception inspection,
  traceback-ring capture, detailed log or DEBUG response. Policy transitions
  cannot grant detailed diagnostics during a failed mount. Legacy diagnostics
  remain unchanged; ADR-038's production activation guard remains closed.
