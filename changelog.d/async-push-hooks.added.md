- **Server-push handlers and `handle_info()` may be `async def`.** They are
  awaited on the event loop; a sync handler still runs off the loop. Before, an
  async push handler raised `TypeError` on the stock path and, with
  `worker_threads` on, returned a coroutine that never ran. A push turn with an
  async handler keeps the stock path instead of moving to the worker thread.
  7 cases in `python/djust/tests/test_async_push_hooks.py`, with the worker
  pool off and on.
