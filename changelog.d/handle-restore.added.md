- **`LiveView.handle_restore(request)`: re-read what the session does not hold
  after a restore.** The HTTP page-POST fallback rebuilds a view from its saved
  session state and either does not call `mount()` or calls it before the saved
  state is put back, so `mount()` never saw the restored ids and rows kept off
  the session were never read again (a page stayed "loading" over the fallback).
  The hook runs after the restore and the object permission check, before the
  handler, and after a socket mount restored from the session. 4 regression
  cases in `python/djust/tests/test_handle_restore.py`.
