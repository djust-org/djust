# Retrospectives

Milestone-level retrospectives synthesized from per-PR retros. The Action Tracker
at the top is the single source of truth for all outstanding items.

## Action Tracker

Items from retrospectives that need resolution. Every item must have a GitHub
issue or be explicitly closed with a reason.

| # | Action | Source | GitHub | Status | Notes |
|---|--------|--------|--------|--------|-------|
| 1 | HTML-escape CSRF token value in renderer.rs | PR #708 | #715 | Open | Defense against token values with special chars; low priority |
| 2 | Log warning instead of bare `except Exception: pass` in rust_bridge.py:270 | PR #708 | #716 | Open | Silent exception swallowing |
| 3 | Unify GET/POST context processor application (dict overlay vs instance attrs) | PR #710 | #717 | Open | Fragile asymmetry between the two code paths |
| 4 | Add Python-side integration test for DATE_FORMAT settings injection | PR #714 | #718 | Open | Rust filter works, but no test that Django settings flow through |
| 5 | Pre-existing test failures should be fixed in separate PRs first | Retro v0.4.3 | — | Closed | Addressed in #708 (fixed debug_state_sizes) and #714 (fixed navigation.test.js) |
| 6 | Run ruff locally before first commit attempt | Retro v0.4.3 | — | Closed | Process reminder — not a code change |
| 7 | try/finally for context processor cleanup | PR #710 | #711 | Closed | Fixed in PR #714 |
| 8 | Regression test for authenticated HTTP fallback | PR #710 | #712 | Closed | Fixed in PR #714 |

---

## v0.4.3 — HTTP Fallback & Template Engine Fixes (PRs #708, #710, #714)

**Date**: 2026-04-14
**Scope**: Critical bugs found during djustlive.com production deployment that made djust unusable without WebSocket. CSRF token poisoning, HTTP fallback session loss, plus tech-debt cleanup (try/finally, auth test, DATE_FORMAT).
**Tests at close**: 2,188 Python + 1,124 JS + 322 Rust

### What We Learned

**1. The Rust template engine's CSRF placeholder was a ticking time bomb.**
The `CSRF_TOKEN_NOT_PROVIDED` placeholder in `renderer.rs` existed from the initial implementation and was never a problem because WebSocket mode doesn't need CSRF. The moment djustlive.com deployed without WebSocket (django-tenants + nginx), every HTTP fallback event hit 403. The three-layer fix (Rust renders empty, Python injects real token, client.js falls through to cookie) ensures this class of problem can't recur.

**Action taken**: Fixed in PR #708. The defense-in-depth approach means any single layer can fail without breaking CSRF.

**2. The HTTP fallback path was never tested with authentication.**
The POST handler's `render_with_diff()` path skipped `_apply_context_processors()` entirely — no `user`, no `perms`, no `messages`. This was invisible in development (WebSocket always works locally) and only surfaced in production. The fix (#710) works but uses instance-attribute injection, which is a different pattern than the GET path's dict overlay.

**Action taken**: Fixed in PR #710 + #714 (try/finally + regression test). The GET/POST asymmetry remains as tech-debt (Action #3).

**3. Pre-commit hooks caught real issues but wasted significant time.**
Three commit attempts for #708 failed due to: `cargo fmt` reformatting, ruff F841 (unused variable), and pre-existing `test_debug_state_sizes` failures. The lesson: always run `cargo fmt`, `ruff check`, and the test suite locally before `git commit`.

**Action taken**: Test failures fixed as part of the PRs. Process lesson noted.

**4. Pipeline discipline matters — skipping review stages has consequences.**
The first two PRs (#708, #710) were merged without running the Code Review and Retrospective stages. The post-merge review of #710 found a real issue (missing try/finally) that should have been caught pre-merge. PR #714 followed the pipeline correctly and the review found no issues.

**Action taken**: Pipeline stages must not be skipped. The backfilled retros for #708/#710 were honest about this.

### Insights

- **Production deployment is the ultimate test.** All four bugs (#696, #705, #706, #707) were invisible in local dev where WebSocket always works. The djustlive.com deploy exposed them within minutes.
- **Batching small fixes into one PR works well.** PR #714 shipped 3 fixes in 30 minutes with one review cycle. Good for related tech-debt items.
- **Issue triage saves time.** #706 (nginx config) and #707 (by design) were closed without code changes after investigation. #703 was already fixed. Not every issue needs a PR.
- **The `_collect_sub_ids` mechanism (#703) was already working** — the issue was filed after the fix landed. Quick verification with a reproduction script confirmed this.

### Review Stats

| Metric | PR #708 | PR #710 | PR #714 | Total |
|--------|---------|---------|---------|-------|
| Files changed | 6 | 1 | 5 | 12 |
| Tests added | 0 | 0 | 6 | 6 |
| 🔴 Findings | 0 | 0 | 0 | 0 |
| 🟡 Findings | 0 | 2 | 0 | 2 |
| 🟢 Findings | 2 | 1 | 2 | 5 |
| Findings fixed pre-merge | 0 | 0 | 0 | 0 |
| Findings fixed post-merge | — | 2 (in #714) | — | 2 |

### Process Improvements Applied

**CLAUDE.md**: No changes this milestone
**Pipeline template**: No changes
**Checklist**: No changes
**Skills**: No changes
**Lesson**: First two PRs skipped review stages → found a real issue post-merge. Reinforces that the pipeline exists for a reason.

### Open Items

- [ ] HTML-escape CSRF token value in renderer.rs — Action Tracker #1
- [ ] Log warning for bare `except` in rust_bridge.py — Action Tracker #2
- [ ] Unify GET/POST context processor paths — Action Tracker #3
- [ ] Python integration test for DATE_FORMAT injection — Action Tracker #4
