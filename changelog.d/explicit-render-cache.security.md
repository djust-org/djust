- **Isolate staged explicit renderers from legacy state caches.** Explicit-exposure
  views no longer read or write the shared legacy Rust render cache. Render
  context and VDOM are not declared persistence state; policy transitions also
  discard a legacy renderer before explicit rendering. The production explicit
  exposure guard remains closed pending ADR-038 acceptance.
