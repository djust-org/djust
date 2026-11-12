---
table: python
covers: enable_state_snapshot
removed: `enable_state_snapshot = True`, on a view or a mixin
use: `state(..., persist="server")` on each field that must survive a reconnect, and `persist="client", client=True` for one the browser may hold across Back (example below). A view that still sets it is refused with `ImproperlyConfigured` when the class is created, or when it mounts if the value is set on the instance or later. `False` is accepted.
---
