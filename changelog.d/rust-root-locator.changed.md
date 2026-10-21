- **Extracting a rendered page's LiveView root uses the Rust scanner.**
  `_extract_liveview_content` runs on every WebSocket mount and full render,
  and masked the whole page in Python first: on a 300-row page with `dj-id` on
  every element that was 11 ms, about half of the mount's CPU, all on the
  process's one sync thread. The new `djust._rust.root_content_range` wraps the
  existing Rust twin of the locator (`find_dj_root_content_range`, pinned to the
  same #3031 root rule) and takes 0.4 ms; it is now the only locator of a
  rendered page's root (see the html5ever-aligned fixes under Fixed). 40 pages
  of 300 rows in one process: mount p50 881 → 431 ms, one page
  alone 23 → 11 ms. Covered in `python/djust/tests/test_concurrent_liveview_cpu.py`.
