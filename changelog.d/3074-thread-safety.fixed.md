- **Shared state that sessions' sync code touches is now safe when two
  threads use it at once (#3074).** This was reachable before (an HTTP
  request thread beside the WebSocket thread) and is common with
  `worker_threads`.
  - `DjangoJSONEncoder`'s recursion depth was a single counter shared by
    every thread, so one render's nesting could decide whether another's
    related objects were serialised. It is now per thread.
  - The state and presence backend registries could build two backends on
    first use and drop one's data.
  - The tenant-scoped in-memory presence backend, `CursorTracker`,
    component auto-keys and the JIT variable cache now take a lock or do a
    single lookup.

  6 regression cases in
  `python/djust/tests/test_worker_pool_thread_safety_3074.py`.
