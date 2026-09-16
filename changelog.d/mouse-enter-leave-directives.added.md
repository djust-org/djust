- **`dj-mouseenter` / `dj-mouseleave` are now real directives (#2869).** The
  attributes were previously stamp-listed by `{% live_render %}` — the
  framework asserted they existed — but the client never bound them, so a
  developer writing `<div dj-mouseenter="highlight">` got a stamped
  attribute, no listener, and no warning. They are now wired as first-class
  event directives: `mouseenter`/`mouseleave` do not bubble, so the client
  attaches listeners DIRECTLY to the declaring element through the existing
  scoped-listener machinery instead of the delegated root-level shape
  `dj-click` uses. Nesting semantics are the platform's own: moving the
  pointer from an element into one of its children fires neither the
  element's `dj-mouseleave` nor a second `dj-mouseenter` (entering a child
  is not leaving the parent — the reason these event types exist over
  `mouseover`/`mouseout`). Bind-pass safety follows the house rules: the
  #2845 skip-on-unchanged-value / evict-and-rebuild rule prevents
  double-attach across morphs, the #2832 sweep detaches listeners when the
  template stops declaring the attribute, and the handler closure re-reads
  the attribute at fire time (#2858) so a value change under a surviving
  element is honoured. `dj-debounce` / `dj-throttle`, `dj-confirm`,
  `data-*` params, inline handler args, and `{% live_render %}` embedded
  routing (`view_id` from the stamp) all work as on sibling directives.
  Documented in `docs/website/core-concepts/events.md` ("Mouse Enter /
  Leave") and the template cheatsheet quick-reference card.
