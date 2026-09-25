- **`djust.layers.InMemoryChannelLayer`: an in-process channel layer whose
  expiry sweep is rate-limited (#3074).**
  - **The problem.** Channels' `InMemoryChannelLayer` walks every channel queue
    and every group membership on each `receive()` and `group_send()`. A
    broadcast round across N sessions therefore costs O(N²) on the event loop:
    17.7 ms per round at 224 sessions in rooms of 4, and 78 ms at 512. In the
    #3074 snake profile it was 11.9 % of the event-loop thread.
  - **The fix.** djust's subclass sweeps at most once per `clean_interval`
    (default 1 s; `0` restores Channels' behaviour): 4.4 ms and 10.3 ms per
    round.
  - **How to use it.** It is opt-in: set `"BACKEND":
    "djust.layers.InMemoryChannelLayer"`. It is only for single-process
    deployments, which with free-threaded Python and `worker_threads` can use
    several cores. Multi-process deployments still need `channels_redis`.
  - **What changes.** An expired message or membership is removed up to
    `clean_interval` seconds later.

  8 regression cases in `python/djust/tests/test_inmemory_layer_3074.py`.
