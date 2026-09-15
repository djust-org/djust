- **The HTTP POST event transport now enforces the same authorization as every other
  transport.** `post()` applied only the event-name format check and the
  `@event_handler` decorator policy; the three authorization layers that `get()` and
  the WebSocket/SSE event paths apply — view-level `login_required` /
  `permission_required`, handler-level `@permission_required`, and the ADR-017
  object-level check — were not consulted, so an event could be dispatched to a
  caller the other transports would refuse. All three now run before dispatch, via
  the same `check_view_auth` / `check_handler_permission` /
  `enforce_object_permission` entry points the other paths use.
- **A malformed request body no longer masks its own cause.** `post()`'s `except`
  block referenced `event_name` and `params`, which are assigned inside the `try`,
  so a failure before their assignment (a body that is not valid JSON is the easy
  one) raised `UnboundLocalError` from the error path itself — reporting a
  controlled failure as an unexplained 500 and never logging the real exception.
  Both names are now initialised before the `try`.
