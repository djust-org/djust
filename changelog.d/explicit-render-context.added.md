- **Stage an explicit render-only context path (ADR-038).** Build base context
  from deliberate additions and component/action/stream providers instead of
  reflected attributes. Preserve native rendering inputs and diagnose reserved
  provider collisions. Explicit mode remains gated until persistence and browser
  exporters no longer reuse rendering context; legacy behavior is unchanged.
