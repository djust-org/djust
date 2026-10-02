- **The `reauth_on_event` re-check now denies when it raises instead of allowing
  the event.** With `LIVEVIEW_CONFIG["reauth_on_event"]` on, an exception other
  than `PermissionDenied` from the per-event authorization check (for example
  `Http404` or `DoesNotExist` from `check_permissions()`, or a failing session or
  authentication backend) was logged at DEBUG and the event ran anyway. The
  WebSocket and SSE transports now log the exception type and view class at
  WARNING (never the message) and take the normal refusal path: navigate to
  login and close 4403 on WebSocket, an auth-error frame and end of stream on
  SSE. Clean allow and deny results are unchanged, and `reauth_on_event` stays
  off by default. Regression cases in
  `python/djust/tests/test_reauth_fail_closed.py`.
