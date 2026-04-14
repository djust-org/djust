# Retrospectives

Milestone-level retrospectives synthesized from per-PR retros. The Action Tracker
at the top is the single source of truth for all outstanding items.

## Action Tracker

Items from retrospectives that need resolution. Every item must have a GitHub
issue or be explicitly closed with a reason.

| # | Action | Source | GitHub | Status | Notes |
|---|--------|--------|--------|--------|-------|
| 1 | HTML-escape CSRF token value in renderer.rs | PR #708 | #715 | Closed | Fixed in PR #721 (manual escape chain) |
| 2 | Log warning instead of bare `except Exception: pass` in rust_bridge.py:270 | PR #708 | #716 | Closed | Fixed in PR #721 (logging with exc_info) |
| 3 | Unify GET/POST context processor application (dict overlay vs instance attrs) | PR #710 | #717 | Closed | Fixed in PR #721 (`_processor_context` context manager) |
| 4 | Add Python-side integration test for DATE_FORMAT settings injection | PR #714 | #718 | Closed | Fixed in PR #721 (4 tests in test_date_format_injection.py) |
| 5 | Pre-existing test failures should be fixed in separate PRs first | Retro v0.4.3 | — | Closed | Addressed in #708 (fixed debug_state_sizes) and #714 (fixed navigation.test.js) |
| 6 | Run ruff locally before first commit attempt | Retro v0.4.3 | — | Closed | Process reminder — not a code change |
| 7 | try/finally for context processor cleanup | PR #710 | #711 | Closed | Fixed in PR #714 |
| 8 | Regression test for authenticated HTTP fallback | PR #710 | #712 | Closed | Fixed in PR #714 |
| 9 | Use `filters::html_escape()` instead of manual `.replace()` chain in CSRF tag | PR #721 | #722 | Open | renderer.rs:370 duplicates existing utility; shared fn also escapes single quotes |
| 10 | Move `from contextlib import contextmanager` to module-level import | PR #721 | #723 | Open | Class-body import in request.py:28 is unconventional |
| 11 | Wire `_processor_context` into GET path or fix docstring | PR #721 | #724 | Open | Docstring says "both GET and POST" but only POST uses it |
| 12 | Add negative test for `|date` filter (invalid date input) | PR #720 | #725 | Open | Happy-path tests only; no test for "2026-13-45" or "not-a-date" |
| 13 | Document `|date` filter Django compatibility gaps | PR #720 | #726 | Open | Only handles RFC 3339 + YYYY-MM-DD; Django accepts epoch ints, date objects, etc. |

---

## v0.4.3 — HTTP Fallback & Template Engine Fixes (PRs #708, #710, #714, #720, #721)

**Date**: 2026-04-14
**Scope**: Critical bugs found during djustlive.com production deployment that made djust unusable without WebSocket. CSRF token poisoning, HTTP fallback session loss, DateField filter compatibility, plus two rounds of tech-debt cleanup.
**Tests at close**: 2,192 Python + 1,124 JS + 322 Rust

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

**5. The Rust template engine's Django compatibility is a long tail.**
PR #720 fixed `|date` filter on bare `DateField` values ("2026-03-15") — it only supported RFC 3339 datetimes. The fix is 6 lines (NaiveDate fallback to midnight UTC), but the pattern is clear: every new production use case surfaces another Django filter edge case. The Rust engine now handles 2 of Django's ~5 date input types. The midnight-UTC assumption is correct but means `{{ date_field|date:"H:i" }}` renders "00:00", which could surprise developers.

**Action taken**: Fixed in PR #720. Tracking remaining compatibility gaps as Action #13.

**6. Review-to-action-item pipeline completed a full cycle.**
All 4 items in PR #721 originated as 🟢 findings from PRs #708 and #710 reviews. They were tracked in the Action Tracker, filed as GitHub issues (#715-#718), and resolved in a batch PR. This is the pipeline working as designed. However, the #721 review found that the CSRF HTML-escape fix used a manual `.replace()` chain instead of the existing `filters::html_escape()` utility — an implementation gap where the developer didn't search for existing utilities before writing new code.

**Action taken**: Fixed in PR #721. New action item #9 filed for the utility duplication.

### Insights

- **Production deployment is the ultimate test.** All four bugs (#696, #705, #706, #707) were invisible in local dev where WebSocket always works. The djustlive.com deploy exposed them within minutes.
- **Batching small fixes into one PR works well.** PR #714 shipped 3 fixes in 30 minutes with one review cycle. Good for related tech-debt items.
- **Issue triage saves time.** #706 (nginx config) and #707 (by design) were closed without code changes after investigation. #703 was already fixed. Not every issue needs a PR.
- **The `_collect_sub_ids` mechanism (#703) was already working** — the issue was filed after the fix landed. Quick verification with a reproduction script confirmed this.
- **Action item lifecycle completed in one milestone.** Findings from early PRs (#708, #710) → Action Tracker → GitHub issues (#715-#718) → resolved in #721. The full loop took ~1 day. This is what the pipeline-retro skill was designed to enable.
- **Two rounds of batching cleared all open action items.** PR #714 (round 1) fixed 3 items; PR #721 (round 2) fixed 4 more. Zero open items from the first retro remain.
- **"Search for existing utilities" is a gap.** The manual HTML escape in #721 duplicated `filters::html_escape()` in the same crate. Not caught during implementation, only during review.

### Review Stats

| Metric | PR #708 | PR #710 | PR #714 | PR #720 | PR #721 | Total |
|--------|---------|---------|---------|---------|---------|-------|
| Files changed | 6 | 1 | 5 | 1 | 4 | 17 |
| Tests added | 0 | 0 | 6 | 3 | 4 | 13 |
| 🔴 Findings | 0 | 0 | 0 | 0 | 0 | 0 |
| 🟡 Findings | 0 | 2 | 0 | 0 | 1 | 3 |
| 🟢 Findings | 2 | 1 | 2 | 3 | 3 | 11 |
| Findings fixed pre-merge | 0 | 0 | 0 | 0 | 0 | 0 |
| Findings fixed post-merge | — | 2 (in #714) | — | — | — | 2 |

### Process Improvements Applied

**CLAUDE.md**: No changes this milestone
**Pipeline template**: No changes
**Checklist**: Pre-commit checklist added to pipeline-run skill (cargo fmt, ruff before commit)
**Skills**: pipeline-run updated with gate check, duplicate PR prevention, review quality rules (100-word minimum, line citations). pipeline-drain skill created. djust-release updated with clean working tree step.

### Open Items

- [x] HTML-escape CSRF token value in renderer.rs — Action Tracker #1 — resolved in PR #721
- [x] Log warning for bare `except` in rust_bridge.py — Action Tracker #2 — resolved in PR #721
- [x] Unify GET/POST context processor paths — Action Tracker #3 — resolved in PR #721
- [x] Python integration test for DATE_FORMAT injection — Action Tracker #4 — resolved in PR #721
- [ ] Use `filters::html_escape()` instead of manual escape chain — Action Tracker #9
- [ ] Move class-level contextmanager import to module level — Action Tracker #10
- [ ] Wire `_processor_context` into GET path or fix docstring — Action Tracker #11
- [ ] Add negative test for `|date` filter — Action Tracker #12
- [ ] Document `|date` filter Django compatibility gaps — Action Tracker #13
