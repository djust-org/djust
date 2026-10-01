- **`djust_ai_context` carries the login-over-WebSocket and `get_context_data()` notes (#3304).**
  The generated `CLAUDE.md` / `.cursorrules` / copilot text now says, in the Security section, not to call `login()` in
  an event handler (over the WebSocket it cannot set the session cookie; use an HTTP login view, the accounts pages, or
  `dj-trigger-action` with `self.trigger_submit()`), and, in a new "State and `get_context_data()`" section, to set state
  in `mount()` and always call `super().get_context_data()` because the HTTP fallback rebuilds the view from the dict
  that method returned. Both notes restate `docs/website` and are pinned for all three formats in
  `python/djust/tests/test_ai_context_command_3297_3292.py`.
