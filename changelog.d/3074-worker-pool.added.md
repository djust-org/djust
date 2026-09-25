- **Opt-in pinned session worker pool: `LIVEVIEW_CONFIG["worker_threads"]`
  (#3074).** By default every WebSocket session's sync work (mount, handlers,
  hooks, renders) runs on asgiref's one thread shared by the whole process.
  Set `worker_threads` to `True` (one thread per CPU, up to 32) or an integer,
  and each session is pinned to one thread of a pool for its lifetime, while
  different sessions run at the same time. The mechanism is asgiref's
  `SyncToAsync.thread_sensitive_context`, so every thread-sensitive
  `sync_to_async` a session makes, djust's, Channels' and the app's, lands on
  its thread. HTTP and SSE are unchanged. The default (`None`) keeps today's
  behaviour, and `djust.C021` reports an invalid value. See "More than one
  core per process" in the deployment guide. 9 regression cases in
  `python/djust/tests/test_worker_pool_3074.py`.
