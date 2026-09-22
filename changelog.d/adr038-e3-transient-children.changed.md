- **Non-sticky explicit-exposure children are transient; `lazy=` is refused
  (ADR-038 D-m).** A `{% live_render %}` of an explicit child without
  `sticky=True` now mounts under the child reuse-identity check, renders without
  the raw `view` context, handles events, and is never persisted (it may not
  declare persisted fields; a pinned `view_id` keeps the instance across parent
  renders while its identity matches). Previously such a child failed the
  parent's mount with a state error. `lazy=` on an explicit child raises a
  static `TemplateSyntaxError` before any placeholder is emitted.
