---
table: python
covers: time_travel_enabled, time_travel_excluded_fields, djust.time_travel, djust.bug_capture.encode_view_state
removed: Time-travel debugging: `time_travel_enabled = True`, `time_travel_excluded_fields`, the `djust.time_travel` module (`TimeTravelBuffer`, `EventSnapshot`), `djust.bug_capture.encode_view_state()`, the debug panel's Time Travel tab, and the `time_travel_jump` / `time_travel_component_jump` / `forward_replay` / `bug_capture_share` frames
use: Delete the attributes; a view that sets either to a true value is refused with `ImproperlyConfigured` when the class is created, or when it mounts if the value is set on the instance or later. To follow state changes event by event, open the debug panel's **State** tab (with `DEBUG = True`); its **Share** button copies a bug capture with the event's real patches, leaving out `LIVEVIEW_CONFIG['bug_capture_default_scrub']`. From code: `BugCapture(state_before=..., state_after=..., vdom_patches=...).encode(scrub=scrub_fields(...))`. See [What does not survive](#5-what-does-not-survive).
---
