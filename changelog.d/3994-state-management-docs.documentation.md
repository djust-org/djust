- **The state-management pages teach djust 2.0 (#3994).** `STATE_MANAGEMENT_API`, `_ARCHITECTURE`,
  `_COMPARISON`, `_MIGRATION`, `_QUICKSTART`, `_PATTERNS`, `_EXAMPLES`, `_TUTORIAL` and the phase-1
  and phase-5 records no longer teach `@optimistic` / `@client_state` (removed by #4006): instant
  feedback is `dj-loading.*` attributes or a JS Command chain, and two values kept in step are set
  by one handler. `IMPLEMENTATION_PHASE3.md`, which only described `@optimistic`, is deleted. All
  eleven snippets that were pinned as failing in `tests/test_doc_snippets_execute.py` now run.
