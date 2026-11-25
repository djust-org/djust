---
table: python
covers: parameter_policy
removed: `@event_handler(parameter_policy="legacy")` and `@server_function(parameter_policy="legacy")`
use: Annotate the handler's parameters (`Any` accepts a value unchecked), send `dj-value-*` from the template instead of `data-*`, and delete the argument. Refused with `ImproperlyConfigured` when the decorator applies. `parameter_policy="strict"` is accepted and does nothing. System check `djust.V036` is retired.
---
