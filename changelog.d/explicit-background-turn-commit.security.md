- **Explicit views: background results are authorized and persisted, and a
  failed save withholds the success frame.** For a staged `exposure_policy="explicit"`
  root, `start_async` work ran its callback and `handle_async_result` and sent
  the re-render with the mount-time principal. A revoked session still received
  its result, and declared `persist="server"` state changed in the background was
  never saved, so a reconnect restored the old value. Background turns now
  re-authorize against a fresh session before the callback and again before
  handling the result. A revoked turn is dropped with the foreground denial
  (static error, close 4403), and declared state is committed before the
  result frame. On both foreground and background turns, a failed or timed-out
  explicit save now sends a static `state_error`, and no success frame, instead
  of being logged and ignored. That error carries a null snapshot revocation,
  which the client applies to the primary view's cached token. Legacy views are
  unchanged.
