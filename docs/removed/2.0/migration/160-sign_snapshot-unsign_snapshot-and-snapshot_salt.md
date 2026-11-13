---
table: python
covers: _restore_snapshot, djust.security.sign_snapshot, djust.security.unsign_snapshot, djust.security.SNAPSHOT_SALT
removed: `sign_snapshot`, `unsign_snapshot` and `SNAPSHOT_SALT` from `djust.security`, the `djust.security.state_snapshot` module, and the `LiveView._restore_snapshot()` override
use: `state(..., persist="client", client=True)` fields. djust signs and verifies their token itself (example below). Importing one of the three names raises `ImportError` with this advice, and so does reading it as an attribute: `hasattr(djust.security, "sign_snapshot")` raises instead of returning `False`, so a compatibility shim must catch `ImportError`. A class that defines `_restore_snapshot()` is refused with `ImproperlyConfigured`; move what it did after restoring into `handle_restore()`.
---
