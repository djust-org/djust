- **BREAKING: the legacy Rust render cache.** A legacy-exposure view's
  compiled `RustLiveView` (template, render state and VDOM baseline) is no
  longer stored in the state backend under the session and page and reused by
  the next request or reconnect; each view instance builds its own, as an
  explicit view always did. `DJUST_CONFIG['STATE_BACKEND']` still backs the
  interactive-component visibility ledger and `djust clear`. The private
  helpers `_refresh_cached_rust_view`, `_saved_state_key` and
  `_get_cached_template_hash_slot` are gone.
