- **`reauth_on_event` now covers every live turn, and SSE frames are
  rate-limited on arrival (ADR-044).** The re-check runs once, as the turn
  chain's `reauth` built-in, instead of in three hand-written copies. Two turns
  it missed are now re-checked: a legacy view's `url_change` (`handle_params`)
  and background results on the shared runtime (`start_async` / `@background`
  over SSE, and over WebSocket for event-started work); `push_event` delivery
  to a socket is gated like presence. Over SSE, root-view events, `url_change`
  and page mounts were not charged to the per-connection rate limiter; every
  POSTed frame now is, as the WebSocket charges every frame.
