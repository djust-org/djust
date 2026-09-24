- **Presence metadata is application output; explicit views no longer get the
  username injected (ADR-038 D-c).** `track_presence` filled in the
  authenticated user's `name` (username) and `user_id`, which peers read through
  `list_presences()` and which `LiveCursorMixin` rebroadcasts on every cursor
  move. For nonlegacy views only the meta the application passes is tracked;
  legacy views are unchanged. The `track_presence` and `update_cursor_position`
  docstrings now say that meta is shown to peers. 1 regression test (2 cases) in
  `python/djust/tests/test_exposure_presence_meta.py`.
