- **Removed `@reactive` (`djust.reactive`, `djust.decorators.reactive`) (#3500).**
  It never worked on a LiveView: `LiveView` has no `update()`, so the decorator
  raised `TypeError` at class definition. On other hosts the decorated
  function's body was never called. Use a plain attribute (djust re-renders
  after every event handler) or `@computed`. Its `get_framework_schema` and
  MCP `get_decorators` entries are gone too.
