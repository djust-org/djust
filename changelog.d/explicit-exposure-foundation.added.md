- **Internal foundations for explicit state exposure (ADR-038).** Add typed
  declaration permissions, bounded purpose-specific projections, schema-checked
  restore preparation, and a server-session adapter with identity and expiry
  validation. Explicit LiveView exposure remains unavailable until all runtime
  exporters enforce these contracts; unsupported opt-ins fail instead of silently
  using legacy reflection. Existing legacy behavior remains the default.
