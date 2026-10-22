- **With `worker_threads` on, sockets of one session on one page no longer race
  on a shared Rust view.** The in-memory state backend stored the socket's own
  live `RustLiveView` under the page's key, and every other socket of the
  session cloned that object while its owner rendered or tore it down on another
  pool thread: `round-trip failed … Already mutably borrowed`, `set_raw_py_values
  failed during teardown: Already borrowed`, and failed mount renders. The
  backend now stores a serialized snapshot (as the Redis backend does) and
  refreshes it after each render on the owner's thread
  (`StateBackend.refresh`, a no-op for remote backends), so the next request of
  the page still diffs against the last render and no Rust object is shared.
  Measured with 40 sockets of one session on one page: borrow errors 156 → 0 and
  failed mounts 25 → 0 on 3.14t with 8 workers, with the pool's speed-up kept
  (mount p50 0.36 s vs 0.76 s without the pool). 6 cases in
  `python/djust/tests/test_pool_shared_session_view.py`.
