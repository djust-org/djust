- **BREAKING: `PersistentLiveView`.** The `LiveView` subclass that set
  `enable_state_snapshot = True` is removed. Importing it from `djust` or
  `djust.live_view` raises `ImportError` naming the replacement: subclass
  `LiveView` and declare the fields to keep with `state(..., persist="server")`.
  See [Migrating to 2.0](docs/website/guides/migrating-to-2.0.md#python-names).
