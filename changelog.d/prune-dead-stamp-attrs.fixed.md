- **Five never-bound event attributes removed from the `{% live_render %}`
  stamp list (#2869).** `dj-keypress`, `dj-mouseenter`, `dj-mouseleave`,
  `dj-viewport-enter`, and `dj-viewport-leave` sat in
  `_LIVE_RENDER_EVENT_ATTRS` — the list `{% live_render %}` scans when
  stamping `view_id` onto embedded elements — but no client module ever
  bound them: zero mentions in `static/djust/src/`, zero occurrences in the
  built client, and the only `'dj-' + eventType` construction in the client
  produces `dj-keydown`/`dj-keyup`. An element carrying one of these inside
  an embedded child was stamped exactly like a working directive while no
  listener was ever installed — no error, no warning, no event. Removing
  them from the stamp list is behaviour-neutral: working directives
  (`dj-click`, `dj-keydown.enter`, `dj-window-keydown.escape`) stamp
  byte-identically, and the only observable change is that the meaningless
  stamp no longer appears on the pruned names. `dj-keypress` is also
  deprecated in the DOM in favour of `keydown`; wiring `dj-mouseenter` /
  `dj-mouseleave` would be a feature addition and remains an open decision.
  The same false promise was removed from two system-check regexes
  (`djust.T001` no longer suggests `dj-mouseenter=` as the migration target
  for `@mouseenter=`; `djust.T012` no longer counts it as an event
  directive). A new invariant test — new cases in
  `tests/unit/test_live_render_event_attrs_invariant.py` — fails whenever a
  stamp-list entry has no client-side binding: each entry must appear as a
  quoted string literal in some `static/djust/src/` module, or belong to the
  `dj-window-`/`dj-document-` scoped cross product derived from the client's
  own `scopedPrefixes` × `scopedEventTypes` arrays. The test caught the
  `dj-viewport-enter`/`dj-viewport-leave` pair on its first run. Its blind
  spots (a quoted mention inside a comment, a dead string reference that
  never dispatches, dynamic names outside the scoped path) are documented in
  the module docstring.
