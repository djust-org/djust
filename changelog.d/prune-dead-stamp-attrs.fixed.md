- **`dj-keypress`, `dj-viewport-enter`, and `dj-viewport-leave` removed from
  the `{% live_render %}` stamp list (#2869).** They sat in
  `_LIVE_RENDER_EVENT_ATTRS` — the list `{% live_render %}` scans when
  stamping `view_id` onto embedded elements — but no client module ever
  bound them: zero mentions in `static/djust/src/`, zero occurrences in the
  built client. An element carrying one of them inside an embedded child was
  stamped exactly like a working directive while no listener was ever
  installed — no error, no warning, no event. `dj-keypress` is also
  deprecated in the DOM in favour of `keydown` (which djust ships with a
  full modifier system); there is no `viewportenter` DOM event for the
  viewport pair to bind at all. `dj-keypress`, `dj-viewport-enter`, and
  `dj-viewport-leave` were all introduced in the same v0.6.0 stamp-list
  commit (`e9907c7f`) — the invariant test below caught the viewport pair on
  its first run. A new invariant test — new cases in
  `tests/unit/test_live_render_event_attrs_invariant.py` — fails whenever a
  stamp-list entry has no client-side binding: each entry must appear as a
  quoted string literal in some `static/djust/src/` module, or belong to the
  `dj-window-`/`dj-document-` scoped cross product derived from the client's
  own `scopedPrefixes` × `scopedEventTypes` arrays. Its blind spots (a
  quoted mention inside a comment, a dead string reference that never
  dispatches, dynamic names outside the scoped path) are documented in the
  module docstring.
