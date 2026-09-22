- **Redact offline-sync handler failures for staged explicit exposure.** When a
  view's `sync_create/update/delete_<model>` handler (or the default sync)
  failed, `SyncMixin` logged the exception text, which can echo the
  client-queued data. A nonlegacy view now gets the value-free log line; legacy
  logging is unchanged.
