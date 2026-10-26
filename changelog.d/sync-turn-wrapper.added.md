- **`LIVEVIEW_CONFIG["sync_turn_wrapper"]`: a scope around each sync step of a
  turn.** A socket turn runs a view's sync code in separate hops with awaits
  between them, so a `LIVEVIEW_MIDDLEWARE` could not hold a database
  transaction for it (with `worker_threads`, other sessions' hops run on the
  same thread in the gaps). The wrapper, `wrapper(view, step, turn)` returning a
  context manager, is entered on the thread around each sync event handler,
  `mount`, `handle_params`, other sync hook, `start_async` callback and every
  framework call of `get_context_data()` (`djust.sync_turn`); a nested step
  does not enter it again. See the turn middleware guide. 8 regression cases
  in `python/djust/tests/test_sync_turn_wrapper.py`.
