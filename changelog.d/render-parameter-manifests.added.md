- **Render-bound parameter contracts.** Stage fresh owner-scoped parameter
  contracts on shared-runtime render responses. Removed strict owners produce
  explicit contract clears; legacy-only sessions retain their response shape.
  Contract discovery failures suppress the DOM response with a redacted error.
  Client installation and complete transport coverage remain prerequisites for
  activating strict browser bindings.
  URL changes now share the transport render lock and recheck their mounted
  owner after waiting, keeping them serialized with events and background results.
