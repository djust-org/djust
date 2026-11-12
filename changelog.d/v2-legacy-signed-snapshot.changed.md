- **`DJUST_STATE_SNAPSHOT_MAX_AGE` is read in one place.** The signed client
  snapshot of `persist="client"` fields and the service worker's
  `state_snapshot_max_age` now use the same validated value
  (`djust._exposure_snapshots.get_max_age`, still importable from
  `djust.security`). A whole number of seconds from 1 to 86400 is used as
  given, including a string of digits from an environment variable. A larger
  value is capped at 86400, and anything else falls back to 3600; each is
  logged once. Before, a value above 86400 or a string turned the explicit
  snapshot off without a word while the service worker went on using it.
