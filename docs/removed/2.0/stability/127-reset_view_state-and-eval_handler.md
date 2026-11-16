### `reset_view_state` and `eval_handler` → nothing

The observability endpoints `reset_view_state/` and `eval_handler/`, their
dry-run helper `djust.observability.dry_run` (`DryRunContext`,
`DryRunViolation`) and the MCP tools `eval_handler` and `reset_view_state`
were removed in djust `2.0.0`. They worked only on views that state
`exposure_policy = "legacy"`; every other view got `409`, so they leave with
the legacy policy. The read-only endpoints and their MCP tools stay.
