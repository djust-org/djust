- **`djust.V005`, `LiveViewTestClient.assert_allowlisted()` and
  `assert_all_routed_liveviews_allowlisted()` now agree with the WebSocket mount
  (#3994).** They matched `LIVEVIEW_ALLOWED_MODULES` entries with a bare string
  `startswith`, while the mount matches on a dotted-segment boundary, so an
  entry such as `"djust"` passed every check for a view in `djust_docs.views`
  that the mount then refused (the page rendered, and every event fell back to
  HTTP). All three now call `djust.security.mount.is_view_path_allowed`. The
  demo project had fourteen routed views in that state; its allowlist now
  names their modules. New cases in `python/djust/tests/test_allowlist_segment_boundary_3994.py`.
