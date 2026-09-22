- **The opt-in service worker no longer writes explicit-exposure pages to its
  VDOM or shell cache (ADR-038 D-b).** An `exposure_policy="explicit"` page (or
  a legacy page with an explicit child) is marked ineligible: its HTTP response
  carries `X-Djust-SW-Cache: no-store`, which the worker checks before writing
  `SHELL_CACHE`, and its mount frame carries `"sw_cache": "no-store"`, which the
  client checks before `cacheVdom`. Legacy pages cache exactly as before.
  Tests: `tests/js/exposure_sw_caches.test.js` and
  `python/djust/tests/test_exposure_sw_caches.py`.
