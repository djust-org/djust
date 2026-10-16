- **A view is cleaned up when a late mount step raises.** The steps of a live
  mount outside `mount()`, `handle_params()`, `connected()` and the first render
  (the session-state and signed-snapshot restore, the route binding, the
  dirty-tracking baseline, the post-render hooks) could raise with the view
  still set on the runtime, in its channel groups and listed in presence. Any
  exception after the view is admitted now leaves no view mounted: its groups
  are left, its presence is untracked and its `disconnected()` runs. The
  exception and its error frame are unchanged. Regression cases in
  `python/djust/tests/test_failed_mount_leaves_no_view.py`.
