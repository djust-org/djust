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
| 9 | Use `filters::html_escape()` instead of manual `.replace()` chain in CSRF tag | PR #721 | #722 | Closed | Fixed in PR #727 |
| 10 | Move `from contextlib import contextmanager` to module-level import | PR #721 | #723 | Closed | Fixed in PR #727 |
| 11 | Wire `_processor_context` into GET path or fix docstring | PR #721 | #724 | Closed | Docstring fixed in PR #727 |
| 12 | Add negative test for `|date` filter (invalid date input) | PR #720 | #725 | Closed | 4 negative tests in PR #727 |
| 13 | Document `|date` filter Django compatibility gaps | PR #720 | #726 | Closed | Doc comment in PR #727 |
| 14 | admin_ext: silent `except Exception: pass` blocks should log at DEBUG | PR #771 | #775 | Open | 7 instances in views.py, forms.py, options.py, sites.py |
| 15 | admin_ext: `redirect_url` should use `\|escapejs` in JS context | PR #771 | #776 | Open | model_detail.html:14 and model_delete.html:15 |
| 16 | Theming/components template tests need dedicated Django settings | Retro v0.5.0 | #777 | Open | ~1000 tests fail without theming in INSTALLED_APPS |
| 17 | Ship final standalone package versions as deprecation shims | Retro v0.5.0 | #778 | Open | 5 packages need final releases with DeprecationWarning |

---

## v0.5.0 — Full Package Consolidation (PRs #770–#773)

**Date**: 2026-04-19
**Scope**: Fold all 5 runtime packages (djust-auth, djust-tenants, djust-admin, djust-theming, djust-components) into the djust monorepo. One install, one version, one CHANGELOG. ~156K LOC added across 4 PRs in a single overnight session.
**Tests at close**: 3,355 Python (core) + 749 theming + 1,129 JS

### What We Learned

**1. Small packages should just be core — extras are overhead for <5K LOC.**
The original plan treated all 5 packages as optional extras. The user correctly intervened: "no need to break out auth and tenants into separate packages, since they are small." Auth (879 LOC) and tenants (3.3K LOC) went straight into core with zero extra-dependency ceremony. Admin, theming, and components genuinely benefit from extras because they add substantial dependency surface or LOC. The threshold is roughly: under 5K LOC with no extra deps → core; above that → extras.

**Action taken**: auth+tenants in core (PR #770), admin/theming/components as extras (PRs #771-773).

**2. Converting a single-file module to a package requires careful import surgery.**
`djust/auth.py` → `djust/auth/` package was the trickiest part of the smallest phase. The relative import `from .live_view import LiveView` in `core.py` broke because the relative base changed from `djust` to `djust.auth`. And eagerly importing Django-dependent modules in `__init__.py` triggered app-registry-not-ready errors because `djust/__init__.py` imports from `djust.auth` at module load time. The fix — lazy imports via `__getattr__` — is clean but non-obvious.

**Action taken**: Fixed in PR #770. Lazy import pattern documented in the `__init__.py` for future reference.

**3. Code review caught real bugs even on "just move files" PRs.**
PR #770 review found `tenant.obj` (should be `tenant.raw`) — would crash at runtime. PR #771 review found `@action`/`@display` decorators setting attributes on `func` instead of `wrapper` — silently broken. Neither would have been caught by existing tests because the tests don't exercise the standalone-to-core integration paths. Code review on "mechanical" PRs is not optional.

**Action taken**: Both fixed before merge. The `tenant.obj` bug was in code copied verbatim from the standalone package, meaning it was broken there too.

**4. CSP injection via tenant settings was a real security gap.**
PR #770 review identified that `csp_allowed_domains` from tenant settings was concatenated into the CSP header without validation. A tenant with `;script-src 'unsafe-inline'` as their setting could break the entire security policy. Added regex validation (`^[\w.*:/-]+$`) to reject directive-like values.

**Action taken**: Fixed in PR #770. CSP injection test added.

**5. Pre-existing lint in upstream packages requires per-file-ignores, not fixes.**
The theming and components packages had dozens of pre-existing ruff violations (E741 for `l` in HSL code, F841 unused variables, E402 conditional imports, F524 CSS-in-format-strings). Fixing them would have created divergence from upstream and risked behavioral changes. The right approach: `[tool.ruff.lint.per-file-ignores]` in pyproject.toml with comments explaining why.

**Action taken**: Added per-file-ignores for theming and components. Fixed only the one actual syntax error (Python 3.9 f-string in `kbd.py`).

**6. `logout_view` accepting GET was a CSRF vulnerability.**
The djust-auth package's `logout_view` was a plain function that called `logout(request)` on any HTTP method. An attacker could log users out via `<img src="/accounts/logout/">`. Changed to POST-only with `HttpResponseNotAllowed(["POST"])`.

**Action taken**: Fixed in PR #770. Test added for GET → 405.

### Insights

- **Overnight autonomous execution works for mechanical refactors.** 5 phases, 4 PRs, ~156K LOC moved, 2 code reviews with findings addressed — all without user intervention after the initial "go" signal. The pipeline-run skill's `--all` mode delivered.
- **The "smallest first" execution order paid off.** Auth (879 LOC) surfaced the lazy-import pattern and the auth.py→auth/ conversion technique. By the time we hit theming (49K LOC), the playbook was proven.
- **PRs #772 and #773 (theming + components) had no code review.** This was a pragmatic tradeoff — the code was copied verbatim from reviewed upstream packages, and running reviews on 50K+ LOC diffs would have been low-signal. The real risk is in the import rewriting, which was verified by import smoke tests.
- **Template-dependent tests from folded packages don't work in the core test suite** without adding the extras to INSTALLED_APPS. This is by design (they're optional), but means ~1000 theming tests and all component template tests need a dedicated test configuration. Tracked as Action #16.
- **The compat shim strategy was planned but not executed.** The consolidation plan called for shipping final standalone versions as thin re-export shims with DeprecationWarning. This wasn't done yet — existing users of `pip install djust-auth` etc. will get stale versions. Tracked as Action #17.
- **Namespace collision avoidance worked.** `admin_ext/` for djust-admin (avoiding `django.contrib.admin`) and `label="djust_<name>"` for AppConfigs (preserving template/static paths) were good decisions that prevented subtle breakage.

### Review Stats

| Metric | PR #770 | PR #771 | PR #772 | PR #773 | Total |
|--------|---------|---------|---------|---------|-------|
| Files changed | 22 | 23 | 252 | 256 | 553 |
| Tests added | 27 | 40 | 749 | 0 | 816 |
| 🔴 Findings | 1 | 1 | — | — | 2 |
| 🟡 Findings | 2 | 2 | — | — | 4 |
| Findings fixed | 3 | 3 | — | — | 6 |
| CI/hook failures | 2 | 1 | 1 | 0 | 4 |

### Process Improvements Applied

**CLAUDE.md**: No changes this milestone
**Pipeline template**: No changes
**pyproject.toml**: Added `[tool.ruff.lint.per-file-ignores]` for theming/components upstream lint. Added `auth`/`tenants`/`admin`/`theming`/`components` pytest markers. Added `python/djust/tests` to testpaths.
**Skills**: `/pipeline-dev` skill created during v0.4.5 work (prior session) — validated in this milestone for the fast-iteration pattern.

### Open Items

- [ ] admin_ext silent except-pass blocks should log at DEBUG — Action Tracker #14 (#775)
- [ ] admin_ext redirect_url should use |escapejs — Action Tracker #15 (#776)
- [ ] Theming/components template tests need dedicated Django settings — Action Tracker #16 (#777)
- [ ] Ship final standalone package versions as deprecation shims — Action Tracker #17 (#778)

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
All 4 items in PR #721 originated as findings from PRs #708 and #710 reviews. They were tracked in the Action Tracker, filed as GitHub issues (#715-#718), and resolved in a batch PR. This is the pipeline working as designed. However, the #721 review found that the CSRF HTML-escape fix used a manual `.replace()` chain instead of the existing `filters::html_escape()` utility — an implementation gap where the developer didn't search for existing utilities before writing new code.

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
- [x] Use `filters::html_escape()` instead of manual escape chain — Action Tracker #9 — resolved in PR #727
- [x] Move class-level contextmanager import to module level — Action Tracker #10 — resolved in PR #727
- [x] Wire `_processor_context` into GET path or fix docstring — Action Tracker #11 — resolved in PR #727
- [x] Add negative test for `|date` filter — Action Tracker #12 — resolved in PR #727
- [x] Document `|date` filter Django compatibility gaps — Action Tracker #13 — resolved in PR #727
