---
table: python
removed: `NonPersistableStateError`, and the `strict=` argument of `LiveView._capture_snapshot_state()`
use: Nothing. A client field has to be JSON, and a model or `Decimal` is refused when the token is built.
---
