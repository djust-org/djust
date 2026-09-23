- **Explicit views: background, tick, push and NOTIFY turns refresh the
  back-navigation snapshot.** When one of these turns changed a
  `persist="client"` field, back-navigation still offered the token from the
  last user event. The result frame now carries the refreshed signed token for
  the primary view, and the client accepts it from primary-view `async`,
  `tick` and `broadcast` frames as it does from event acknowledgements. Child
  frames still can't replace it.
