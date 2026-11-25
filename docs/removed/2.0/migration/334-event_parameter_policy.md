---
table: settings
covers: event_parameter_policy
removed: `event_parameter_policy` in `LIVEVIEW_CONFIG` or `DJUST_CONFIG`
use: Delete the key; djust refuses it with `ImproperlyConfigured` at startup. Strict event parameters are the only policy: `manage.py check` reports each handler parameter strict refuses as `djust.V016`. System check `djust.C045` is retired.
---
