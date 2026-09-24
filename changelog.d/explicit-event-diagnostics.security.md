- **Redact staged explicit-view runtime event diagnostics.** Handler and render
  failures no longer expose exceptions through the covered diagnostic logs,
  error frames, and traceback ring, including policy transitions during rendering.
  Preserve legacy diagnostics and deferred-handler response behavior. Explicit
  exposure remains gated pending the remaining ADR-038 acceptance work.
