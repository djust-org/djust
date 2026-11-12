- **BREAKING: the legacy signed back-navigation snapshot.** A view that set
  `enable_state_snapshot = True` no longer has its public attributes signed and
  sent to the browser, and a token a 1.x server signed is refused, so the
  first Back after the upgrade mounts fresh. Removed with it:
  `djust.security.state_snapshot` (`sign_snapshot`, `unsign_snapshot`,
  `SNAPSHOT_SALT`), the `LiveView._restore_snapshot()` override, the `strict=`
  argument of `LiveView._capture_snapshot_state()` and
  `NonPersistableStateError`. Importing `sign_snapshot`, `unsign_snapshot` or
  `SNAPSHOT_SALT` from `djust.security` raises `ImportError` naming the
  replacement: declare the fields the browser may hold with
  `state(..., persist="client", client=True)`, whose token djust signs and
  verifies itself. `enable_state_snapshot` still saves a legacy view's state
  in the session and restores it on a reconnect. See
  [Migrating to 2.0](docs/website/guides/migrating-to-2.0.md#python-names).
