- **A legacy session save keeps the view's state, not the whole render
  context.** The HTTP GET, the page-POST fallback, a socket turn's save and a
  lazy child's save kept every key `get_context_data()` returned except the
  render-only ones, so a key an override added on top of `super()` was saved
  too: a QuerySet as a list of dicts with every column of each row, content a
  view never meant to persist. They now keep only what the base
  `get_context_data()` built from the view's attributes
  (`djust.mixins.context.legacy_unsaved_keys`, one filter for all four sites).
  A key an override adds is computed again on every render, and is no longer
  set back on the view as an attribute after a restore. A view whose override
  never calls `super()` saves as before.
