- **Limit HTTP API `assigns` to declared client fields for staged explicit
  exposure.** The ADR-008 HTTP API returned every public attribute a handler
  changed, with no policy check, so an explicit view's undeclared attributes
  reached the client. For a nonlegacy view the `assigns` diff now carries only
  declared `client=True` fields, the projection `get_state` uses. Handler,
  `server_function` and view-initialization failures no longer log their
  exception text for a nonlegacy view. Legacy responses and logs are unchanged.
