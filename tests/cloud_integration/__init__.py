"""Real-cloud integration tests for upload writers (#963).

Each test in this package requires real cloud credentials and is
auto-skipped when they're not present in the environment. The
`DJUST_CLOUD_INTEGRATION` env var (set by the weekly-cloud-uploads
workflow) names the provider being exercised; tests check for it
and for the provider-specific credentials before running.

Never run these in regular CI — they cost real money (a few cents
per weekly run) and writer failures are not PR-blocking events.
"""
