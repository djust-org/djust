- **PWA offline-sync errors no longer carry exception text for explicit views
  (ADR-038).** `SyncMixin` sent `str(exc)` to the client in the
  `offline:sync_error` push event and stored it in the sync queue through
  `mark_failed`. For nonlegacy views the push event now carries
  `"Offline sync failed"` and the queue stores the exception class name; legacy
  views are unchanged. Separately, the ownerless `sync_endpoint_view` now returns
  `Batch sync error: <ExceptionClass>` instead of the exception text **for every
  caller**, including legacy apps: it is a plain Django endpoint with no view to
  read a policy from. `IndexedDBStorage`'s docstring now says what it is:
  in-process server memory, not browser IndexedDB. 2 regression tests (5 cases) in
  `python/djust/tests/test_exposure_pwa_sync_sinks.py`.
