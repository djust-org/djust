- **Less work on the asyncio event loop per WebSocket frame (#3095).** On
  free-threaded CPython with `LIVEVIEW_CONFIG["worker_threads"]` the event
  loop, not the cores, caps one process. A profile of a multi-room game at
  saturation showed the loop spending about a third of its time on events
  that mostly skip the render. Changes that apply everywhere:
  - no thread hop for the handler-permission check when the handler has no
    `@permission_required`, nor for the object-permission check when the view
    does not override `get_object`; both are metadata checks then;
  - a handler's `inspect.signature` and type hints are resolved once per
    function (a failed type-hint resolution is retried, as before);
  - a free render lock is taken without arming an `asyncio.wait_for` timer;
  - `djust.layers.InMemoryChannelLayer.group_send` delivers without creating
    a task per member.

  With the worker pool on only: Channels' per-frame `close_old_connections`
  hop is replaced by a check the session's pool thread runs before its next
  task, and a tick's change-detection snapshots run in the `handle_tick` hop.
  20 regression tests in
  `python/djust/tests/test_event_loop_ceiling_3095.py`.
