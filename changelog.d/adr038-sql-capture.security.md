- **Observability SQL capture redacts query parameters for explicit views
  (ADR-038 D-d).** The DEBUG-only `/_djust/observability/sql_queries/` endpoint
  served each captured query's raw parameters, which are often derived from view
  state. `capture_for_event` now takes the owning view (`owner=`, passed by the
  WebSocket event turn), and parameters are recorded as `"[redacted]"`
  placeholders when that owner is nonlegacy or the active diagnostic scope is
  restricted. SQL text, tags and timing are kept; legacy views are unchanged.
  3 regression tests (6 cases) in
  `python/djust/tests/test_exposure_sql_capture.py`.
