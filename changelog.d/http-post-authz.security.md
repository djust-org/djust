- **HTTP POST fallback (`RequestMixin.post()`) enforced none of djust's three
  authorization layers, so an unauthenticated or under-privileged caller could
  drive `@event_handler` methods on a `login_required = True` view with a
  plain POST.** `get()` and every WS/SSE event path already enforced
  view-level `check_view_auth`, handler-level `check_handler_permission`, and
  the ADR-017 object-level check; the POST transport now runs all three
  before dispatch, with the same denial shapes (403 `{"redirect": <login_url>}`
  for the unauthenticated case, 403 `{"error": "Permission denied"}` for
  view/handler permission denials, 403 `{"error": "Access denied for this
  object."}` for object-level denials). Anonymous POSTs to views without auth
  requirements are unchanged. Also fixes an `UnboundLocalError` in `post()`'s
  own error path: a body that was not valid JSON raised from the `except`
  handler itself, masking the real exception as an unlogged 500. 7 regression
  cases in `python/tests/test_http_post_authz.py`.
