- **`reauth_on_event` now also covers frames the server sends on its own.**
  With `LIVEVIEW_CONFIG["reauth_on_event"]` on, a WebSocket whose user was
  logged out or lost a permission was refused on its next client event but kept
  receiving `server_push` / `push_to_view`, `db_notify`, `tick`, presence and
  async-result frames for a view with `login_required` or
  `permission_required`. Each such turn on the default
  (legacy) exposure policy now runs the same fresh-principal check as an event,
  under the render lock and before the hook runs; on failure the socket gets
  navigate-to-login and close 4403 and nothing is rendered or sent. A passed
  check covers the socket for `LIVEVIEW_CONFIG["reauth_server_turn_interval"]`
  seconds (default 5; 0 re-checks every turn), so ticking views do not read the
  session store on every tick. On a socket that carries several views (the
  page view plus lazy or batched views) the check runs for the view the turn
  targets and the throttle is per view; a refused lazy or batched view is torn
  down on its own and the socket stays up for the others. A check that raises denies and logs only the
  exception type and view class at WARNING. Off by default; behaviour with
  `reauth_on_event` off, and for explicit-policy views, is unchanged. Tests in
  `python/djust/tests/test_server_turn_reauth.py`.
