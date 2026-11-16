---
table: settings
covers: time_travel_max_events, bug_capture_default_scrub
removed: `time_travel_enabled`, `time_travel_max_events` and `bug_capture_default_scrub` in `LIVEVIEW_CONFIG` or `DJUST_CONFIG`
use: Delete the keys; djust refuses them with `ImproperlyConfigured` at startup. Time-travel debugging and the bug-capture Share button that read them were removed (#4184). System checks `djust.C501`, `djust.C502` and `djust.V014` are retired.
---
