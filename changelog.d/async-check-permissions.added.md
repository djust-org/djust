- **`async def check_permissions(self, request)`.** A view's permission check
  may be a coroutine. Over WebSocket and SSE djust awaits it on the event loop,
  before `mount()` and, with `reauth_on_event`, before every event, URL change
  and server turn, so a check that does I/O no longer holds asgiref's shared
  sync thread (and every other connection's handlers and renders) while it
  waits. The sync form is unchanged. A refusal behaves exactly like a sync
  one, and an exception fails closed. Async callers of djust's auth helpers can
  use the new `djust.auth.acheck_view_auth`, `acheck_view_auth_lightweight` and
  `arun_pre_mount_auth`. See "Async checks" in the authentication guide and
  ADR-044 D10. Cases in `python/djust/tests/test_async_check_permissions.py`.
