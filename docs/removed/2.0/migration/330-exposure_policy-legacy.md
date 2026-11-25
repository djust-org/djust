---
table: python
covers: exposure_policy
removed: `exposure_policy = "legacy"`
use: Supply the names the template reads from `get_context_data()`, declare the state a later event or a reconnect needs with `state()`, then delete the line; `python manage.py djust_exposure_inventory --fix` does the mechanical part. Refused with `ImproperlyConfigured` when the class is created and on construction. `exposure_policy = "explicit"` is accepted and does nothing; any other value is refused. System check `djust.V028` is retired.
---
