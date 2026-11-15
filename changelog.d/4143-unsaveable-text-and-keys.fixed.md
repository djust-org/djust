- **The automatic state save no longer raises on a lone surrogate or a non-string `__dict__` key
  (#4143).** A `str` holding a lone surrogate (`"\ud800"`), as a value or a dictionary key at any
  depth, encoded as JSON but not as UTF-8, so the save raised `UnicodeEncodeError` and the turn
  failed. It is now left out like any other value the save cannot hold, on the WebSocket, SSE and
  the HTTP fallback alike: the response is served, one warning names the view and the field (never
  the value), and the next request runs `mount()` again; a private or component value is skipped
  quietly, as before. Saved bytes for every other value are unchanged. A key in a view's
  `__dict__` that is not a `str` is not an attribute name: the save, change detection, handler
  discovery, the legacy context and snapshots, time-travel restore, component ids and the
  observability reset now skip it instead of raising `AttributeError`. CI's benchmarks job also
  runs the #3997 turn-cost table's 3-round smoke form, so its check that every exposure setting
  answers with legacy's frames executes (`--benchmark-only` skipped it). New cases in
  `test_automatic_state_unsaveable_text_4143.py`,
  `test_reset_skips_a_dict_key_that_is_not_a_string` and
  `test_the_turn_cost_smoke_runs_without_benchmark_only`.
