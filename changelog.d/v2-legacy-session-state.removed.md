- **BREAKING: legacy view state in the session.** A legacy-exposure view's
  public state (`liveview_<path>`), its components' state
  (`liveview_<path>_components`) and the `__mount_work` loading marker are no
  longer written to or read from the Django session. Each page-POST fallback
  request mounts the view again, runs the work `mount()` queued and applies the
  event, so state that lived only on the view (a "load more" offset, a form
  draft, an open dropdown) resets on every fallback POST, and an event
  addressed to an interactive component the previous request rendered is
  refused with 400 "Component not found", as it already was on an explicit
  view. `handle_restore()` runs only when an explicit view's declared state is
  restored. Declare the fields to keep with `state(..., persist="server")`.
  See [Migrating to 2.0](docs/website/guides/migrating-to-2.0.md#5-what-does-not-survive).
