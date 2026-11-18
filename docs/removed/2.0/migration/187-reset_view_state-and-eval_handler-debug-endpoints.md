---
table: python
removed: The observability endpoints `reset_view_state/` and `eval_handler/`, `djust.observability.dry_run` (`DryRunContext`, `DryRunViolation`), and the MCP tools `eval_handler` and `reset_view_state`
use: Nothing replaces them. They worked only on views that state `exposure_policy = "legacy"` (every other view got `409`), so they leave with the legacy policy. Drive a handler through the live page, or through `LiveViewTestClient.send_event()` in a test. The read-only endpoints (`view_assigns/`, `last_traceback/`, `log/`, `handler_timings/`, `turn_timings/`, `sql_queries/`) and their MCP tools stay.
---
