### Time-travel debugging → the debug panel's State tab

Time-travel debugging recorded a view's state before and after every event
(`time_travel_enabled = True`), and the debug panel could jump the view back
to a recorded state, restore one component, or replay an event from a past
state on a new branch. It ran only under `DEBUG`, and the jump, component jump
and replay worked only for legacy views. djust `2.0.0` removed it (#4184):
the `djust.time_travel` module, the per-view buffer, the
`time_travel_jump`, `time_travel_component_jump`, `forward_replay` and
`bug_capture_share` WebSocket frames, the debug panel's Time Travel tab and
its Share button, `djust.bug_capture.encode_view_state()` (which read the
buffer), the `reset_view_state` and `eval_handler` observability endpoints
with their MCP tools, the `LIVEVIEW_CONFIG` keys `time_travel_enabled`,
`time_travel_max_events` and `bug_capture_default_scrub`, and system checks
`djust.C501`, `djust.C502` and `djust.V014`.

A view that sets `time_travel_enabled` or `time_travel_excluded_fields` to a
true value is refused with `ImproperlyConfigured` when the class is created,
and again on the mount path. The removed config keys are refused at startup.
`from djust import time_travel` and
`from djust.bug_capture import encode_view_state` raise `ImportError` naming
the replacement. An older client that still sends one of the removed frames
gets the standard `Unknown message type` error; the connection keeps working.

To follow state changes event by event, open the debug panel's **State** tab
(with `DEBUG = True` and `LIVEVIEW_CONFIG['debug_panel']` left on). It lists
each event with the view's debug projection after it and what changed from
the event before, built from the `_debug` payload every event response carries
under `DEBUG`. A bug capture is still a supported format; build it from the
states you choose:

```python
from djust.bug_capture import BugCapture, scrub_fields

blob = BugCapture(
    state_before={"step": "claimant"},
    state_after={"step": "vehicle"},
    vdom_patches=[],
    event_name="next_step",
).encode(scrub=scrub_fields("password"))
```
