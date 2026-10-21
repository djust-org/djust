- **A `dj-poll` inside a `{% live_render %}` child view now reaches that child.**
  The poll was sent without the child's `view_id`, so the parent view received
  it (and usually had no such handler). A poll inside a component still reaches
  its host view. Cases in `tests/js/poll_root_and_child.test.js`.
