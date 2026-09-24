- **Wire staged explicit HTTP persistence to declared server state.** GET/POST
  no longer persist render context or legacy private/component snapshots in
  explicit mode. Bind stored state to middleware user/tenant, session and route;
  validate schema before restoration and retain fresh state on rejected envelopes.
  Explicit construction remains gated pending the other runtime exporters.
