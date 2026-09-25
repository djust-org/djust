- **With `worker_threads` on, per-frame work moves off the asyncio event loop
  (#3074).** Once sessions render on several threads, the event loop is the
  next ceiling.
  - The pre-event assigns snapshot runs in the same worker hop as a sync
    handler.
  - A server push on a legacy-exposure view is one hop: Django's
    `close_old_connections`, every push's state and hook, the render and the
    diff. Before, each hook took its own hop, the render took one, and so did
    Channels' connection check.
  - On the loop, the Rust patch JSON is spliced into the frame instead of being
    parsed and re-serialised, unless the frame carries anything else: binary
    mode, the DEBUG payload, parameter contracts or a signed snapshot.
  - `dispatch` skips Channels' per-message `aclose_old_connections` hop for
    `server_push`, because both push-turn paths run the check themselves.

  With the pool off, nothing changes. The frames are the same JSON object on
  both paths.

  5 regression tests (9 cases, each run with the pool on and off) in
  `python/djust/tests/test_event_loop_offload_3074.py`.
