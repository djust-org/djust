- **Less CPU per LiveView mount and event, so more pages fit on the one sync
  thread.** A process runs every WebSocket session's sync work on one
  thread, so 40 pages mounting at once wait for 40 pages' CPU. Profiling that
  case (`scripts/bench_concurrent_liveviews.py`, new) found the time in
  pure-Python walks, not thread hops. The change-detection fingerprint
  (`change_detection._walk`) now handles plain `dict` / `list` / `tuple`
  nodes and scalar leaves without a call per leaf (same fingerprints, node for
  node). Extracting a rendered page's root masks the page once instead of
  twice, and the tag scanner skips character runs with compiled patterns. The
  mount frame's `@cache` / `@debounce` / `@throttle` scan no longer reads
  properties, so it stops fingerprinting the whole state four times per mount
  through `changed_fields` / `is_dirty`. 40 pages of 300 rows in one process:
  mount p50 1.85 s → 1.13 s, mount CPU −39 %, one page alone 45 → 26 ms;
  event CPU −8 %. 9 regression tests in
  `python/djust/tests/test_concurrent_liveview_cpu.py`.
