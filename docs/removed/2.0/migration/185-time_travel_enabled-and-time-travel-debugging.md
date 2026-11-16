---
table: python
covers: time_travel_enabled, time_travel_excluded_fields, djust.time_travel, djust.bug_capture.encode_view_state
removed: Time-travel debugging: `time_travel_enabled = True`, `time_travel_excluded_fields`, the `djust.time_travel` module, `djust.bug_capture.encode_view_state()`, the debug panel's Time Travel tab and Share button, the `time_travel_jump` / `time_travel_component_jump` / `forward_replay` / `bug_capture_share` frames, and the `reset_view_state` / `eval_handler` observability endpoints and MCP tools
use: Delete the attributes; a view that sets either to a true value is refused with `ImproperlyConfigured` when the class is created, or when it mounts if the value is set on the instance or later. To follow state changes event by event, open the debug panel's **State** tab (with `DEBUG = True`), which lists each event with the state after it and what changed. To share a capture, build one from the states you choose: `BugCapture(state_before=..., state_after=..., vdom_patches=...).encode(scrub=scrub_fields(...))`. See [What does not survive](#5-what-does-not-survive).
---
