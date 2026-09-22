- **Components under explicit exposure are bound per view and complete (ADR-038
  E2-7).** Behind the explicit-exposure guard, ADR-034 interactive declarations
  (for example `DropdownMenu`) are now registered component providers, so they
  render and dispatch instead of silently missing from the context. A State-less
  class-level `LiveComponent` gives each nonlegacy view its own copy instead of
  the shared class-level object, so two views of one class no longer share
  component state. Components are transient under explicit exposure: their
  state is not persisted and a reconnect remounts them from their declarations,
  while declared `state(persist="server")` fields still restore. Legacy views
  are unchanged. 10 regression tests in
  `python/djust/tests/test_exposure_components.py`.
