- **The PWA sync endpoint's per-action errors carry no exception text.** The
  create, update and delete batch helpers put `str(e)` into the errors that
  `sync_endpoint_view` returns as JSON. They now report the exception class,
  as `_perform_sync` already does (#2950). This is a plain Django endpoint,
  so it applies to every caller.
