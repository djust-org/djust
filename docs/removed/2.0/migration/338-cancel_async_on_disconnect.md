---
table: python
covers: cancel_async_on_disconnect
removed: `cancel_async_on_disconnect = True` on a view
use: Delete the attribute: a view's background work is always cancelled when its connection closes. Work that must finish says so with `start_async(fn, survive_disconnect=True)` or `@background(survive_disconnect=True)`. Refused with `ImproperlyConfigured` when the class is created and on the mount path; `False` is accepted.
---
