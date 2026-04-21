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
| 14 | admin_ext: silent `except Exception: pass` blocks should log at DEBUG | PR #771 | #775 | Closed | Fixed in PR #781 |
| 15 | admin_ext: `redirect_url` should use `\|escapejs` in JS context | PR #771 | #776 | Closed | Fixed in PR #781 |
| 16 | Theming/components template tests need dedicated Django settings | Retro v0.5.0 | #777 | Closed | Fixed in PR #782 (demo INSTALLED_APPS) |
| 17 | Ship final standalone package versions as deprecation shims | Retro v0.5.0 | #778 | Open | 5 packages need final releases with DeprecationWarning |
| 18 | Broaden dep-extractor correctness harness matrix (Spaceless, standalone CustomTag, nested With, standalone Block, ReactComponent, RustComponent) | PR #785 | #786 | Open | — |
| 19 | Extract filter-arg vars as deps in `extract_from_variable` | PR #785 | #787 | Open | `{{ a\|default:fallback }}` drops `fallback` |
| 20 | Slot-in-for-loop test coverage (Risk 1 from plan) | PR #788 | #789 | Open | — |
| 21 | `{% render_slot slots.col.0 %}` dotted-path end-to-end test | PR #788 | #790 | Open | — |
| 22 | Unrelated ruff reformats of 5 test files (stashed during PR #788) | PR #788 | #791 | Open | Chore |
| 23 | `assign_async` concurrent same-name cancellation semantics | PR #792 | #793 | Open | In-flight task can overwrite with stale data |
| 24 | `logger.debug` ping on non-AsyncResult value in `{% dj_suspense await= %}` | PR #792 | #794 | Open | Chore |
| 25 | `suspense.py:138` redundant check + CHANGELOG test-count nit | PR #792 | #795 | Open | Chore |
| 26 | Variable-height virtual list items via ResizeObserver | PR #796 | #797 | Open | ~200 LOC, v0.5.1 candidate |
| 27 | `teardownVirtualList` should restore `originalChildren` | PR #796 | #798 | Open | — |
| 28 | Server-side `stream_append(limit=N)` should trim inserts before sending | PR #796 | #799 | Open | — |
| 29 | Clarify/update ~5 KB client JS budget claim in CLAUDE.md/manifesto | PR #796 | #800 | Open | client.js grew ~355→380 KB raw in v0.5.0 |
| 30 | `stream_prune` `.children` filter redundancy in `17-streaming.js` | PR #796 | #801 | Open | Chore |
| 31 | Block-handler loader access (deferred item 2b from PR #802) | PR #802 | #803 | Open | ~40 LOC Rust |
| 32 | Parent-tag propagation for nested custom-tag handlers (deferred item 2c) | PR #802 | #804 | Open | — |
| 33 | Warn when `register_assign_tag_handler` returns non-dict | PR #802 | #805 | Open | ~5 LOC |
| 34 | Extend `Context::resolve` to `Node::For` iterables over Model instances | PR #802 | #806 | Open | `{% for user in users %}` over QuerySet doesn't walk getattr |
| 35 | `PostgresNotifyListener` event-loop binding across `async_to_sync` | PR #807 | #808 | Open | — |
| 36 | `untrack()` helper for `@notify_on_save` signal receiver cleanup | PR #807 | #809 | Open | — |
| 37 | NOTIFY payload size guard (PostgreSQL 8000-byte limit) | PR #807 | #810 | Open | — |
| 38 | `reset_for_tests` should await task cancellation | PR #807 | #811 | Open | — |
| 39 | Regression test for views without `NotificationMixin` | PR #807 | #812 | Open | — |
| 40 | Document 100 ms render-lock timeout behavior in db_notify handler | PR #807 | #813 | Open | — |
| 41 | `dj-ignore-attrs` should cover morph-path attribute sync/removal | PR #814 | #815 | Open | Contract-scope gap in 12-vdom-patch.js |
| 42 | `dj-ignore-attrs` CSV edge cases (empty/whitespace/trailing comma/double comma) | PR #814 | #816 | Open | — |
| 43 | Namespacing `AttributeError` fallback regression test | PR #814 | #817 | Open | — |
| 44 | Escape namespaced name in `data-hook` attribute (defense in depth) | PR #814 | #818 | Open | Chore |
| 45 | Pre-signed S3 PUT URLs (client-direct S3 upload bypassing djust) | PR #819 | #820 | Open | Feature |
| 46 | Resumable uploads across WebSocket disconnects | PR #819 | #821 | Open | Feature, v0.6+ |
| 47 | First-class GCS and Azure Blob UploadWriter subclasses | PR #819 | #822 | Open | Feature |
| 48 | `BufferedUploadWriter._finalized` flag is dead code | PR #819 | #823 | Open | — |
| 49 | Client "stop sending" signal after size-limit abort (BufferedUploadWriter backpressure) | PR #819 | #824 | Open | — |
| 50 | Document JSON-serializability constraint on `UploadWriter.close()` return | PR #819 | #825 | Open | — |
| 51 | Document that `<script>` in swapped `<main>` won't execute (SW + #814 interaction) | PR #826 | #827 | Open | — |
| 52 | `DjustMainOnlyMiddleware` should early-return on 4xx/5xx responses | PR #826 | #828 | Open | — |
| 53 | `registerServiceWorker` duplicate-call guard | PR #826 | #829 | Open | — |
| 54 | Middleware should match `text/html` with charset variants | PR #826 | #830 | Open | — |

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

## v0.5.0 — Feature Rollout (PRs #784–#826)

**Date**: 2026-04-21
**Scope**: Ten v0.5.0 roadmap items shipped in a single pipeline-run session spanning ~26 hours: the "true #783 fix" (#784), P0 dep-extractor hardening (#785), Component System (#788), async rendering (#792), large-list DOM perf (#796), Rust template parity (#802), PostgreSQL LISTEN/NOTIFY → push (#807), hook polish (#814), UploadWriter (#819), and Service Worker core (#826). Closes the v0.5.0 milestone.
**Tests at close**: ~270 new tests (Python 1264 passing / JS 1174 passing / Rust 620 passing, 0 regressions)

### What We Learned

**1. The strategic-enabler pattern compounds — ship the primitive, reuse it.**
PR #788's `register_block_tag_handler` was the primitive that carried seven subsequent PRs: #792 (`{% dj_suspense %}`), #796 (`stream_prune` op), #802 (`register_assign_tag_handler` sibling), #807 (consumer group send), #814 (`{% colocated_hook %}`). Each subsequent PR got smaller because the prior PR laid a reusable primitive. IntersectionObserver in #796 played the same role across virtual lists and viewport sentinels. The `Arc<HashMap<String, PyObject>>` sidecar in #802 spared a `Value: Serialize` refactor.

**Action taken**: Noted in #788's retro and reinforced in every subsequent PR's "Milestone connection" section. Primitive-first design is now the default for v0.6 work.

**2. Stage 11 (independent code review) kept catching real defects Stage 7 (self-review) missed — three times.**
- **#796**: `window.djust.pushEvent` didn't exist — the viewport-event feature was broken end-to-end. Unit tests asserted on the dispatched CustomEvent, never on the server round-trip. Fixed via `window.djust.handleEvent` + regression test.
- **#814**: `</Script>` mixed-case escape gap — tag-breakout risk in the colocated-hook script envelope. Fixed with case-insensitive regex + all-casing regression test.
- **#819**: Raw boto3 exception strings leaked IAM ARNs / bucket names / endpoints to the browser via `entry._error`. Also a docstring example that contradicted the security callout. All three (and a second leak in the S3 doc example) fixed in one follow-up commit with a pinning regression test (`test_error_messages_do_not_leak_raw_exception_text`).

Pattern worth pinning: Stage 7 tends to miss security/correctness issues in **doc examples** and at **contract boundaries** (what reaches the client). **Action taken**: Session-wide "fix ALL findings in one push" discipline held — each was resolved in a single follow-up commit before merge, not deferred.

**3. "Closed without code" claims must be verified against the reporting downstream test.**
PR #779 was originally credited with closing #783 in both the ROADMAP and the issue tracker. The NYC Claims downstream test (`test_autofill_then_next_step_works`) stayed red. Re-opening exposed that #779 only fixed the Python-side `id()` comparison; the real bug was in `extract_from_nodes` silently dropping deps from nested `Include`/`InlineIf`. PR #784 fixed the actual root cause; PR #785 added the P0 correctness harness (compile-time `Node` variant exhaustiveness + Rust unit tests on `extract_per_node_deps` + Python byte-identical partial-vs-full oracle) so a third instance of this bug class cannot ship silently.

**Action taken**: ROADMAP attribution corrected in PR #784. Dep-extractor now has three complementary guards.

**4. ROADMAP drift — grep before implementing.**
PR #792 found that `temporary_assigns` was already implemented — the ROADMAP claimed "completely absent from djust today" when in fact the machinery was wired through multiple modules. Caught during the Stage 6 codebase survey. Pivoted to regression-test-only + ROADMAP correction. Second time this session — PR #784 also found #783 attribution stale.

**Action taken**: Future rule — BEFORE implementing a ROADMAP P-item, grep for its keywords in the codebase. A `make roadmap-lint` comparing P-items against grep-able tokens is the obvious automation follow-up.

**5. Pre-commit commit-loop friction amplifies each fix attempt.**
PR #814 took six commit attempts to land. Root causes compounded: eslint `no-new-func` at the `new Function` call site, ruff E402 import-order in tests, AND the pre-commit hook's stash-restore cycle itself re-triggering each fix. Running formatters+linters against the *exact staged files* BEFORE `git commit` — not reacting after the hook fails — would have cut this to 1 attempt.

**Action taken**: Added as a process note in PR #814's retro. Belongs in the Stage 9 (commit) pipeline checklist.

**6. Client.js weight drifted past manifesto budget.**
CLAUDE.md claims ~5 KB client JS; the session added roughly +25 KB raw (355 KB → 380 KB): +15.7 KB in #796 (virtual lists + infinite scroll), +6.4 KB in #814 (colocated hooks), ~0 in #826 (service worker is a separate file). The 5 KB number is aspirational/gzipped; needs an explicit clarification in the manifesto or a bundle-split plan before v0.6.

**Action taken**: Filed as follow-up #800 (tracker row #29). Does not block v0.5.0 release.

**7. Ghost-branch drift in subagent workflows.**
PRs #788, #814, #819, and #826 each saw at least one commit land on a phantom `pr-NNN` branch instead of the feature branch, requiring cherry-pick recovery. PR #826 hit it twice. Appears to happen when subagents operate in fresh git contexts and something (likely `gh pr checkout` state) sets up a tracking branch silently. Has not lost work yet, but adds recovery overhead.

**Action taken**: Note captured in multiple per-PR retros. Proposed v0.6 mitigation: pin branch explicitly in subagent prompts + verify `git symbolic-ref HEAD` in stage procedures.

### Insights

- **Phoenix 1.0/1.1 parity milestone reached.** #788 (function components + declarative assigns + named slots), #792 (assign_async + AsyncResult + `{% dj_suspense %}` + temporary_assigns regression), #796 (dj-viewport-top/bottom), #814 (JS.ignore_attributes + colocated hooks + namespacing) together close roughly seven Phoenix.LiveView parity items. djust is now meaningfully at Phoenix 1.1 parity for the core LiveView feature set.
- **Killer feature of the milestone: #807 PostgreSQL LISTEN/NOTIFY → LiveView push.** No other Python web framework has this as a first-class primitive. Phoenix has it via PubSub + Ecto. This is a genuine djust differentiator vs. Rails/Laravel/stock Django.
- **Strategic-enabler pattern was load-bearing.** Six of the ten PRs would have been 2–5× larger without reusing a primitive laid by an earlier PR in the same session. This is the clearest vindication of "Complexity Is the Enemy" (manifesto #1) in the project's history.
- **Stage 11 catch rate was high on new-runtime PRs, low on Rust-surface PRs.** #807 produced 6 non-blocking findings (new process-singleton async task); #802 produced 0 (small, well-typed Rust surface). Stage 7 self-review is adequate for small Rust crates; Stage 11 remains essential for JS/E2E/new-runtime work. Do not collapse the two-stage discipline.
- **Zero Stage 11 rubber-stamps across 10 PRs.** Every PR had at least one acted-on finding. This is the feedback_pipeline_discipline memory working as designed.
- **Commit-loop friction (#814) is the one real process regression of the session.** Every other PR landed in 1–2 commit attempts. Stage 9 (commit) checklist needs the pre-run-formatters step.

### Review Stats

| Metric | #784 | #785 | #788 | #792 | #796 | #802 | #807 | #814 | #819 | #826 | Total |
|--------|------|------|------|------|------|------|------|------|------|------|-------|
| Files changed | 5 | 5 | 10 | 11 | 15 | 14 | 15 | 14 | 6 | 12 | 107 |
| Tests added | 7 | 16 | 52 | 32 | 30 | 22 | 42 | 24 | 27 | 17 | 269 |
| 🔴 Findings | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 3 | 0 | 4 |
| 🟡 Findings | 0 | 0 | 4 | 3 | 1 | 4 | 6 | 3 | 3 | 4 | 28 |
| Findings fixed pre-merge | 0 | 0 | 4 | 1 | 2 | 4 | 0 | 1 | 3 | 1 | 16 |
| CI / hook failures | 0 | 1 | 1 | 1 | 1 | 1 | 0 | 6 | 1 | 1 | 13 |

### Process Improvements Applied

**CLAUDE.md**: No changes this milestone (client.js weight claim flagged for revision — tracker row #29).
**Pipeline template**: No changes this milestone. Stage 9 pre-run-formatters checklist item proposed (PR #814 retro) — to land in v0.6.
**Skills**: No changes this milestone. `/pipeline-dev` skill validated again as the correct fast-iteration mode for this session.
**ROADMAP**: Corrected in #784 (#783 attribution) and #792 (`temporary_assigns` already present). Two-strikes suggests a `make roadmap-lint` check before v0.6.

### Open Items

- [ ] Tracker rows #18–#19 — dep-extractor hardening follow-ups (PR #785 → #786, #787)
- [ ] Tracker rows #20–#22 — component-system coverage + chore (PR #788 → #789, #790, #791)
- [ ] Tracker rows #23–#25 — async rendering follow-ups (PR #792 → #793, #794, #795)
- [ ] Tracker rows #26–#30 — large-list DOM perf follow-ups + client.js budget (PR #796 → #797–#801)
- [ ] Tracker rows #31–#34 — Rust template parity deferrals (PR #802 → #803–#806)
- [ ] Tracker rows #35–#40 — DB change notifications hardening (PR #807 → #808–#813)
- [ ] Tracker rows #41–#44 — hook polish follow-ups (PR #814 → #815–#818)
- [ ] Tracker rows #45–#50 — UploadWriter features + tech-debt (PR #819 → #820–#825)
- [ ] Tracker rows #51–#54 — Service Worker follow-ups (PR #826 → #827–#830)

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
