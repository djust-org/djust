---
table: python
removed: System check `djust.V011` (sticky child and parent snapshot opt-in)
use: Remove it from `SILENCED_SYSTEM_CHECKS`; nothing reports it. `djust.C304` now warns about a `client=True` field with a credential-like name.
---
