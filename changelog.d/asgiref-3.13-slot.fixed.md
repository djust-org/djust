- **The worker pools work with asgiref 3.13.** asgiref 3.13.0's
  `SyncToAsync.__call__` reads `_exited` on the thread-sensitive context, and
  djust's pool slots (`worker_pool._Slot`, `_HTTPSlot`, set as that context by
  `worker_threads` and `PooledHTTP`) had no such attribute, so every
  `sync_to_async` of a pooled session raised `AttributeError`. A slot never
  exits while the pool lives: `_exited` is a class attribute that is always
  False. 3 regression cases in
  `python/djust/tests/test_worker_pool_asgiref_contract.py`.
