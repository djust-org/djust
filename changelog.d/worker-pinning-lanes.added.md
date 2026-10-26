- **`LIVEVIEW_CONFIG["worker_pinning"] = False`: worker pool lanes instead of pinned threads.** With
  `worker_threads` on, each WebSocket session is pinned to one pool thread, so a slow hop of one session (a long
  handler, a heavy mount, a `start_async` callback) holds up every session pinned to the same thread while other
  threads may be idle. Opt in to `worker_pinning: False` and each session gets a lane instead: its thread-sensitive
  calls still run one at a time and in order, on its home thread while that thread is free, and on any idle thread
  of the pool when it is not; a lane goes to the back of its queue after each call, so sessions take turns. The
  default (`True`) is unchanged. `djust.C021` also reports a `worker_pinning` that is not a bool. 17 regression cases
  in `python/djust/tests/test_worker_lanes.py`.
