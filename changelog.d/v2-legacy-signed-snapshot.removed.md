- **BREAKING: the legacy signed back-navigation snapshot.** A view that set
  `enable_state_snapshot = True` no longer has its public attributes signed and
  sent to the browser, and a token a 1.x server signed is refused.
  Back and a reconnect still restore an `enable_state_snapshot` view from the
  state saved in the session, which this release keeps. Where nothing is saved
  there (`LIVEVIEW_CONFIG['view_state_in_session'] = False`, no or an expired
  session, an unresolved tenant), the view now mounts fresh instead of
  restoring from the token.
  Removed with it:
  `djust.security.state_snapshot` (`sign_snapshot`, `unsign_snapshot`,
  `SNAPSHOT_SALT`), the `LiveView._restore_snapshot()` override, the `strict=`
  argument of `LiveView._capture_snapshot_state()` and
  `NonPersistableStateError`. Importing `sign_snapshot`, `unsign_snapshot` or
  `SNAPSHOT_SALT` from `djust.security` raises `ImportError` naming the
  replacement: declare the fields the browser may hold with
  `state(..., persist="client", client=True)`, whose token djust signs and
  verifies itself. Attribute access raises the same `ImportError`, so
  `hasattr()` on one of those names raises rather than returning `False`. A
  view class that defines `_restore_snapshot()` is refused with
  `ImproperlyConfigured` (when the class is created, and on the mount path),
  since nothing would call it. `enable_state_snapshot` still saves a legacy view's state
  in the session and restores it on a reconnect. See
  [Migrating to 2.0](docs/website/guides/migrating-to-2.0.md#python-names).
