- **Removed the dead accessibility flush (#3500).** `LiveViewConsumer` and
  `ViewRuntime` each called `_drain_announcements()` / `_drain_focus()` at the
  end of every turn, behind a `hasattr` guard, but no djust class defines
  either method, so no `accessibility` or `focus` frame was ever sent. Both
  frame types are also gone from the page-level frame set in
  `python/djust/_view_slots.py`. Programmatic focus is unaffected: it goes
  through `JS.focus(...)` and `push_commands`.
