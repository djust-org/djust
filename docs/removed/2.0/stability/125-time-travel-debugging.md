### Time-travel debugging → the debug panel's State tab

Time-travel debugging recorded a view's state before and after every event
(`time_travel_enabled = True`). Recording and the bug-capture Share button
worked on explicit views too, as a redacted read-only history; jumping the
view back to a recorded state, restoring one component, and replaying an event
from a past state (with edited parameters, on a new branch) worked only on
legacy views. djust `2.0.0` removed it (#4184): the `djust.time_travel` module
(`TimeTravelBuffer`, `EventSnapshot`), the per-view buffer, the
`time_travel_jump`, `time_travel_component_jump`, `forward_replay` and
`bug_capture_share` WebSocket frames, the client events
`djust:time-travel-state`, `djust:time-travel-event` and
`djust:bug-capture-share-result`, the debug panel's Time Travel tab,
`djust.bug_capture.encode_view_state()` (which read the buffer), the
`LIVEVIEW_CONFIG` keys `time_travel_enabled` and `time_travel_max_events`, and
system checks `djust.C501`, `djust.C502` and `djust.V014`.

**Lost:** stepping the view back to an earlier state, restoring one component,
and replaying an event forward with edited parameters. **Kept:** the per-event
history (the debug panel's **State** tab lists each event with the state after
it and what changed), the Events tab's "Replay this event" button, and Share,
which moved to the State tab and now builds the capture in the browser with
the event's real patches, leaving out the names in
`LIVEVIEW_CONFIG['bug_capture_default_scrub']`.

A view that sets `time_travel_enabled` or `time_travel_excluded_fields` to a
true value is refused with `ImproperlyConfigured` when the class is created,
and again on the mount path. The removed config keys are refused at startup.
`from djust import time_travel` and
`from djust.bug_capture import encode_view_state` raise `ImportError` naming
the replacement. An older client that still sends one of the removed frames
gets the standard `Unknown message type` error; the connection keeps working.

A capture can still be built in code from the states you choose:

```python
from djust.bug_capture import BugCapture, scrub_fields

blob = BugCapture(
    state_before={"step": "claimant"},
    state_after={"step": "vehicle"},
    vdom_patches=[],
    event_name="next_step",
).encode(scrub=scrub_fields("password"))
```
