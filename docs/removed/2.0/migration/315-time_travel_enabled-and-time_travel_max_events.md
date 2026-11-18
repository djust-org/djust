---
table: settings
covers: time_travel_max_events
removed: `time_travel_enabled` and `time_travel_max_events` in `LIVEVIEW_CONFIG` or `DJUST_CONFIG`
use: Delete the keys; djust refuses them with `ImproperlyConfigured` at startup. Time-travel debugging was removed (#4184). System checks `djust.C501`, `djust.C502` and `djust.V014` are retired. `bug_capture_default_scrub` stays: the State tab's Share button applies it.
---
