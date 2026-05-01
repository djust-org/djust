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
| 17 | Ship final standalone package versions as deprecation shims | Retro v0.5.0 | #778 | Closed | Shipped as v99.0.0 git tags + DeprecationWarning shims in all 5 sibling repos (djust-{auth,tenants,admin,theming,components}), 2026-04-23. **PyPI publish deliberately deferred (Path A)**: existing PyPI versions stay as-is; new users are directed to `pip install djust` via the updated READMEs. Rationale: sibling packages had low/no PyPI download volume; the forced `djust>=0.5.6rc1` shim dep would pull full framework + Rust wheels into users who only wanted a narrow subset. Can publish later if user feedback surfaces. |
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
| 29 | Clarify/update ~5 KB client JS budget claim in CLAUDE.md/manifesto | PR #796 | #800 | Closed | CLAUDE.md updated with accurate numbers; pre-minified distribution scoped as v0.6.0 P1 ROADMAP entry |
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
| 55 | `djust_typecheck`: support `{% firstof %}`, `{% cycle %}`, `{% blocktrans with %}` | PR #849 | #850 | Closed | Shipped in PR #859 |
| 56 | `djust_typecheck`: MRO walk for `self.foo = ...` assigns from parent classes | PR #849 | #851 | Closed | Shipped in PR #859 |
| 57 | Extract shared `_walk_subclasses` / `_is_user_class` (2x duplication; reviewer claimed 3x) | PR #849 | #852 | Closed | Shipped in PR #859 |
| 58 | `follow_redirect` silent drop on multiple redirects | PR #842 | #844 | Closed | Shipped in PR #865 |
| 59 | `handle_async_result` callback not invoked in `render_async` | PR #842 | #843 | Closed | Shipped in PR #865 |
| 60 | Document `<script>` limitation of instant-shell swap (SW + dj-hook) | PR #826 | #827 | Closed | Shipped in PR #860 (also found + fixed actual dj-hook re-bind bug via Stage 11) |
| 61 | Middleware early-return on 4xx/5xx responses | PR #826 | #828 | Closed | Shipped in PR #860 |
| 62 | `registerServiceWorker` idempotency guard | PR #826 | #829 | Closed | Shipped in PR #860 |
| 63 | Middleware content-type widening (xhtml, charset/boundary tolerance) | PR #826 | #830 | Closed | Shipped in PR #860 |
| 64 | Slot-in-for-loop end-to-end test coverage | PR #788 | #789 | Closed | Shipped in PR #862 |
| 65 | `{% render_slot slots.col.0 %}` dotted-path end-to-end test | PR #788 | #790 | Closed | Shipped in PR #862 at handler level; surfaced new Rust bug #861 |
| 66 | `{% render_slot %}` Rust engine returns empty for all input | (drain) | #861 | Closed | Issue manually closed 2026-04-22 |
| 67 | Morph-path should honor `dj-ignore-attrs` | PR #814 | #815 | Closed | Shipped in PR #863 |
| 68 | `dj-ignore-attrs` CSV edge cases (empty/whitespace/trailing comma) | PR #814 | #816 | Closed | Shipped in PR #863 |
| 69 | Namespacing `AttributeError` fallback regression test | PR #814 | #817 | Closed | Shipped in PR #863 |
| 70 | `BufferedUploadWriter._finalized` flag dead code | PR #819 | #823 | Closed | Shipped in PR #864 |
| 71 | Trailing-chunks-after-abort fast-path (partial #824) | PR #819 | #824 | Closed | Shipped in PR #864 as log fast-path; full "stop sending" push-event deferred |
| 72 | Document JSON-serializability on `UploadWriter.close()` return | PR #819 | #825 | Closed | Shipped in PR #864 (+ runtime validation) |
| 73 | `stream()` with `limit=N` pre-trims emitted inserts | PR #796 | #799 | Closed | Shipped in PR #866 |
| 74 | `teardownVirtualList` restores original children | PR #796 | #798 | Closed | Shipped in PR #866 |
| 75 | `stream_prune` `.children` filter redundancy | PR #796 | #801 | Closed | Shipped in PR #866 (cosmetic) |
| 76 | `send_pg_notify` payload size guard | PR #807 | #810 | Closed | Shipped in PR #867 |
| 77 | `PostgresNotifyListener.reset_for_tests` awaits cancellation | PR #807 | #811 | Closed | Shipped in PR #867 as `areset_for_tests` |
| 78 | Regression test: consumer handles views without `NotificationMixin` | PR #807 | #812 | Closed | Shipped in PR #867 |
| 79 | Document 100ms `db_notify` render-lock timeout semantics | PR #807 | #813 | Closed | Shipped in PR #867 |
| 80 | FormArrayNode drops inner template content (block body parsed but never rendered) | PR #929 | #930 | Closed | Shipped in PR #939 — renders self.nodelist per row |
| 81 | `tag_input` widget missing `name` attribute — form submissions drop value | PR #929 | #932 | Closed | Shipped in PR #939 — hidden input with name + CSV payload |
| 82 | `gallery/registry.py get_gallery_data` never consumes `discover_*` results | PR #929 | #933 | Closed | Shipped in PR #939 — wires discovery as drift-detector |
| 83 | `_registry.py` F401 unused-import alerts may need explicit `# noqa` post-rescan | PR #929 | #1025 | Open | No issue filed; revisit after CodeQL rescan |
| 84 | Add CodeQL MaD model for `sanitize_for_log` to close log-injection FP class | PRs #913/#923 | #934 | Closed | Shipped in PR #945 — `.github/codeql/models/djust-sanitizers.model.yml` |
| 85 | Pre-existing main test failures (`test_api_response`, `test_observability_eval_handler`, `test_observability_reset_view`) | Arc #898–#931 | #935 | Closed | Shipped in PR #946 — 3 tests fixed, main now clean |
| 86 | Verify post-#928 CodeQL rescan closed the 872 cyclic-import alerts | PR #928 | — | Closed | Confirmed 2026-04-23: open alerts dropped from ~1130 to 37 |
| 87 | `dispatch.py:295` vs `observability:399` JSON-parse error message consistency | PR #919 | #1026 | Closed | Style-only follow-up | **Resolved in v0.8.1 (#1067)** — Shipped in PR #1067 (security upgrade — generic JSON-parse error + logger.exception) |
| 88 | Replace `inspect.getsource + substring` test with behavior-level test | PR #919 | #1027 | Closed | Test quality | **Resolved in v0.8.1 (#1066)** — Shipped in PR #1066 (replaced inspect.getsource test with behavior test) |
| 89 | `javascript:` scheme + HTTPS downgrade + null-byte storybook rejection tests | PR #920 | #922 | Closed | Shipped in PR #946 — 4 edge tests added |
| 90 | Audit ALL `HttpResponseRedirect`/`redirect()` sites for `url_has_allowed_host_and_scheme` guards | PR #920 | #921 | Closed | Shipped in PR #946 — audited + fixed mixins/request.py, auth/mixins.py |
| 91 | Shared `conftest.py` staff-user fixture for auth-gated view tests | PR #918 | #1028 | Closed | Tooling | **Resolved in v0.8.1 (#1066)** — Shipped in PR #1066 (make_staff_user factory in python/djust/tests/conftest.py) |
| 92 | `docs/internal/codeql-patterns.md` taint-flow cheat sheet | PR #918 | #1029 | Closed | Docs | **Resolved in v0.8.1 (#1064)** — Shipped in PR #1064 (docs/internal/codeql-patterns.md) |
| 93 | Automate CHANGELOG test-count validation (3rd recurrence across #898/#904/#885) | PRs #898/#904 | #908 | Closed | Shipped in PR #945 — scripts/check-changelog-test-counts.py + pre-commit hook |
| 94 | Bump `.pre-commit-config.yaml` ruff-pre-commit from v0.8.4 to current | PR #940 retro | #948 | Open | Local ruff disagreements with pinned hook cause spurious format churn |
| 95 | tag_input CSV serialization ambiguous for commas-in-values | PR #939 retro | #949 | Open | Escape, multiple inputs, or JSON-encode — decide and document |
| 96 | pipeline-run outer loop should verify retro comment before `completed_at` | PR #946 retro | #950 | Open | Retro dropout caught in drain — add artifact gate |
| 97 | dj-virtual variable-height: data-key-based cache survives reorders | PR #947 retro | #951 | Open | Currently index-keyed; reorders bind heights to wrong items |
| 98 | dj-virtual variable-height guide page | PR #947 retro | #952 | Open | Tuning estimated-height, scrollbar-jump tradeoffs, data-key story |
| 99 | Consolidate JSDOM test helpers (DOMContentLoaded wait + repo-root cwd) | Retros #885/#918/#925/#943 | #953 | Closed | Shipped in PR #956 |
| 100 | `make ci-mirror` target — run exact CI pytest invocation locally | Retro v0.5.7 / PR #959 | #960 | Closed | Shipped in v0.7.1 PR #995 (merged as `56bd85d5`) — `make ci-mirror` target mirrors the exact CI pytest invocation locally; ~90 LOC. Pipeline-dev (condensed) flow used: no subagent reviews, no formal Stage 7/8/11 — DX tooling change. |
| 101 | Replace hand-rolled Redis mock with `fakeredis` in test_security_upload_resumable.py | Retro v0.5.7 / PR #959 | #961 | Closed | Shipped in v0.7.1 PR #996 (merged as `d54dcd0f`) — net −19 LOC + accurate Redis semantics (real TTL, real key expiry, real connection-error path). Test-only refactor; clean Stage 11 APPROVE with 0 🔴. |
| 102 | v0.6.0 or v0.7.0 decision: breaking rename of framework-internal attrs to `_*` prefix | Retro v0.5.7 / PR #957 | #962 | Open | #762 shipped non-breaking filter; rename still on table |
| 103 | Weekly real-cloud CI matrix job for S3 / GCS / Azure upload writers | Retro v0.5.7 / PR #958 | #963 | Open | All SDK tests are mocked; no real-cloud end-to-end |
| 104 | Document `key_template` UUID-prefix convention for `s3_events.parse_s3_event` | Retro v0.5.7 / PR #958 | #964 | Open | Silent fallback to full key otherwise |
| 105 | Substring-matching tests in other existing suites should be rewritten to parse HTML | Retro v0.6.0 / PR #966 | — | Closed | Discipline-resolved: v0.6.1 PRs #974/#975/#976 all used HTML-parsed assertions; pattern consistent across three features. Remaining legacy suites swept opportunistically. |
| 106 | Silent cache-write failures in `03-websocket.js:386` should log under `djustDebug` | Retro v0.6.0 / PR #970 | #1030 | Closed | Tech-debt | **Resolved in v0.8.1 (#1067)** — Shipped in PR #1067 (cache-write debug log under djustDebug) |
| 107 | No version-probe fallback for `mount_batch` — older servers produce generic "unknown msg type"; client should fall back gracefully | Retro v0.6.0 / PR #970 | #1031 | Closed | Tech-debt | **Resolved in v0.8.1 (#1068)** — Shipped in PR #1068 (mount_batch fallback for old-server compat) |
| 108 | Dashboard→Dashboard re-mount limitation in sticky LiveView demo; `{% live_render %}` doesn't auto-detect preserved stickies | Retro v0.6.0 / PR #969 | #1032 | Closed | v0.6.x/v0.7.0 enhancement — teach tag to emit slot markers automatically | **Shipped in v0.9.0 via PR #1128** — sticky LiveView auto-detect (ADR-014). Closes the 1.0 blocker. |
| 109 | `djust[admin]` extra vs `djust.admin_ext` module name divergence | Retro v0.6.0 / PR #971 | #1033 | Closed | Rename one or the other in v0.7.0 | **Resolved in v0.8.1 (intentional)** — Closed at Stage 4 — naming divergence is intentional per docs/website/guides/migration-from-standalone-packages.md |
| 110 | Hardcoded `TARGET_LIST_UPDATE_S * 20` for WS mount target in perf tests should become named `TARGET_WS_MOUNT_S` | Retro v0.6.0 / PR #972 | #1034 | Closed | Tech-debt | **Resolved in v0.8.1 (#1066)** — Shipped in PR #1066 (TARGET_WS_MOUNT_S named constant) |
| 111 | cProfile top-N table in `docs/performance/v0.6.0-profile.md` is a single-run snapshot; add "not canonical" disclaimer | Retro v0.6.0 / PR #972 | #1035 | Closed | Tech-debt | **Resolved in v0.8.1 (#1064)** — Shipped in PR #1064 (cProfile snapshot disclaimer) |
| 112 | `_assert_benchmark_under` helper should move to `tests/benchmarks/conftest.py` for shared scope | Retro v0.6.0 / PR #972 | #1036 | Closed | Tech-debt | **Resolved in v0.8.1 (#1066)** — Shipped in PR #1066 (helpers in tests/benchmarks/conftest.py) |
| 113 | Pre-commit Self-Review should grep for stubbed JSDOM API shapes (greenwashing-catcher) | Retro v0.6.1 / PR #976 | #1037 | Closed | **Resolved in v0.9.1 release arc** — PR #1226 (v0.9.1-7 canon batch). `globalThis.djust.websocket` was stubbed in test; no source ever assigns it — real path is `window.djust.liveViewInstance.sendMessage`. Add check: if JSDOM test stubs `djust.FOO` and nothing in source assigns it, flag. |
| 114 | Planning-stage check: "grep for how OTHER callers do X" before implementation agents write send-path / API-consuming code | Retro v0.6.1 / PR #976 | #1038 | Closed | **Resolved in v0.9.1 release arc** — Already canon in CLAUDE.md §v0.9.0 (closed during v0.9.1-7 audit). Implementer invented `globalThis.djust.websocket` instead of reading `03-tab-events.js` / `11-integration.js`. Planner should answer "how does existing code do X?" before implementation starts. |
| 115 | Mutation-after-capture test discipline for any snapshot/capture function | Retro v0.6.1 / PR #976 (+ latent v0.6.0 bug) | #1039 | Closed | **Resolved in v0.9.1 release arc** — PR #1226 (v0.9.1-7 canon batch). `_capture_snapshot_state` reference-aliasing bug existed unnoticed for two milestones (v0.6.0 `enable_state_snapshot` + v0.6.1 time-travel). Generalize: every capture function needs a test exercising mutation after capture. |
| 116 | Doc-accuracy data-flow trace — require implementation agents to trace data-flow of each claimed benefit before writing user-facing docs | Retro v0.6.1 / PR #975 (+ v0.6.0 PRs #969/#971/#972 pattern) | #1040 | Closed | **Resolved in v0.9.1 release arc** — PR #1226 (subsumed by #1046). **Phase 1 doc-claim debt closed in v0.9.0 via PR #1135** (Phase 2 streaming PR-A reframed Phase 1 as transport-layer-only; ADR-015 documents the actual semantics). The general process rule (trace data-flow before writing docs) remains open as a Stage 7 self-review pattern. |
| 117 | Component-level time-travel (Phase 1 records against parent; full component capture) | Retro v0.6.1 / PR #976 | #1041 | Closed | v0.6.2 candidate | **Shipped in v0.9.0 via PR #1141** — `_capture_components_snapshot` extension under reserved `__components__` snapshot key. |
| 118 | Forward-replay through branched timeline (Redux DevTools parity) | Retro v0.6.1 / PR #976 | #1042 | Closed | v0.6.2 candidate | **Shipped in v0.9.0 via PR #1142** — `replay_event(view, snapshot, override_params, record_replay)` with dunder reject + handler-captured-before-restore. |
| 119 | Phase 2 streaming (lazy-child render + true server overlap) | Retro v0.6.1 / PR #975 | #1043 | Closed | v0.6.2 — Phase 1 was transport-layer only | **Shipped in v0.9.0 via PRs #1135 + #1138 + #1139** — split-foundation arc (PR-A async render path, PR-B `lazy=True` capability + `as_view` dispatch, PR-C `asyncio.as_completed` parallel render). ADR-015. |
| 120 | ADR-006 AI-generated UIs — deferred due to AssistantMixin/LLM-provider dependency chain | Retro v0.6.1 | #1044 | Closed | Deferred from v0.6.1 to v0.6.2 | **Resolved in v0.8.1 (v0.9.0 ROADMAP)** — Closed-as-deferred to v0.9.0+ via PR #1069 |
| 121 | Shared `_SCRIPT_CLOSE_TOLERANT_RE` constant for HTML5-tolerant `</script>` matching | Retro v0.6.1 / PR #975 | #1045 | Closed | Third occurrence of CodeQL py/bad-html-filtering-regexp (PR #966, #970, #975). Centralize into `mixins/template.py` or a new `_html_utils.py`. | **Resolved in v0.8.1 (intentional)** — Closed at Stage 4 — already centralized at templatetags/live_tags.py:39 |
| 122 | Post-commit verification step in pipeline-run skill: `git log -1 --oneline` sanity check after every `git commit` | Retro v0.6.1 / PR #974 (+ PRs #989, #996, #1007, #1008, #1014, #1015, #1021, #1024) | — | **Closed (skill-level)** | Eight reinforcements in single 24-hour session (PRs #989, #996, #1007, #1008, #1014, #1015, #1021, #1024). Implemented as new "MANDATORY Post-Commit Verification (Action #122)" section in `~/.claude/skills/pipeline-run/SKILL.md` (2026-04-25, post-v0.8.0rc1). Documents the failure mode (pre-commit hook stash → reformat → conflict → silent rollback), the canonical fix (`git commit -m "..." && git log -1 --oneline`), the load-bearing detail (`&&` chains the verify, so the agent immediately sees the previous-commit subject if the commit didn't register), and the "never skip" rationale. Resolves the highest-ROI technical-debt item from the v0.6.1 → v0.8.0 session arc. |
| 123 | FORCE_SCRIPT_NAME / mounted sub-path support for JS clients (hardcoded `/djust/api/...` prefix in `48-server-functions.js` and other client modules) | Retro v0.7.0 / PR #986 | #987 | Closed | Shipped in v0.7.1 PR #993 (merged as `f03d64eb`) — `{% djust_client_config %}` template tag (dual-registered for Django + Rust engines per the djust_markdown precedent) + `window.djust.apiPrefix` / `djust.apiUrl(path)` helpers + `48-server-functions.js` routed through the helper. 15 new tests (5 Py + 6 JS + 1 regression + 3 dual-engine parity cases added at Stage 12). Bundle delta +148 B gzipped. Follow-up filed for `03b-sse.js:44` (SSE fallback transport — same class of bug, #992, v0.7.2 target). |
| 124 | Upgrade Action #116 — for every feature with non-trivial semantics (gate rules, error envelopes, state contracts), write doc-claim-verbatim tests BEFORE writing implementation | Retro v0.7.0 / PR #988 (+ v0.6.0/v0.6.1/#986 pattern) | #1046 | Closed | **Resolved in v0.9.1 release arc** — PR #1226 (v0.9.1-7 canon batch). 4th consecutive milestone with doc-vs-code drift 🔴/🟡. Action #116 ("trace data-flow before writing docs") is aspirational, not executable. Upgrade to TDD sharpened: the test cases ARE the doc claims. Enforcement: Stage 7 checklist grows a "for each documented rule, point to the asserting test" row. PR #989 application: partial — five rule tests written RED first, but PR-body headline claim ("action fires → redirect to progress page") was never a test; that's the 🔴 Stage 11 caught. Subsumed for user-visible features by #125. |
| 125 | Upgrade Stage 7 checklist with user-flow trace — for every user-visible feature, trace the happy-path user story end-to-end (HTTP request → server dispatch → response envelope → browser render/navigation) | Retro v0.7.0 / PR #989 (+ PR #986 + PR #988 pattern) | #1047 | Closed | **Resolved in v0.9.1 release arc** — PR #1226 (v0.9.1-7 canon batch). 3rd consecutive pipeline where Stage 7 rubber-stamped a diff that Stage 11 proved was broken end-to-end. PR #986 — JsonResponse outside try/except (response-layer). PR #988 — fire-and-forget flush breaking same-round-trip (transport-layer). PR #989 — HttpResponseRedirect silently dropped by @event_handler (dispatch-layer). Common shape: code does a thing, but thing doesn't reach the user. Enforcement: Stage 7 output template grows a "User flow trace" section with a required bullet per user-visible feature. **Validated across 5 pipelines** (#990, #993, #995, #996, #997 — all 0 🔴 at Stage 11; #995 + #996 ran condensed pipeline-dev flow with no Stage 11 but no live-verify regressions either). The class of defect that plagued #976/#988/#989 has not recurred since #125 was filed. |
| 126 | Flaky perf test triage — `test_broadcast_latency_scales[10]` missed its 10 ms budget by ~12× on py3.13 CI runner (py3.12/py3.14 passed) | Retro v0.7.0 / PR #990 | #1048 | Closed | Caused 1 CI retry on PR #990. 10-subscriber case only; 1-subscriber and 50-subscriber cases passed. Options: (a) per-runner tolerance, (b) `@pytest.mark.flaky(reruns=2)`, (c) move to non-required check. | **Resolved in v0.8.1 (PR #1021)** — Closed-as-superseded by PR #1021's unconditional 30ms budget bump |
| 127 | Stage 9 test-count recount rule — Stage 9 (Documentation) must re-count tests AFTER Stage 7/12 fix-pass deltas and update the CHANGELOG test-count line before the final docs pass | Retro v0.7.0 / PR #990 | #1049 | Closed | **Resolved in v0.9.1 release arc** — PR #1226 (v0.9.1-7 canon batch). PR #990 CHANGELOG claimed "38 total" but actual was 41 (docs author cited Stage 5 count, not post-fix-pass). Stage 11 caught it. Second milestone running with a small CHANGELOG test-count drift — auditable claim, should be precise. Enforcement: Stage 9 checklist grows a "run `make test`, record final count, diff against CHANGELOG" row. |
| 128 | External-crate doc.rs read before implementation for security-surface dependencies — any external crate (Rust or Python) whose API forms part of a security boundary must have its doc.rs entry read at Stage 4/5 for the specific API surface we use | Retro v0.7.0 / PR #990 | #1050 | Closed | **Resolved in v0.9.1 release arc** — PR #1226 (v0.9.1-7 canon batch). PR #990 surfaced two pulldown-cmark 0.12 API corrections only because RED tests failed: `Options::ENABLE_HTML` omission does NOT suppress `Event::Html`, and `Options::ENABLE_GFM_AUTOLINK` doesn't exist in 0.12. Luck saved the XSS surface. Doc-reading first would be systematic. Enforcement: Stage 4 plan template grows a "linked doc.rs section for each external security-boundary API" row. |
| 129 | Stage 4 planner checklist — engine-path declaration for template tags. Any new template tag plan must explicitly state which engine(s) render the template(s) that consume it (Django template engine, Rust template engine, or both) and plan registration accordingly | Retro v0.7.1 / PR #993 | — | Partially validated | PR #993 originally registered `{% djust_client_config %}` ONLY with Django's template library; pre-push pytest caught that `base.html` (rendered through the Rust engine for LiveView views) 500'd because the Rust engine didn't know the tag. Fix mirrored PR #990 dual-registration pattern. Stage 4 plan template grows an "Engine path" bullet — for each new template tag, list the engine(s) that render templates consuming the tag. Second canonical example (alongside `djust_markdown`) is `djust_client_config`. **Partially validated v0.7.1**: PR #997 (SSE FORCE_SCRIPT_NAME) was a transport-layer mirror, not a template-tag PR — engine-path checklist not exercised. Generalized into Action #131 (broader: declare engine-paths during Stage 4 for any feature touching templates). |
| 130 | SSE FORCE_SCRIPT_NAME / mounted sub-path support — `03b-sse.js:44` hardcoded `/djust/sse/` prefix breaks the same way as `48-server-functions.js` did | Retro v0.7.0 / PR #993 follow-up | #992 | Closed | Shipped in v0.7.1 PR #997 (merged as `4adc27b6`). Mechanically applied the PR #993 pattern: meta-tag emission via `{% djust_client_config %}` extension + `djust.ssePrefix` + `djust.sseUrl()` helper; +3 tests; +46 B bundle. First-push clean merge (single Stage 11 APPROVE, 0 🔴/🟡). **Template-reuse dividend**: total engineering time was a fraction of PR #993 — PR #993 established the pattern, #997 applied it. |
| 131 | Stage 4 plan-template "Engine path" bullet should generalize beyond template-tags — any feature that touches the template rendering pipeline (filters, tags, context processors, custom blocks, post-processing hooks) must declare which engine(s) the user templates run through | Retro v0.7.1 / PR #993 generalization | #1051 | Closed | **Resolved in v0.9.1 release arc** — PR #1226 (v0.9.1-7 canon batch). Generalizes Action #129. PR #993 caught the dual-engine bug ONLY because pre-push runs the full demo suite; targeted Stage 6 subsets miss it. Class of bug: any code path that participates in user template rendering can silently work in one engine and 500 in the other. Enforcement: Stage 4 plan template's "Engine path" row applies to filters, context processors, post-processing hooks, and any registry-style API — not just `register_tag_handler`. |
| 132 | Pipeline-run skill should list pipeline-dev-eligible PR shapes explicitly | Retro v0.7.1 / PRs #995 + #996 | #1052 | Closed | Empirically validated this milestone: PR #995 (Makefile target) and PR #996 (test-only refactor) shipped cleanly under condensed pipeline-dev flow (no subagent reviews, no Stage 7/8/11). Proposed heuristic: pipeline-dev-eligible iff PR touches only {Makefile, scripts/, docs/, tests/} AND has zero changes under `python/djust/` or `crates/`. Production code always goes through pipeline-run. Action: update `~/.claude/skills/pipeline-run/SKILL.md` with a triage section before next milestone. | **Resolved in v0.8.1 (v0.9.0 ROADMAP)** — Skill-level work tracked under out-of-scope-for-djust-drain label |
| 133 | py3.14 timing-sensitive CI flake class — `test_hotreload_slow_patch_warning` and `test_broadcast_latency_scales[10]` both fail intermittently on the py3.14 runner only | Retro v0.7.0 / PR #990 + Retro v0.7.2 / PR #1001 | #1016 | Closed | Shipped in v0.7.4 PR #1021 (merged as `6a4c0a58`). Two distinct fixes: (a) phase-based `time.time()` mock in test_hotreload (resilient to extra scheduler `loop.time()` calls on py3.14), (b) bumped 10ms→30ms dispatch budget for broadcast_latency_scales. No new deps. | Same class as #126 (filed during v0.7.0). py3.14 CI runner has different timing characteristics from py3.12/3.13 — wall-clock threshold assertions and warning-debounce timeouts hit the threshold occasionally on py3.14 only. Both tests (`test_broadcast_latency_scales[10]` from PR #990, `test_hotreload_slow_patch_warning` from PR #1001) passed on rerun. Options: (a) loosen thresholds with per-runner tolerance, (b) `@pytest.mark.flaky(reruns=2)` on timing-sensitive tests, (c) move py3.14 to non-required check until thresholds are recalibrated. Track next 3-4 py3.14 runs; if a third test joins the class, prioritize the fix. |
| 134 | PR review checklist reminder: when adding a framework-set attribute on `LiveView`/`LiveComponent`, also add it to `_FRAMEWORK_INTERNAL_ATTRS` | Retro v0.7.2 / PR #1002 / ADR-012 | #1017 | Closed | Shipped in v0.7.4 PR #1022 (bundled docs PR). One bullet under Code Quality > Architecture in `docs/PULL_REQUEST_CHECKLIST.md`. | Mitigation for ADR-012's accepted maintenance burden. The `_FRAMEWORK_INTERNAL_ATTRS` filter is the single source of truth for "this attr is internal"; a future framework attr added without the filter entry would re-introduce the v0.5.7 leak class. Not a CI gate (would be over-engineering for ~25 attrs that change rarely) — just a one-line bullet in `docs/PULL_REQUEST_CHECKLIST.md`. ~2 minutes to add; lock the mitigation that ADR-012 documented. |
| 135 | "Misleading existing tests" pattern — a check fix often requires UPDATING tests, not just adding new ones, when the pre-existing test fixture exemplifies the broken behavior the issue is about | Retro v0.7.3 / PR #1008 | #1018 | Closed | Shipped in v0.7.4 PR #1022 (bundled docs PR). Bullet in `docs/PULL_REQUEST_CHECKLIST.md` Test Quality section + canonical example documented in `docs/development/check-authoring.md`. | PR #1008's `test_c011_passes_when_output_exists` had been writing an 18-byte placeholder and asserting no C011 — exactly the bug #1003 was about. The test was wrong in a load-bearing way: it codified the broken behavior. Add a one-paragraph note to `docs/PULL_REQUEST_CHECKLIST.md` and PR review prompts: "if a check claims to test X but its test fixture exemplifies the broken behavior, updating the test is part of the fix." Locks against future "I added a new test but the existing one passes for the wrong reason" failures. |
| 136 | Whitespace-preserving redaction pattern for line-number-aware regex scanners | Retro v0.7.3 / PR #1014 | #1019 | Closed | Shipped in v0.7.4 PR #1022 (bundled docs PR). Documented in `docs/development/check-authoring.md` with `_strip_verbatim_blocks` (PR #1014) as canonical reference + fast-path note. | When a regex scanner needs to ignore a region of text (e.g. `{% verbatim %}` blocks for A070, or potentially future `{% comment %}` / `<script>` regions), replace the body with whitespace (preserve newlines) instead of stripping it. Line numbers from `match.start()` stay aligned with the original source for matches outside the region. Worth a one-paragraph note in the check-authoring guide as a reusable pattern; PR #1014 is the canonical reference implementation. |
| 137 | Scope-decision helpers belong as named functions, not inline branches | Retro v0.7.3 / PR #1015 | #1020 | Closed | Shipped in v0.7.4 PR #1022 (bundled docs PR). Documented in `docs/development/check-authoring.md` with `_contrast_check_scope` / `_presets_to_check` (PR #1015) as canonical reference + safe-default contract. | When a check's behavior depends on a config-driven scope (e.g. `_contrast_check_scope()` reading `DJUST_THEMING.contrast_check_scope`), extracting the decision into a named helper creates a clean test seam. PR #1015's `_contrast_check_scope()` and `_presets_to_check()` are the canonical examples — 4 small tests cover the four branches (default-active, opt-in-all, missing-preset, unknown-value) without dragging in the full Django settings stack. Worth a one-paragraph note in the check-authoring guide. |

### v0.7.0 milestone updates (2026-04-24)

- **#124 — Validated (partial).** Applied in PRs #989 and #990. PR #989: 5 rule tests RED-first caught real defects (log-level drift, A073 gate direction, LRU cap exact, generic-error split, cooperative cancel). PR #990: 9/9 fix-pass tests passed first-run AND 2 pulldown-cmark API surprises (ENABLE_HTML not suppressing Event::Html; ENABLE_GFM_AUTOLINK nonexistent) were caught ONLY because RED rule tests forced the investigation. Without #124's RED-first discipline, PR #990 would have shipped with raw-HTML XSS surface open. **Status: Validated for rule-level claims; partial for PR-body-headline claims** (subsumed for user-visible features by #125).
- **#125 — Validated (2 consecutive pipelines).** Introduced in PR #989's retro after 3 consecutive Stage-7-miss-Stage-11-catch pipelines (#986 JsonResponse, #988 fire-and-forget flush, #989 redirect-drop). Applied for the first time at Stage 7 of PR #990 — Stage 11 APPROVE with 0 🔴 (streak broken). Applied again at Stage 7 of PR #993 (v0.7.1) — Stage 11 APPROVE with 0 🔴 (second consecutive clean Stage 11). Two-PR correlation across two distinct milestones (v0.7.0 Rust+PyO3 markdown feature; v0.7.1 client-config JS-prefix feature). **Status: Validated (2 consecutive); track next pipeline for continued trend.**

### v0.7.1 milestone updates (2026-04-24)

- **#100 — Closed (`make ci-mirror`).** Shipped in PR #995. Mirrors the exact CI pytest invocation (same flags, same coverage thresholds, same selection set) as a single `make ci-mirror` target. Prevents coverage-threshold surprises that previously only surfaced in CI. ~90 LOC.
- **#101 — Closed (fakeredis swap).** Shipped in PR #996. Replaced hand-rolled in-memory Redis mock with `fakeredis` in `test_security_upload_resumable.py` — net −19 LOC AND accurate Redis semantics (real TTL, real key expiry, real connection-error path). Test-only refactor.
- **#123 — Closed (FORCE_SCRIPT_NAME for JS clients).** Shipped in PR #993 (recorded earlier today after PR-level retro).
- **#125 — Validated across 5 pipelines (status updated).** Streak now: PR #990 → #993 → #995 → #996 → #997 — all 0 🔴 at Stage 11 (PRs #995/#996 ran condensed pipeline-dev with no Stage 11 but no live-verify regressions either). The class of defect that plagued #976/#988/#989 (code does a thing, thing doesn't reach the user) has not recurred since #125 was filed. Discipline is empirically working.
- **#129 — New (Stage 4 engine-path checklist for template tags).** PR #993 shipped `{% djust_client_config %}` and originally registered the tag only with Django's template library. Pre-push pytest caught the issue: LiveView-rendered `base.html` is parsed by the Rust template engine, which didn't know the tag → 500. Fix mirrored the `djust_markdown` (PR #990) dual-registration pattern. Enforcement: Stage 4 plan template grows an "Engine path" bullet — for every new template tag, state explicitly which template engine(s) render the consuming templates. Second canonical dual-registration example (alongside `djust_markdown`) is `djust_client_config`.
- **#130 — New (closed same-day).** SSE FORCE_SCRIPT_NAME — same class of bug as #123, mirrored PR #993 pattern in PR #997. First-push clean merge. Template-reuse dividend: ~1/3 the engineering time of #993.
- **#131 — New (generalize #129).** "Engine path" Stage 4 bullet applies to any feature that touches template rendering (filters, context processors, post-processing hooks) — not just template tags.
- **#122 — Reinforced (post-commit verification).** PR #996 hit the pre-commit-hook stash/restore gotcha for the second time in the session (first was PR #989 at Stage 10): hook stashed+restored the working tree, ruff cleaned up an unused import, but the initial commit didn't register. Had to re-stage and retry. Action #122 (`git log -1 --oneline` post-commit verification) remains correctly filed; second occurrence reinforces priority.
- **Pre-push gate as last-line defense.** Stage 6 test subsets run Python + JS + Rust independently; the Python-side tag handler tests pass in isolation, but the demo views exercising the Rust engine aren't in the targeted set. The FULL pre-push pytest (as configured) runs the demo tests and caught the 500. Consider making Stage 6 explicitly invoke `make test` (not targeted subsets) for cross-engine/cross-language features — filing as a process note under #129.
- **pipeline-dev pattern empirically validated for tooling/test-only PRs.** PR #995 (Makefile target) and PR #996 (test-only refactor) both used the condensed pipeline-dev flow — no subagent reviews, no separate Stage 7/8/11 — and shipped clean. Don't invoke the full 14-stage pipeline for Makefile / dev-tooling / docs / test-only changes. Propose: update pipeline-run skill guidance to explicitly list what qualifies for pipeline-dev vs pipeline-run. Filing as Action #132.

---

| 138 | Stage 11 grep-adjacent-files checklist bullet | Retros: security/CodeQL arc | #1053 | Closed | **Resolved in v0.9.1 release arc** — Closed as won't-fix during v0.9.1-7 audit (redundant with v0.8.6 canon). New (reconcile 2026-04-25) |
| 139 | pipeline-retro Stage 2 (read per-PR retros) reinforcement | Retro v0.8.0 | #1054 | Open | New (reconcile 2026-04-25) |
| 140 | Stage 4 plan template — multi-PR milestone iter sequencing | Retro v0.8.0 | #1055 | Closed | **Resolved in v0.9.1 release arc** — PR #1226 (v0.9.1-7 canon batch). New (reconcile 2026-04-25) |
| 141 | Stage 4 plan template — 'API shape options considered' row | Retro v0.7.2 (addendum to Action #124) | #1056 | Closed | **Resolved in v0.9.1 release arc** — PR #1226 (v0.9.1-7 canon batch). New (reconcile 2026-04-25) |
| 142 | make roadmap-lint — automate ROADMAP-vs-codebase grep | Retro v0.5.0 feature rollout | #1057 | Open | New (reconcile 2026-04-25) |
| 143 | Ghost-branch drift mitigation in subagent prompts | Retro v0.5.0 feature rollout | #1058 | Closed | **Resolved in v0.9.1 release arc** — Already canon (closed during v0.9.1-7 audit). New (reconcile 2026-04-25) |
| 144 | Pre-existing test failure threshold — fix at ~10 | Retro v0.5.1 | #1059 | Closed | **Resolved in v0.9.1 release arc** — Closed as won't-fix during v0.9.1-7 audit (aspirational, not surfaced). New (reconcile 2026-04-25) |
| 145 | Dogfood pass for new CLI tools before commit | Retro v0.5.1 | #1060 | Open | New (reconcile 2026-04-25) | **Validated in v0.8.2 (PR #1074)** — Validated under fire — caught the dataclass vs dict bug at Stage 5 in PR #1074. Discipline empirically working; first real-world payoff. |
| 146 | Pre-push hook for `noqa: F822` in `__all__` patterns | Retro v0.7.0 | #1061 | Open | New (reconcile 2026-04-25) |
| 147 | Centralize tech-debt issue queue around Action Tracker rows | Retro 2026-04-25 reconcile | #1062 | Open | New (reconcile 2026-04-25) |
| 148 | RETRO_GATE_VIOLATION backfill — PRs #995, #996, #997 | Retro 2026-04-25 reconcile | #1063 | Closed | **Resolved in v0.9.1 release arc** — Closed as won't-fix during v0.9.1-7 audit (backfill window stale; subsumed by #1212). New (reconcile 2026-04-25) |
| 149 | Stage 4 re-classification — re-read cited code before assuming tracker classification | Retro v0.8.1 | #1070 | Closed | **Resolved in v0.9.1 release arc** — Already addressed by Stage 4 'VERIFY ARTIFACT' rule in PR #1218 (closed during v0.9.1-7 audit). New (5 instances this milestone) |
| 150 | Doc-claim TDD extends to prose docs with external references | Retro v0.8.1 / PR #1064 | #1071 | Closed | **Resolved in v0.9.1 release arc** — PR #1226 (v0.9.1-7 canon batch). New (Action #124 generalization) |
| 151 | Stage 1 branch-from-target reminder — even between drain groups | Retro v0.8.1 / PR #1068 | #1072 | Closed | **Resolved in v0.9.1 release arc** — Already canon (closed during v0.9.1-7 audit). New (Group F process error) |
| 152 | Lift-from-downstream FIRST pattern (Stage 4 plan-template) | Retro v0.8.2 / PR #1074 | #1077 | Closed | **Resolved in v0.9.1 release arc** — PR #1226 (v0.9.1-7 canon batch). New (prose.css canonical example) |
| 153 | Stage 11 mark_safe XSS-trace audit bullet | Retro v0.8.2 / PR #1074 | #1078 | Closed | **Resolved in v0.9.1 release arc** — PR #1226 + PR-checklist bullet. New (theme_css_link example) |
| 154 | Stage 4 broader-sweep → follow-up issue scope-discipline (validated 2x) | Retro v0.8.2 / PRs #1067, #1076 | #1079 | Closed | **Resolved in v0.9.1 release arc** — PR #1226 (v0.9.1-7 canon batch). New (validated across 2 milestones) |
| 155 | djust-release skill Step 6 stages 3/4 files (+ Cargo.lock); causes fix-pass on every release | Retro v0.8.2rc1 release | #1080 | Closed | **Resolved in v0.9.1 release arc** — PR-equivalent local skill fix during v0.9.1-6 (closed via gh issue comment with diff). New (caught during 0.8.2rc1 release) |
| 156 | Edit-tool failure-mode + smoke-test discipline gap | Retro v0.8.3 / PR #1083 | #1084 | Closed | **Resolved in v0.9.1 release arc** — Closed as won't-fix during v0.9.1-7 audit (Edit tool no longer fails silently). New (Edit-failure produces silent unmodified file; same shape as Action #122) |
| 157 | 3rd-strike RETRO_GATE_VIOLATION — small bookkeeping PRs bypass retro-artifact gate | Retro v0.8.3 / PRs #1069, #1073, #1082 | #1085 | Closed | **Resolved in v0.9.1 release arc** — Closed as won't-fix during v0.9.1-7 audit (de-facto practice). New (3 milestones in a row; document explicit ROADMAP-PR exemption or fix the gate) |
| 158 | Inheritance round-trip identity tests must drive from parser output, not direct AST construction | Retro v0.8.4 / PR #1086 | — | Open | New — `nodes_to_template_string`'s existing test passed because it built `Node::Variable` with bare-string args, bypassing `parse_filter_specs`'s "preserve outer quotes for dep-tracking" contract. PR #1086 added the parser-driven round-trip case. Generalize: every AST round-trip (inheritance / serialization / cache-rebuild) needs a "parse the source, round-trip, re-parse, assert AST equality" test, not an AST-equality-only test. |
| 159 | Stale-`collectstatic` Django system check (`djust.S0XX`) | Retro v0.8.4 / PR #1086 (red-herring trail) | #1088 | Closed | Shipped as `djust.C013` in v0.8.6 PR #1115 |
| 160 | Expand release wheel matrix to cp313 + cp314 | Retro v0.8.4 / PR #1086 | #1089 | Closed | Shipped in v0.8.6 PR #1115 (`.github/workflows/release.yml` matrix) |
| 161 | Debug-log when `\|date` / `\|time` filter parse fails | Retro v0.8.4 / PR #1086 | #1090 | Closed | Shipped in v0.8.6 PR #1115 (`tracing::debug!` in `Err` arm of `format_date`/`format_time`) |
| 162 | Demand bit-exact runnable repro before posting "root cause confirmed" on a multi-reopen issue | Retro v0.8.4 / PR #1086 (process) | — | Open | New — posted 3 "root cause" / "smoking gun" comments on #1081 based on framework-side theory testing; all three were wrong. The actual fix landed only after gaining direct project access. Process rule: on any issue with N≥2 reopens, refuse to post a root-cause claim without a runnable script that reproduces against the user's exact environment. Add to `pr/feedback/` triage checklist. |
| 163 | Split-foundation pattern for high-blast-radius features (canonicalize) | Retro v0.8.6 / View Transitions arc | #1122 | Open | Validated 3× now (View Transitions PR-A/PR-B, plus #1098 fix between). Add to CLAUDE.md / ADR template. |
| 164 | Pre-mount/post-mount keyset invariant test pattern (canonicalize) | Retro v0.8.6 / PR #1117 + PR #1119 | #1123 | Closed | **Resolved in v0.9.1 release arc** — Already canon in CLAUDE.md §v0.8.6 (closed during v0.9.1-7 audit). Generalizable to any context dict with default + runtime forms. Canonicalize in CLAUDE.md testing patterns. |
| 165 | CodeQL `js/tainted-format-string` self-review checkpoint | Retro v0.8.6 / PR #1120 | #1124 | Closed | **Resolved in v0.9.1 release arc** — Already canon in CLAUDE.md §v0.8.6 (closed during v0.9.1-7 audit). Caught by CodeQL post-CI; canonical safe pattern is `console.error('msg %s:', val, e)`, not template literals with user-controlled `${val}`. Add to CLAUDE.md JS-side patterns + Stage 7. |
| 166 | Bulk dispatch-site refactor + count-test pattern (canonicalize) | Retro v0.8.6 / PRs #1117 + #1120 | #1125 | Closed | **Resolved in v0.9.1 release arc** — Already canon in CLAUDE.md §v0.8.6 (closed during v0.9.1-7 audit). Pattern: many similar sites → one helper + a count-based test that catches future additions that forget the pattern. |
| 167 | v0.8.5 milestone retro never written | Retro v0.8.6 (backfill bookkeeping) | #1126 | Closed | Backfilled v0.8.5 entry in RETRO.md alongside the v0.8.6 retro session, 2026-04-26. |
| 168 | Stage-4 first-principles canonicalization in CLAUDE.md | Retro v0.9.0 / 3 of 6 PRs | #1143 | Closed | **Resolved in v0.9.4 via PR #1192** — CLAUDE.md "Process canonicalizations from v0.9.0 retro arc" section added with 5 concrete grep targets (wire-protocol, state-snapshot, async dispatch, decorator composition, component lifecycle) so Plan stages cite file:line. |
| 169 | Branch-name verify check in pipeline-run skill | Retro v0.9.0 / PR-A + PR-C drift | #1144 | Closed | **Resolved in v0.9.4 via PR #1192** — same CLAUDE.md section adds the pre-commit one-liner that compares `git symbolic-ref --short HEAD` against the active state file's `branch_name` field. Catches the silent wrong-branch-commit failure mode. |
| 170 | #1134 polluting-test bisect (HIGH-priority) | Retro v0.9.0 / 6 PRs | #1134 | Closed | **Resolved in v0.9.1 via PR #1159** — bisect identified two independent polluters (in-memory SQLite `aclose_old_connections` leak + `sys.modules` rebind in watchdog test). 6 flaky tests unskipped; 3 clean full-suite runs verified. |
| 171 | Rust template engine `{% live_render %}` tag handler (lazy=True parity) | Retro v0.9.0 / PR #1138 | #1145 | Closed | **Resolved in v0.9.1 via PR #1166** — lazy callback delegation via shared registry sidecar; new `call_handler_with_py_sidecar` bridge generic for future Rust-path tags. 8 parity tests. |
| 172 | A075 system check — sticky+lazy template scan | Retro v0.9.0 / PR #1138 deferral / ADR-015 §"Deferred from PR-B" | #1146 | Closed | **Resolved in v0.9.1 via PR #1163** — A075 check + verbatim-block guard (mirrors A070/A071 pattern); 8 regression tests. |
| 173 | CSP-nonce-aware activator for `<dj-lazy-slot>` fills | Retro v0.9.0 / PR #1138 deferral / ADR-015 §"Deferred from PR-B" | #1147 | Closed | **Resolved in v0.9.1 via PR #1163** — `request.csp_nonce` propagation through `live_render` lazy=True branch onto `<template>` + activator `<script>`. 6 Python + 3 JS regression tests. |
| 174 | Replay handler argument validation (defense-in-depth) | Retro v0.9.0 / PR #1142 Stage 11 | #1148 | Closed | **Resolved in v0.9.1 via PR #1164** — `is_event_handler(handler)` registry check after dunder guard. 3 regression tests in `TestReplayHandlerValidation`. |
| 175 | `markdown` package missing from default test env | Retro v0.9.0 (carryover from v0.8.7 retro) | #1149 | Closed | **Resolved in v0.9.1 via PR #1164** — `markdown` + `nh3` added to `[project.optional-dependencies.dev]`. |
| 176 | Descriptor-pattern component time-travel verification test | Retro v0.9.0 / PR #1141 Stage 11 | #1150 | Closed | **Resolved in v0.9.1 via PR #1164** — `TestDescriptorPatternComponentTimeTravel` (2 cases) added; descriptor auto-promotion gap noted as follow-up in #1165. |
| 177 | Debug panel UI for per-component scrubbing + forward-replay | Retro v0.9.0 / PR #1141 + PR #1142 follow-up | #1151 | Closed | **Resolved in v0.9.4 via PR #1193 (wire-protocol PR-A) + PR #1194 (debug panel UI PR-B)** — branch indicator badge, "X / max" event count, forward-replay button, per-component expand-toggle with sub-row scrubbers. CSP-strict throughout. 12 server-side cases + 23 client-side cases. |
| 178 | Vitest unhandled-rejection in `view-transitions.test.js` | Retro v0.9.0 / PR #1135 pre-push | #1152 | Open | Non-deterministic teardown error; audit test stubs against the v0.8.5 retro #1113 microtask-yield rule. |
| 179 | `asyncio.as_completed._wait_for_one` warning suppression | Retro v0.9.0 / PR #1138 Stage 11 | #1153 | Open | DeprecationWarning under teardown in tests/integration/test_chunks_overlap.py; either filter locally or fix `_cancel_pending` lifecycle. |
| 180 | Serialize implementer agents per checkout (parallel-agent CHANGELOG contamination) | Retro v0.9.1 / PR #1163 + #1164 | #1172 | Closed | **Resolved in v0.9.2** — applied directly to `~/.claude/skills/pipeline-run/SKILL.md` "One Implementer Agent Per Checkout" section. Validated across all 7 v0.9.2 PRs: zero CHANGELOG cross-contamination after enforcement. |
| 181 | Two-commit shape (impl+tests / docs+CHANGELOG) as canonical pipeline stage gate | Retro v0.9.1 / PRs #1166 + #1168 + #1170 | #1173 | Closed | **Resolved in v0.9.2 via PR #1176** — `.pipeline-templates/feature-state.json` Stage 5 forbids CHANGELOG; Stage 9 mandates docs-only. `bugfix-state.json` + `ship-state.json` symmetric. Validated on all 7 v0.9.2 PRs — Stage 11 reviewers verified clean splits. |
| 182 | "3 clean full-suite runs" verification gate for pollution-class fixes | Retro v0.9.1 / PR #1159 (#1134 bisect) | #1174 | Closed | **Resolved in v0.9.2 via PR #1176** — `.pipeline-templates/bugfix-state.json` Stage 6 mandates 3 clean full-suite runs when task description matches `/pollution\|leak\|flak\|test isolation/i`. Not exercised in v0.9.2 (no pollution-class fixes in the drain). |
| 183 | CSP-strict defaults for new client-side framework code (no inline scripts, no inline event handlers) | Retro v0.9.1 / PRs #1163 + #1170 | #1175 | Closed | **Resolved in v0.9.2 via PR #1178** — codified in CLAUDE.md ("Process canonicalizations from v0.9.1 retro arc" section) + `docs/PULL_REQUEST_CHECKLIST.md` ("CSP-Strict Defaults" subsection) + `docs/guides/security.md` ("CSP-Strict Defaults for Framework Code" section). 3-doc canonicalization. |
| 184 | "Closes #N, closes #M" each on its own body line (parenthesized comma-list silently fails) | Retro v0.9.2 / PR #1176 (self-defeating) | #1185 | Closed | **Resolved in v0.9.4 via PR #1192** — `docs/PULL_REQUEST_CHECKLIST.md` Closing-Keywords rule expanded to explicitly name the parenthesized form `(closes #X, closes #Y)` as a known auto-close failure mode and recommend PR body over title. |
| 185 | Refactor-with-helper guard audit pattern | Retro v0.9.4 / PR #1194 | #1195 | Closed | **Resolved in v0.9.1 release arc** — PR #1225 (v0.9.1-6 v0.9.4 retro process canon batch). When extracting a helper from N call sites with inline input-validation logic, audit each call site to decide where validation lives. PR #1194 inadvertently dropped a `typeof index !== 'number'` guard when routing through `_sendTimeTravelMessage`. Failure mode is silent — production code keeps working when inputs are well-formed. |
| 186 | Delegated-listener integration test pattern | Retro v0.9.4 / PR #1194 | #1196 | Closed | **Resolved in v0.9.1 release arc** — PR #1225 (v0.9.1-6 v0.9.4 retro process canon batch). For any "marker class + delegated listener" feature, unit tests (direct method) and integration tests (real DOM event → handler → method) need separate coverage. PR #1194's first version had 17 method-level cases but ZERO integration tests. Stage 11 caught it; backfill added 6 integration cases. |
| 187 | Canon-doc citation discipline (grep-verify before commit) | Retro v0.9.4 / PR #1192 | #1197 | Closed | **Resolved in v0.9.1 release arc** — PR #1225 (v0.9.1-6 v0.9.4 retro process canon batch). Every `file:line` / attribute name / bash one-liner cited in a canon doc (CLAUDE.md, PR-checklist, ADR) should be `grep`-verified. PR #1192 had 5 inaccuracies in a 3-rule docs PR. Pre-empt Stage 11 by running greps in self-review. |
| 188 | Commit-or-rollback handler shape | Retro v0.9.4 / PR #1193 | #1198 | Closed | **Resolved in v0.9.1 release arc** — PR #1225 (v0.9.1-6 v0.9.4 retro process canon batch). Async handlers with both state mutation AND early-return paths must mutate AFTER the commit point. PR #1193's `handle_forward_replay` set `branch_id = new_branch` before awaiting `replay_event`; on `replayed is None`, branch state stayed bumped with no recorded events. View+client diverged silently. |
| 189 | Edge-case coverage for index/cursor logic | Retro v0.9.4 / PR #1193 | #1199 | Closed | **Resolved in v0.9.1 release arc** — PR #1225 (v0.9.1-6 v0.9.4 retro process canon batch). When implementing handlers with index/cursor logic, run cases at `index=0`, `len/2`, `len-1`, `len`. PR #1193 had an off-by-one between `_build_time_travel_state` and `handle_forward_replay`'s gate that disagreed at `cursor=len-1`. Mental trace catches it; the four-boundary discipline catches it before Stage 11. |
| 190 | Tautology test detection | Retro v0.9.4 / PR #1190 | #1200 | Closed | **Resolved in v0.9.1 release arc** — PR #1225 (v0.9.1-6 v0.9.4 retro process canon batch). When a test asserts "this thing happened", check whether the assertion would pass if the action did nothing. PR #1190's `test_ready_completes_other_setup` asserted `any(isinstance(filters, DjustLogSanitizerFilter))` but every prior test populates the logger; the assertion was tautological. Fix pattern: snapshot count before, assert grew by exactly 1. |
| 191 | Reproducer-first discipline canonicalized in plan-template Stage 4 | Retro v0.9.5 / PRs #1206 + #1201 | #1210 | Closed | **Resolved in v0.9.1 release arc** — PR #1218 (v0.9.5 Stage 4 reproducer-required). Bugfix plans must require a failing reproducer test before lock-in; security plans must require reading actual code at the alert-cited location. ~10 min wasted in PR #1206 chasing reporter-cited dead code; ~2 min saved in PR #1201 by reading alert lines first. Same discipline, different surface. |
| 192 | Reviewer-prompt budget guidelines for pipeline-run Stage 11 | Retro v0.9.5 / PR #1201 | #1211 | Closed | **Resolved in v0.9.1 release arc** — PR #1219 (v0.9.5 reviewer-prompt budget). Long-running reviewer-agent prompts hit watchdog stalls; tight short-prompt reviewers find real gaps. Cap security-PR review prompts at 200 words, feature at 350, bugfix at 250. Forbid edge-case spelunking beyond the documented attack-shape list. PR #1201 reviewer stalled at 10-min watchdog mid-tangent on backslash-injection. |
| 193 | Audit pipeline-bypass merges and harden retro-gate against silent dropout | Retro v0.9.5 / PRs #1203 + #1204 | #1212 | Closed | **Resolved in v0.9.1 release arc** — PR #1229 (v0.9.1-8 audit script — part 1; ongoing CI deferred to #1234). PR #1203 and PR #1204 merged without retro comments — pipeline-run Stage 14 retro-artifact gate didn't fire (likely because the operator merged manually outside the pipeline). Audit recent merged PRs against `.pipeline-state/*.json`; consider scheduled CI check that flags merged PRs without `Quality:`/`Lessons learned:`/`RETRO_COMPLETE` markers. |
| 194 | "Bug-report triage" section in CLAUDE.md citing PR #1206 as case study | Retro v0.9.5 / PR #1206 | #1213 | Closed | **Resolved in v0.9.1 release arc** — PR #1216 (v0.9.5 Bug-report triage CLAUDE.md section). Generalizes the "issue-reporter analysis ≠ root cause" lesson from PR #1206. Reporter pointed at `_lazy_serialize_context` — a dead-code method whose `str(model)` fallback exactly matched the reported `__str__` symptom but had zero call sites. Trace from observable symptom to actual code path; don't trust path-down hypotheses. |
| 195 | Heterogeneous and nested `list[Model]` shapes in change-detection normalize pass | PR #1206 review (post-merge) | #1207 | Closed | **Resolved in v0.9.1 release arc** — PR #1223 (v0.9.1-6 expansion to heterogeneous + nested shapes). Defensive normalize pass added in PR #1206 covers homogeneous `list[Model]` only. `[dict, Model]` (Model not first) and `list[list[Model]]` still escape change detection. Fix: scan full list for any Model + recurse into nested lists with depth bound. |
| 196 | Strengthen idempotency test with explicit zero-patch assertion | PR #1206 review (post-merge) | #1208 | Closed | **Resolved in v0.9.1 release arc** — PR #1217 (v0.9.5 idempotency-test strengthening). `test_normalize_idempotent_on_already_serialized` asserts no exception. Should also assert `dom_changes` count is 0 on noop event to lock idempotency contract tightly. ~15 min effort. |
| 197 | Vulture-based pre-push check for unused private methods | PR #1206 retro IDEA | #1209 | Closed | **Resolved in v0.9.1 release arc** — PR #1220 (v0.9.5 vulture-based pre-push check). `_lazy_serialize_context` was dead code with `str(model)` fallback that misled the issue reporter. A vulture-based linter would catch unused private methods at PR time. Whitelist framework-hook patterns + reflection-called methods. |
| 198 | CodeQL query model declaring `sanitize_for_log` as sanitizer | Retro v0.9.5 / PR #1201 (carried) | #1214 | Closed | **Resolved in v0.9.1 release arc** — PR #1224 (v0.9.1-6 CodeQL canonical sanitize_for_log path). Every `dispatch.py` log call trips `py/log-injection` because CodeQL doesn't model `sanitize_for_log` as sanitizer. Ship `.github/codeql/sanitizers.qll` (or equivalent suite-override) declaring `djust.security.sanitize_for_log` as a sink-clearing function. Compounds across every future security sweep. |
| 199 | Pre-commit `mixed-line-ending` cleanup of two `.pxd` files | Retro v0.9.5 / PR #1201 (carried) | #1215 | Closed | **Resolved in v0.9.1-6 via PR #1222** — exclude `.pxd` from `mixed-line-ending`/`trailing-whitespace`/`end-of-file-fixer` in `.pre-commit-config.yaml`. Files are binary ZIP archives misclassified as text by `identify`. |
| 200 | Pipeline-bypass CI check (ongoing) — flag merged PRs without retro markers within 24h | Retro v0.9.1 release / Carryover from #1212 part 2 | #1234 | Closed | **Resolved in v0.9.2-1 via PR #1241** — `.github/workflows/retro-gate-audit.yml` runs daily at 13:00 UTC, calls `scripts/audit-pipeline-bypass.py --limit 50`, surfaces flagged PRs as workflow annotations. Manual `workflow_dispatch` for ad-hoc runs. |
| 201 | Isolated cargo-test target for `filter_registry::tests` | Retro v0.9.1 release / Carryover from #1180 item 4 | #1235 | Closed | **Resolved in v0.9.2-1 via PR #1241** — moved to `crates/djust_templates/tests/test_filter_registry_isolated.rs` (Cargo runs each integration-test file in its own process; the `OnceLock` workaround is no longer needed). |
| 202 | Watch-list for release-workflow-touching dep bumps | Retro v0.9.1 release / PR #1233 retro | #1236 | Closed | **Resolved in v0.9.2-1 via PR #1241** — `.github/workflows/check-release-workflow-deps.yml` requires the `release-workflow-reviewed` label on PRs modifying release-critical workflow files; label was created via `gh label create`. |
| 203 | Stage 4 plan-template item: verify literal API names/kwargs/return-shapes against actual contracts before locking the plan | Retro v0.9.2-1 / PR #1242 | #1243 | Closed | **Resolved in v0.9.2-2 via PR #TBD** — added "VERIFY LITERAL API CONTRACTS" mandatory checklist item to Stage 4 in both `.pipeline-templates/feature-state.json` and `bugfix-state.json`. |
| 204 | Stage 7 self-review item: cross-ref new workflow files' header-comment claims against actual step semantics | Retro v0.9.2-1 / PR #1241 | #1244 | Closed | **Resolved in v0.9.2-2 via PR #TBD** — added "WORKFLOW-HEADER CROSS-REF" mandatory checklist item to Stage 7 in both `.pipeline-templates/feature-state.json` and `bugfix-state.json`. Triggers when changed files include `.github/workflows/*.yml` or any file with a runtime-behavior docstring. |
| 205 | Pipeline-run Stage 14 retro-post: use Write tool + `gh --body-file` (not bash heredoc + `--body "$(cat ...)"`) | Retro v0.9.2-1 / PRs #1239 #1241 #1242 (all 3 hit it) | #1245 | Closed | **Resolved in v0.9.2-2 via PR #1246** — Stage 14 subagent_prompt in both `.pipeline-templates/{feature,bugfix}-state.json` instructs Write tool + `--body-file` + retro-marker regex verification. Self-validated: PR #1246's own Stage 14 retro used the new pattern. |
| 206 | Stage 7 self-review item: "self-applicability check" for canon PRs | Retro v0.9.2-2 / PR #1247 | #1248 | Open | New. PR #1247's Stage 11 reviewer asked "would the new Stage 4/Stage 7 check have flagged anything in this PR's diff?" — answer was no (no workflow files / no docstrings), correctly. Worth elevating as a routine Stage 7 question for all canon-class PRs: would the new rule have caught the originating bug? would it false-positive on the canon PR itself? Both should be explicitly answered. |
| 207 | Single-source-of-truth pattern for multi-consumer regexes (extract to shared module) | Retro v0.9.2-2 / PR #1246 | #1249 | Open | New. The retro-marker regex (`retrospective\|quality:\\s*\\d\|lessons\\s+learned\|retro_complete\|what\\s+went\\s+well`) is currently defined in two places: `scripts/audit-pipeline-bypass.py:38-39` and the Stage 14 `subagent_prompt` text. Same pattern, two consumers — could drift. Extract to a shared constant (e.g., `scripts/lib/retro_markers.py`) that both import. Same shape applies to other multi-consumer regexes (commit-keyword pattern used by comma-list-Closes lint + auto-close GitHub parser). |
| 208 | Direct-to-main commits bypass the retro-gate audit (audit only scans merged PRs) | Retro v0.9.2-2 / commit 18e5b117 | #1250 | Open | New. The daily retro-gate audit GHA (#1234, shipped v0.9.2-1) scans merged PRs via `gh pr list --state merged`. The v0.9.2-2 milestone-open commit `18e5b117` was a direct push to main (per the pipeline-drain skill's literal instruction to "git commit + git push origin main" in Step 7). It bypassed /pipeline-next, /pipeline-run, and the audit. Two fixes: (a) extend the audit script to also scan direct-to-main commits since the last merged PR, OR (b) update the pipeline-drain skill to always go through a docs PR rather than direct-pushing the ROADMAP update. (b) is more defensible: branch-protection on main with required-PR-review covers it mechanically. |
| 209 | `git add <file>` bundles pre-existing uncommitted modifications without warning | Retro v0.9.2-2 / pipeline-skill CANON.md attempt (commit `bf1a67f`) | #1251 | Open | New. When the pipeline-skill `CANON.md` work was committed, `git add skills/pipeline-run/SKILL.md` staged both my intended 5-line cross-link AND ~130 lines of pre-existing uncommitted canon work in the same file. Bundling was detected only post-commit via `git log -1 --stat` (the diff was 136+/2− vs my expected 5+). Mitigation options: (a) pre-commit gate that fails when staged content of any file diverges from the line-ranges named in the commit message, (b) reflex of running `git diff --cached --stat` before every `git commit` and verifying the line counts match expectation, (c) `git add -p` interactively when the file has pre-existing uncommitted modifications. (b) is lowest-friction and worth canonicalizing as a Stage 5/Stage 9/Stage 10 mandatory item. |

---

## v0.9.2-2 — Pipeline-template canon batch (PRs #1246, #1247)

**Date**: 2026-04-30
**Scope**: Second drain bucket toward v0.9.2 release. Three issues closed across 2 PRs in ~30 minutes wall-clock. All 3 are pipeline-template canon additions (in the new sense documented in pipeline-skill repo's `CANON.md`):

- #1245 (PR #1246) — Stage 14 retro-post pattern: Write tool + `gh --body-file` (replaces bash heredoc + `$(cat ...)` subshell that silently failed under zsh `set -o noclobber`).
- #1243 (PR #1247) — Stage 4 VERIFY LITERAL API CONTRACTS (pattern from #1240/#1242 spec/convention drift).
- #1244 (PR #1247) — Stage 7 WORKFLOW-HEADER CROSS-REF (pattern from #1241 `pipefail` vs header-claim mismatch).

Plus a direct-to-main milestone-open commit (`18e5b117`) that bypassed /pipeline-next + /pipeline-run.

**Tests at close**: 4842 Python (unchanged from v0.9.2-1; this milestone touched only `.pipeline-templates/*.json` + docs). 1492 JS (unchanged). Pipeline-template canon doesn't affect the test suite.

### What We Learned

**1. Self-applying canon — eat the dog food on the merge that lands it.**

PR #1246 used Write tool + `gh --body-file` for its own Stage 14 retro post — the very pattern it canonicalizes. PR #1247's Stage 11 review and Stage 14 retro both used the pattern from #1246. This is a healthy invariant: if the new pattern can't be used for the canon PR's own workflow, the pattern isn't ready. A canon PR that fails to self-apply is the strongest signal of an over-engineered or misshapen rule.

**Action taken**: Closed — pattern demonstrated and self-validated end-to-end across both PRs in this milestone. No follow-up needed; the invariant is now visible in two consecutive canon PRs.

**2. Self-applicability as a routine canon-PR question.**

PR #1247's Stage 11 reviewer explicitly asked "would the new Stage 4 / Stage 7 check have flagged anything in this PR's diff?" — answer was no (no workflow files, no docstrings; the templates are JSON not workflows). The check correctly excludes itself. Worth elevating to a Stage 7 self-review item for ALL canon-class PRs: would the new rule have caught the originating bug at the stage it adds? Would the new rule false-positive on the canon PR itself? Both should be explicitly answered before merge.

**Action taken**: Open — tracked in Action Tracker #206 (GitHub #1248).

**3. Single-source-of-truth pattern for multi-consumer regexes.**

The retro-marker regex (`retrospective|quality:\s*\d|lessons\s+learned|retro_complete|what\s+went\s+well`) is now defined in two places: `scripts/audit-pipeline-bypass.py:38-39` and the Stage 14 `subagent_prompt` text in both pipeline templates. Same regex, two consumers — they can drift. The same shape applies to other multi-consumer regexes (e.g., commit-keyword pattern used by the comma-list-Closes lint AND the GitHub auto-close parser). Worth extracting to a shared constants module so updates land atomically.

**Action taken**: Open — tracked in Action Tracker #207 (GitHub #1249).

**4. Direct-to-main commits bypass the retro-gate audit.**

The v0.9.2-2 milestone-open commit (`18e5b117`) was a direct push to main — bypassing /pipeline-next, /pipeline-run, and (importantly) the daily retro-gate audit GHA from #1234. The audit scans merged PRs via `gh pr list --state merged`; direct commits to main aren't surfaced. Per the pipeline-drain skill's literal Step 7 instruction ("git commit + git push origin main"), this bypass is by design — the skill expects ROADMAP updates to ride direct on main. But the new audit infrastructure makes that expectation outdated.

**Action taken**: Open — tracked in Action Tracker #208 (GitHub #1250).

**5. `git add <file>` silently bundles pre-existing uncommitted modifications.**

While shipping the pipeline-skill CANON.md doc (commit `bf1a67f`, separate repo), `git add skills/pipeline-run/SKILL.md` staged both my intended 5-line cross-link AND ~130 lines of pre-existing uncommitted canon work that had been sitting in the working tree. The bundling was invisible until `git log -1 --stat` showed the actual diff (136+/2−) vs my expected (5+). The commit message understated the content. Mitigation: a pre-commit reflex of `git diff --cached --stat` immediately before every `git commit` to verify the line counts match expectation. Cheap, mechanical, would have caught this in 5 seconds.

**Action taken**: Open — tracked in Action Tracker #209 (GitHub #1251).

### Insights

- **Wall-clock per canon PR**: 10 min (#1246) and 15 min (#1247). Canon PRs are the highest LoC-efficiency intervention in the pipeline — small diffs, no test-writing burden (templates aren't tested), instant downstream benefit on every future PR.
- **Two-commit shape held cleanly across both PRs.** `7d426f9c`+`c2b3f844` for #1246, `e95750e3`+`badf77ec` for #1247. Plus #1246 had a third Stage-11-finding-fix commit (`1cda17f2`) which is the protocol-correct shape, not a violation.
- **Stage 11 was clean on both PRs** (0 🔴, 0 🟡 on #1246 after the inline `1cda17f2` fix; 0 🔴 0 🟡 on #1247). High signal-to-noise on the first review pass.
- **The pipeline-skill repo doc work** (`CANON.md` and the canon-venue framing) is in flux — uncommitted on a feature branch in a separate repo, with a bundling issue (Finding 5) that needs resolution. Cross-repo canon work tracking is currently undocumented; the user is the operator-of-record for both repos so coordination is informal.
- **"Pipeline-template canon" as a recognized category** is now self-aware: the term has a definition (in pipeline-skill repo's draft CANON.md), an enforcement venue (mandatory checklist items + subagent_prompt text), and a small but growing portfolio of examples (#1245, #1243, #1244 are the v0.9.2-2 entries; #181/#180/#1177 are earlier examples).

### Review Stats

| Metric | PR #1246 | PR #1247 | Total |
|---|---|---|---|
| Commits | 3 (impl + docs + Stage-11-fix) | 2 (impl + docs) | 5 |
| LoC added | 30 | 36 | 66 |
| LoC deleted | 4 | 2 | 6 |
| Tests added | 0 (template-only) | 0 (template-only) | 0 |
| 🔴 Findings | 0 | 0 | 0 |
| 🟡 Findings | 1 (audit-script exit propagation) | 0 | 1 |
| 🟢 Findings | 3 | 0 | 3 |
| Findings fixed in PR | 1 (commit `1cda17f2`) | 0 (none to fix) | 1 |
| CI failures | 0 | 0 | 0 |

### Process Improvements Applied

**CLAUDE.md**: no changes this milestone (canon went into pipeline templates, not project CLAUDE.md).

**Pipeline templates**: 4 changes shipped this milestone in `.pipeline-templates/{feature,bugfix}-state.json` symmetrically:
- Stage 14 `subagent_prompt`: Write tool + `--body-file` + retro-marker verification (PR #1246, closes #1245).
- Stage 4 mandatory checklist: VERIFY LITERAL API CONTRACTS (PR #1247, closes #1243).
- Stage 7 mandatory checklist: WORKFLOW-HEADER CROSS-REF (PR #1247, closes #1244).

**PR-checklist**: no changes this milestone.

**Skills**: no changes to `~/.claude/skills/pipeline-*` directly. However, draft documentation work in the upstream `pipeline-skill` repo (`CANON.md` + cross-links from README/CLAUDE/pipeline-run-SKILL) is in flux on branch `feat/retro-state-file-gate`, commit `bf1a67f`, with a bundling issue noted in Finding 5.

### Open Items

- [ ] Stage 7 self-applicability check for canon PRs — Action Tracker #206 (GitHub #1248)
- [ ] Extract retro-marker regex to shared constants module — Action Tracker #207 (GitHub #1249)
- [ ] Extend retro-gate audit to scan direct-to-main commits OR migrate ROADMAP updates to PR-only — Action Tracker #208 (GitHub #1250)
- [ ] Pre-commit `git diff --cached --stat` reflex to catch silent-bundle commits — Action Tracker #209 (GitHub #1251)
- [ ] Resolve pipeline-skill `CANON.md` bundling decision (Option A/B/C from session conversation) — separate repo, no tracker row needed

---

## v0.9.2-1 — SSE transport DRY refactor + tracker carryovers (PRs #1238, #1239, #1241, #1242)

**Date**: 2026-04-30
**Scope**: First drain bucket toward v0.9.2 release. Headlined by #1237 (3 SSE-transport bugs fixed via a transport-agnostic `ViewRuntime` + `Transport` Protocol per ADR-016). Three v0.9.1 retro carryovers (#1234 retro-gate audit GHA, #1235 cargo-test isolation, #1236 release-deps label gate) bundled in one chore PR. One immediate Stage 11 follow-up (#1240 use_actors error envelope) shipped within the same milestone window.
**Tests at close**: 4842 Python (was 4803 at v0.9.1 close; +39 new across `test_runtime.py`, extended `test_sse.py`, new `test_sse_ws_symmetry.py`, +1 use_actors guard test). 1492 JS (unchanged from v0.9.1 close minus minor adjustments — net +6 new SSE tests). Full suite green pre-merge on every PR.
**Wall-clock**: ~3.5 hours from milestone open (PR #1238) to last merge (PR #1242).

### What We Learned

**1. 🟡 plan-fidelity findings → separate small follow-up PR (preserves audit trail).**

PR #1239's Stage 11 review classified the missing `use_actors` error envelope as 🟡 not 🔴 — a documented plan promise (ADR-016 §Implementation / plan §Risks #3) that the implementer skipped. We filed #1240, shipped PR #1242 with the 5-line guard + 1 test, and closed it within the same milestone. This is a healthier pattern than amend-and-force-push because: (a) the audit trail stays linear (Stage 11 review → reviewer classification → follow-up issue → follow-up PR); (b) the reviewer's signal is preserved across PRs; (c) the original PR's commit hash stays stable, so rebasing/blaming downstream consumers don't see history rewrites.

**Action taken**: Closed — pattern demonstrated and validated end-to-end in the #1239 → #1240 → #1242 sequence. No skill update needed; the existing pipeline-run flow already supports this shape (the only "rule" being to file the follow-up as its own state file rather than amend the merged PR).

**2. Spec snippets in plans can be wrong; verify literal API contracts before pasting.**

The plan for #1240 (in the state-file `task_description`) literally said `transport.send_error(..., type="mount_error")`. Existing djust convention (`websocket.py:1835/1891/2116/2133/2217`) is `error_type=` — and `type=` would have collided with the outer envelope's `{"type": "error", ...}` shape. The implementer correctly followed convention rather than the spec snippet, and the Stage 11 reviewer flagged this as informational. But it's a class of failure mode that bites silently when the implementer trusts the spec verbatim. The Stage 4 plan-template should grow a "verify literal API contracts" checklist item.

**Action taken**: Open — tracked in Action Tracker #203 (GitHub #1243).

**3. Stage 11 review finds runtime-semantics issues that pre-flight reviews can't.**

PR #1241's Stage 11 reviewer caught a `pipefail` interaction with the audit script's exit code that would have made the daily retro-gate cron go red on every flagged PR — directly contradicting the workflow's own header-comment intent ("annotations not red runs"). This wasn't a syntax issue, an injection issue, a permissions issue, or a missing-test issue — it was a behavioural contract mismatch between the implementation and the header docstring. Stage 7 (self-review) and Stage 8 (security check) wouldn't have caught it because both look at structural properties. Stage 11's "run the implementation through your head end-to-end" is what found it. Validates the rule that **Stage 11 must never be skipped, even on chore-class PRs**.

**Action taken**: Closed — reinforces existing canon (Stage 11 non-skip rule, already in `~/.claude/skills/pipeline-run/SKILL.md`). The new tracker row #204 (Stage 7 cross-ref of workflow header claims) is the proactive guard against this specific shape; Stage 11 stays load-bearing as the safety net.

**4. Workflow header-comment claims are a behavioural contract; cross-ref them in Stage 7.**

The retro-gate-audit.yml's header said "annotations not red runs"; the implementation made the runs red whenever a PR was flagged. The reviewer caught it, and we shipped a follow-up commit (`1cda17f2`) with `set +o pipefail` around the audit-script call. **Pattern**: when a workflow file has a header docstring describing intent, Stage 7 should grep the docstring for behavioural claims and verify each one against the actual step semantics. Not just for workflows — same shape for any file whose docstring describes runtime behavior.

**Action taken**: Open — tracked in Action Tracker #204 (GitHub #1244).

**5. Daily retro-gate audit closes the docs-PR pipeline-bypass gap (meta-validation).**

PR #1238 (the docs PR that opened this milestone) was opened directly via `git checkout -B` + `gh pr create`, NOT via `/pipeline-next` + `/pipeline-run`. No state file, no Stage 14 retro post until this retro's backfill. This is exactly the failure mode the new daily retro-gate audit (PR #1241, #1234) is designed to catch — and it'll catch #1238 on its first run. **The audit shipped in this milestone validates itself** by surfacing a real bypass from the same milestone.

**Action taken**: Closed — the daily retro-gate audit GHA is the answer; #1241 shipped it; the meta-validation here confirms it works.

### Insights

- **Two-commit shape (Action #181) held cleanly across all 4 PRs.** Every PR had a clear `feat:`/`fix:`/`chore:`/`docs:` impl commit followed by a `docs:` CHANGELOG/docs commit. Total of 9 commits (1 docs + 4 PR commits + 4 stage-9 docs commits + 1 stage-11-finding-fix commit on PR #1241), zero CHANGELOG cross-contamination, zero force-pushes to merged branches.
- **Single-implementer-one-checkout (Action #180) held across 4 sequential PRs.** No parallel implementer agents on the same checkout; PRs #1239 → #1241 → #1242 ran serially, with #1238 (docs) sequenced before #1239 to unblock the relative MD link.
- **Cross-PR doc-link dependency caused a pre-push lint hit but resolved cleanly.** PR #1239's `docs/sse-transport.md` referenced `docs/adr/016-transport-runtime-interface.md` via relative path; that file existed only on PR #1238's branch. The pre-push `docs stale-MD-ref check` lint correctly flagged the broken reference. Sequencing #1238 first (merge), then push #1239 (with rebase) was the right answer. Worth noting: this is the kind of dependency that future cross-PR docs-link work should plan for.
- **Wall-clock per PR was ~30-45 minutes for #1239 (the largest)** down to ~10 minutes for #1242 (the smallest). The bulk of the time was the implementer-subagent run for #1239 (~18 minutes for 1100 LOC including tests). Pipeline-next overhead is genuinely small (~2 minutes per pipeline) — most of the time is in the actual code-write stages.
- **Convergent finding from parallel reviews**: PR #1239's Stage 7 + Stage 8 both independently flagged the same SW-buffering issue (33-sw-registration.js patching `sendMessage` unconditionally on SSE instances). Same finding from different angles → genuine signal. Validates the parallel-stage rule (Stages 6/7/8 run together).

### Review Stats

| Metric | PR #1238 | PR #1239 | PR #1241 | PR #1242 | Total |
|---|---|---|---|---|---|
| Commits | 1 | 2 (+1 amend) | 3 (impl+docs+11-fix) | 2 | 9 |
| LoC added | 277 | 2170 | 251 | 87 | 2785 |
| LoC deleted | 4 | 57 | 80 | 5 | 146 |
| Tests added | 0 | 24 | 2 | 1 | 27 |
| 🔴 Findings | n/a (backfill) | 0 | 0 | 0 | 0 |
| 🟡 Findings | n/a | 1 | 1 | 0 | 2 |
| 🟢 Findings | n/a | 2 | 3 | 0 | 5 |
| Findings fixed in PR | n/a | 0 (deferred to #1242) | 1 (commit 1cda17f2) | n/a | 1 |
| Findings deferred to follow-up | n/a | 1 (#1240) | 0 | 0 | 1 |
| CI failures | 0 | 0 | 0 | 0 | 0 |

### Process Improvements Applied

**CLAUDE.md**: no changes this milestone (the v0.9.1 retro arc canonicalized 5 process rules; this milestone was about applying them).

**Pipeline template**: no changes this milestone. Two-commit shape (#181), single-implementer-per-checkout (#180), 3-clean-runs gate (#182), CSP-strict defaults (#183) all already canonical from v0.9.1.

**Checklist**: no changes this milestone.

**Skills**: no changes this milestone. Tracker rows #203 (Stage 4 plan-template literal-API verification) and #204 (Stage 7 workflow-header-claim cross-ref) are open and will land as skill-template additions in a follow-up.

### Open Items

- [ ] Stage 4 plan-template addition: verify literal API contracts — Action Tracker #203 (GitHub #1243)
- [ ] Stage 7 self-review addition: cross-ref workflow header claims against step semantics — Action Tracker #204 (GitHub #1244)

---

## v0.9.1 — Release retro (8 drain buckets, 39 PRs, post-v0.9.0 GA polish + backlog clean)

**Date**: 2026-04-30
**Scope**: First release under the new milestone-naming convention (`vX.Y.Z` = release, `vX.Y.Z-N` = drain bucket). v0.9.1 packages 8 drain buckets shipped between the v0.9.0 GA bump (2026-04-29) and the v0.9.1 tag (2026-04-30T17:47Z): `v0.9.1-1` through `v0.9.1-5` were named `v0.9.1`/`v0.9.2`/`v0.9.3`/`v0.9.4`/`v0.9.5` under the old scheme; `v0.9.1-6`, `v0.9.1-7`, `v0.9.1-8` shipped under the new scheme directly. Plus a post-cleanup of 3 PRs (#1231 rich_select dedup, #1232 dup-constant Stage 7 rule, #1233 action-gh-release v3 bump) and the version-bump commit itself.
**Tests at close**: 6666 Python tests collected + ~1486 JS = ~8150 total. 4661 Python pass on unit + integration sweep; 14 skipped. Full suite green pre-tag.
**Release artifacts**: GitHub Release published 2026-04-30T17:55:15Z, 18 PyPI artifacts (1 sdist + 17 wheels: Linux x86_64/aarch64, macOS Intel/ARM, Windows; py3.12/3.13/3.14). PyPI: https://pypi.org/project/djust/0.9.1/

### What We Learned

**1. Reproducer-first discipline is now structural, not aspirational.**
PR #1206 burned ~10 min in Stage 4 chasing the issue reporter's claimed bug location (`_lazy_serialize_context` in `python/djust/mixins/jit.py`) before writing a `LiveViewTestClient` reproducer that surfaced the actual code path. The dead-code fallback method literally contained a `str(model)` call matching the reported symptom — a near-perfect misdirection. Same pattern surfaced from a different angle in PR #1201: 8 `py/log-injection` alerts in `dispatch.py` looked like real bugs until "two minutes of `sed -n '${line}p'`" revealed they all already used `sanitize_for_log()` and were FPs. PR #1218 made the discipline a Stage 4 mandatory checklist item: bug plans require a failing reproducer test; security plans require reading actual code at the alert-cited location.

**Action taken**: Closed — canonicalized as Stage 4 leading mandatory item in `.pipeline-templates/feature-state.json` and `bugfix-state.json` via PR #1218 (closes #1210). Verified self-applying in PR #1223 (#1207 list[Model] expansion) which followed the new gate cleanly.

**2. The two-commit shape held under one-implementer-one-checkout discipline.**
v0.9.1's 23+ pipeline-run PRs all kept implementation-and-tests separate from CHANGELOG-and-docs (Action Tracker #181 / GitHub #1173 / PR #1176 canon). Zero CHANGELOG cross-contamination across the entire arc. Validated specifically: PR #1206 used commits `2212dff4` (impl + tests) + `cfb118ea` (CHANGELOG); PR #1218, PR #1219, PR #1220, PR #1223 all followed identical shape. The serialization rule (one implementer per checkout) is the load-bearing precondition; the v0.9.1 retro arc canon is correct.

**Action taken**: Closed — pattern is canonicalized in `.pipeline-templates/feature-state.json` Stage 5/9 + `bugfix-state.json` Stage 5/9 since v0.9.1-2 PR #1176. PR #1230's auto-format-touched-only-Cargo-fmt commit was the closest call to a violation; no actual cross-contamination.

**3. Comma-list close-keyword silent failure → programmatic enforcement.**
GitHub's auto-close parser only matches a closing keyword when it precedes EACH issue ref. Bare comma-list `Closes #X, #Y, #Z` parses as ONLY closing `#X`. Bit twice in 24 hours during the v0.9.1-6 → v0.9.1-7 drain: PR #1225 closed 1 of 6 cited issues; PR #1226 closed 1 of 15. Each occurrence cost ~5 min of manual `gh issue close` plus retro-comment writing. PR #1184/#1192 documented the parenthesized variant but not the bare comma-list — which is the more common shape since #1192 recommended migrating closing keywords from PR title to PR body. Filed as #1227 during cleanup; shipped as `scripts/check-no-comma-list-closes.py` pre-push hook in PR #1228.

**Action taken**: Closed — pre-push hook + PR-checklist correction shipped via PR #1228 (closes #1227). Verified self-applying: the hook caught its own commit message during the first push attempt of PR #1228; required adding inline-backtick + code-fence skip rules to the regex. Lint validates its own correctness.

**4. Pipeline-bypass merges silently dropped retros → audit + 17 backfills.**
v0.9.1-5 drain (PR #1206 work) revealed that 2 of 4 milestone PRs (#1203, #1204) merged WITHOUT pipeline-run Stage 14 retro comments. The "MANDATORY retro-artifact gate" only fires when pipeline-run is invoked — bypass merges escape the gate entirely. Filed as #1212; shipped `scripts/audit-pipeline-bypass.py` in PR #1229. Empirical use during v0.9.1-8 surfaced **17 historical PRs** without retro markers across 4 milestones (v0.9.1-1 stragglers + v0.9.1-2 era + v0.9.1-4 era + v0.9.1-5 dropouts + #1226 + #1187/#1201). All 17 backfilled via `gh pr comment` during this drain bucket.

**Action taken**: Closed (part 1) — audit script shipped via PR #1229 (closes #1212). Part 2 (ongoing CI check that flags merged PRs without retros within 24 hours) is genuinely deferred; tracked as a v0.9.2-1 candidate. See Action Tracker row below.

**5. Issue-reporter analysis ≠ root cause: dead code that resembles the bug is dangerous.**
PR #1206's reporter cited `_lazy_serialize_context` as the bug location. The function literally contained the buggy `str(model_instance)` fallback that matched the reported symptom (`__str__` strings in serialized context). But it had **zero call sites** — dead code. The actual bug was upstream in `_sync_state_to_rust` change-detection comparing `list[Model]` via `Model.__eq__` (pk-only). Anyone investigating with a "find code that produces __str__ output" mindset would have landed on the dead method and "fixed" it without changing observable behavior. The dead-code+symptom-match combination is dangerous; removing the dead method as part of PR #1206 + canonicalizing the lesson in CLAUDE.md is the structural mitigation.

**Action taken**: Closed — CLAUDE.md "Bug-report triage" section added via PR #1216 (closes #1213). Section cites PR #1206 as canonical case study; rule: trace from observable symptom to actual code path, not from reporter-cited code path to symptom. Symptom-up beats path-down.

**6. CodeQL FP elimination via canonical-path sanitizer model.**
PR #1201's security cleanup dismissed 8 `py/log-injection` FPs in `dispatch.py` and adjacent files. Root cause: the existing CodeQL sanitizer model declared `djust._log_utils.sanitize_for_log` but NOT `djust.security.log_sanitizer.sanitize_for_log` — the canonical definition site that 8+ production files import via `from djust.security import sanitize_for_log` (re-exported through `djust/security/__init__.py`). MaD matches on the actual definition namespace, not the re-export path. PR #1224 added the canonical-path entry; future security sweeps should see zero FPs from these call sites.

**Action taken**: Closed — `.github/codeql/models/djust-sanitizers.model.yml` expanded via PR #1224 (closes #1214). Verified by tonight's CodeQL re-scan: 0 open alerts post-merge.

**7. Backlog cleanup as release-prep discipline → 32 → 0 issues clean.**
User directive mid-arc: "we are releasing too often and need to try to clean up in this release." The v0.9.1-7 (canon batch closing 15 issues in PR #1226) and v0.9.1-8 (final cleanup with 4 work units in PRs #1228/#1229/#1230 + #1177 local) drains were specifically scoped to drain the tech-debt backlog before tag. Combined with mechanical closures (6 already-addressed by existing canon, 7 obsolete with reasons): **32 → 0 open tech-debt issues**, plus 17 PR-retro backfills, plus the open-PR cleanup (PRs #1231/#1232/#1233). The release window closed at 0 open PRs, 0 open issues, 0 open code-scan alerts, 0 open dependabot alerts.

**Action taken**: Closed — cleanup-before-release shipped as v0.9.1-7 + v0.9.1-8 drain buckets. Validated: v0.9.1 cut on the cleanest possible release window since the v0.9.0 GA bump.

**8. Milestone naming convention adopted: vX.Y.Z release vs vX.Y.Z-N drain bucket.**
Mid-arc, the divergence between `__version__ = "0.9.0"` and ROADMAP showing `v0.9.5` shipped surfaced as a documentation problem. The old scheme conflated release and planning bucket. New convention: `vX.Y.Z` = actual release (gets a git tag); `vX.Y.Z-N` = drain bucket / planning iteration toward release `vX.Y.Z`. SemVer-orders before the release: `v0.9.2-1 < v0.9.2`. Documented in ROADMAP.md "Milestone naming convention (adopted 2026-04-30)" section. Skills updated (pipeline-next, pipeline-drain, pipeline-retro, pipeline-roadmap-audit) with both shapes — their grep-based parsers already handle either form so no behavioral change to skill logic. Historical names (v0.9.1 through v0.9.5 already shipped as drain buckets) NOT retroactively renamed; cross-references in 50+ PR bodies and retro files preserved.

**Action taken**: Closed — convention canonicalized in `ROADMAP.md` "Milestone naming convention" section + 4 skill placeholder updates + project-memory `project_milestone_naming_convention.md`. v0.9.1 release is the first cut under the new convention.

### Insights

- **The drain-bucket abstraction works.** Eight buckets shipping over 4 days, packaged into one release, reads coherently in retrospect. Each bucket had a clear theme; bucket-level retros existed; the milestone-level retro synthesizes across without losing per-bucket nuance. Compare to the v0.7→v0.9 arc which conflated release with planning bucket and produced ROADMAP drift.
- **User-directive mid-arc is a load-bearing input.** The "we are releasing too often" directive at v0.9.1-6 → v0.9.1-7 transition forced the cleanup-before-release discipline that closed the backlog. Without that directive, v0.9.1 would have shipped with 30+ open tech-debt issues and the audit script + backfill discipline never would have surfaced. Future: model the user's "are we ready to ship?" gate as an explicit checkpoint between drain buckets, not just at release.
- **Self-applying canon is a quality signal.** PR #1218 (reproducer-first) → PR #1223 (#1207 used the new gate cleanly). PR #1219 (reviewer-prompt budget) → review for PR #1219 itself respected the 250-word cap. PR #1228 (comma-list lint) → caught its own commit message on first push. When new canon is applied to its introducing PR successfully, it tends to stick.
- **17 historical retro backfills surfaced via tooling, not memory.** The pipeline-bypass audit script ran on data the operator (me) didn't remember — historical PRs from v0.9.1-1, v0.9.1-2, v0.9.1-4 era. Tooling > memory for visibility-restoration tasks.
- **Two-commit shape + one-implementer-one-checkout is the right pair.** Either alone fails: the shape without the serialization gets cross-contaminated by parallel implementer agents; the serialization without the shape produces single-commit-with-everything PRs that mix concerns. The pair is the load-bearing pattern.
- **Release-day risk reduction comes from cleanup, not caution.** The user accepted the v3 action-gh-release bump (#1233) for the v0.9.1 cut despite my "defer" recommendation. The cut succeeded on first attempt. Insight: cleanup-before-release does more for release-day reliability than "defer anything risky to post-release."

### Review Stats

| Metric | v0.9.1-1 | v0.9.1-2 | v0.9.1-3 | v0.9.1-4 | v0.9.1-5 | v0.9.1-6 | v0.9.1-7 | v0.9.1-8 | Post-cleanup | Total |
|---|---|---|---|---|---|---|---|---|---|---|
| PRs merged | 7 | 7 | 1 | 5 | 9 | 4 | 1 | 3 | 3 | **40** |
| Local skill fixes | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 1 | 0 | 2 (#1080, #1177) |
| Issues closed | ~10 | ~10 | 3 | ~6 | ~5 + 9 follow-ups | 10 | 15 | 4 | 1 (#2287 auto-close) | ~63 |
| Tests added | ~10 | ~5 | — | ~10 | ~30 | ~12 | 0 (docs) | ~5 | ~5 | ~75+ |
| 🔴 Findings | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 🟡 Findings (review-time) | ~5 | ~10 | ~2 | ~10 | ~3 | ~3 | ~1 | ~1 | ~1 | ~36 (most addressed in same PR or follow-up bucket) |
| CI failures pre-merge | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 (cargo fmt amend on #1230) | 0 | 1 |
| Quality 1-5 (avg) | — | — | — | — | 4.4 (5 retros) | 4.6 (4 retros) | 4 | 4.7 (3 retros) | 5 (3 retros) | 4.5 |

### Process Improvements Applied

**CLAUDE.md** (additions during this milestone):
- §"Bug-report triage" — added in PR #1216 (closes #1213). Generalizes the issue-reporter ≠ root-cause lesson from PR #1206; cites PR #1206 as canonical case study.
- §"Process canonicalizations from v0.9.4 retro arc" — 6 rules added in PR #1225 (closes #1195-#1200).
- §"Process canonicalizations from v0.6.x–v0.8.x retro arcs (backfill)" — 14 rules added in PR #1226 (closes 15 issues across v0.6.1, v0.7.0, v0.7.1, v0.7.2, v0.8.0, v0.8.1, v0.8.2 retro arcs).

**Pipeline templates** (`.pipeline-templates/{feature,bugfix}-state.json`):
- Stage 4 mandatory item: "VERIFY ARTIFACT BEFORE PLANNING" (reproducer-first) — PR #1218 (closes #1210).
- Stage 11 reviewer-prompt budget guidelines (250 words for bugfix, 350 for feature; forbid edge-case spelunking) — PR #1219 (closes #1211).

**PR-checklist** (`docs/PULL_REQUEST_CHECKLIST.md`):
- "Dogfood pass for new CLI tools" — PR #1226 (closes #1060).
- "`mark_safe` XSS-trace audit" — PR #1226 (closes #1078).
- "No duplicate constants across files" Stage 7 rule — PR #1232 (post-cleanup).
- "Bare comma-list close-keyword" entry corrected (was wrongly "only the *last* issue closes"; now "only the FIRST closes") — PR #1228.

**Skills** (`~/.claude/skills/` — local only, gitignored):
- `pipeline-run/SKILL.md` — added "MANDATORY Post-Commit Programmatic Gates" section (#1177 local fix).
- `djust-release/SKILL.md` — Step 6 stages all 5 files + Cargo.lock (#1080 local fix). Verified working at the v0.9.1 cut.
- `pipeline-next/SKILL.md`, `pipeline-drain/SKILL.md`, `pipeline-retro/SKILL.md`, `pipeline-roadmap-audit/SKILL.md` — all updated with milestone-naming-convention placeholders.

**Pre-push hooks** (`.pre-commit-config.yaml`):
- `check-no-dead-private-methods` — PR #1220 (closes #1209).
- `check-no-comma-list-closes` — PR #1228 (closes #1227).
- `mixed-line-ending` exclude pattern for `.pxd` binary files — PR #1222 (closes #1215).

**CodeQL config** (`.github/codeql/models/djust-sanitizers.model.yml`):
- Added canonical-path sanitizer entry for `djust.security.log_sanitizer.sanitize_for_log` — PR #1224 (closes #1214).

**Audit scripts** (`scripts/`):
- `audit-pipeline-bypass.py` — PR #1229 (closes #1212 part 1).

### Open Items

- [ ] **Pipeline-bypass CI check (ongoing)** — the `#1212 part 2` complementary check that flags merged PRs without retro markers within 24 hours of merge. Audit script (part 1) shipped in PR #1229; the scheduled-Action half is deferred. Tracked as Action Tracker #200 (GitHub #1234).
- [ ] **Isolated cargo-test target for `filter_registry`** — `#1180 item 4`. The `OnceLock`-gated short-circuit test silently no-ops when prior tests register a filter. Either an isolated test target or a per-test reset would tighten coverage. Tracked as Action Tracker #201 (GitHub #1235).
- [ ] **Watch-list for release-workflow-touching dep bumps** — surfaced in PR #1233's retro. Any future dependabot bump that modifies `.github/workflows/release.yml` should get explicit risk-review at refile time, not auto-merged. Tracked as Action Tracker #202 (GitHub #1236).

---

## v0.9.5 — Security cleanup, broadcast recovery, RichSelect variants, list[Model] VDOM (PRs #1201, #1203, #1204, #1206)

**Date**: 2026-04-30
**Scope**: Four-PR drain post-v0.9.0 GA. Closes 19 code-scanning alerts (#1201) + 1 production-observed broadcast-recovery bug (#1203 / #1202) + 1 framework JIT change-detection blind spot user-reported with detailed diagnostics (#1206 / #1205) + 1 substantial RichSelect ergonomics polish (#1204). Two retro-gate violations surfaced and were backfilled.
**Tests at close**: 6664 Python + ~1486 JS = ~8150 total (+~14 added this milestone: 7 in test_list_model_diff_1205.py, 3 in test_server_push.py from #1203, plus #1204 internal test additions and assorted security-touch unit coverage).

### What We Learned

**1. Reproducer-first discipline cuts plan-stage waste — applies beyond bugfixes.**
PR #1206 explicitly burned ~10 min in Stage 4 chasing the issue reporter's claimed bug location (`_lazy_serialize_context` in `jit.py`) before writing a `LiveViewTestClient` reproducer that surfaced the actual code path (`_sync_state_to_rust` change-detection comparing `list[Model]` via `Model.__eq__` pk-only). The dead-code fallback method literally contained a `str(model)` call matching the reported symptom — a near-perfect misdirection. PR #1201 surfaced the same pattern from a different angle: 8 `py/log-injection` alerts in `dispatch.py` looked like real bugs until "two minutes of `sed -n '${line}p'`" before plan-stage revealed they all already used `sanitize_for_log()` and were FPs. **The same discipline applies to bug reports AND security alerts: read the actual code at the cited location BEFORE locking in the plan.** Trust-but-verify is cheaper than trace-and-rollback.

**Action taken**: Open — tracked in Action Tracker #191 (GitHub #1210). Update plan-template Stage 4 checklist to require an artifact (failing test for bugs, alert-line excerpt for security PRs) before plan finalization.

**2. Reviewer-agent reliability is variable — long-running prompts hit watchdog, short-prompt reviewers find real gaps.**
Two reviewer-agent contrasts in this milestone: PR #1201's reviewer stalled at the 10-min watchdog mid-tangent on backslash-injection edge cases (output stream stopped without a verdict — completed verification was thorough enough to accept as APPROVE in a security-PR context with fixed attack shapes, but in a less-obvious case would have required re-spawn). PR #1206's reviewer ran tight, surfaced a real test gap (no QuerySet-branch test) before merge, and APPROVED with non-blocking suggestions. **Lesson**: explicit prompt timeboxes and "no edge-case spelunking" guards prevent watchdog stalls. Security PRs especially benefit from cap-at-N-attack-shapes phrasing.

**Action taken**: Open — tracked in Action Tracker #192 (GitHub #1211). Add a `reviewer_prompt_budget` guideline to the pipeline-run skill's Stage 11 prompt template, capped at 200 words for security PRs and 350 words for feature PRs. Forbid edge-case enumeration beyond the documented attack-shape list.

**3. Pipeline-run retro-artifact gate failed for two of four milestone PRs (#1203, #1204) — silent dropout.**
Both PRs merged without their pipeline-run-stage-14 retro posting to GitHub. The gate at `pipeline-run` Stage 14's "MANDATORY retro-artifact gate (before setting `completed_at`)" should have caught this, but evidently didn't fire — possibly because pipeline-run wasn't used for these two PRs at all (operator merged manually or via a branch outside the pipeline-state tracking). This is the v0.9.0 retro arc's "outer-loop integrity" failure mode, surfacing again. **The retro is the source of truth for what was learned; without it, milestone retros must reverse-engineer from PR body + addressed-findings comments. Both backfills below are best-effort, not authoritative.**

**Action taken**: Open — tracked in Action Tracker #193 (GitHub #1212). Audit recent merged PRs against `.pipeline-state/*.json` to detect pipeline-bypass merges; consider a CI check that flags merged PRs without a retro comment on PR body containing `Quality:`/`What went well:`/`Lessons learned:` markers.

**4. The two-commit shape (impl+tests / docs+CHANGELOG) held cleanly under one-implementer-one-checkout discipline (#1206).**
PR #1206 followed the v0.9.1 retro Action Tracker #181 / GitHub #1173 canonicalization: separate commits for implementation+tests vs CHANGELOG+docs. No CHANGELOG cross-contamination, no duplicate `### Fixed` heading collisions. The pre-commit hook's stash-restore cycle behaved correctly because there was no parallel agent flipping branches. **The serialized-execution guarantee is what makes the two-commit shape safe.**

**Action taken**: Closed — pattern is canonicalized in `.pipeline-templates/feature-state.json` Stage 5/9 and `.pipeline-templates/bugfix-state.json` Stage 5/9 already (since v0.9.1 retro). PR #1206 demonstrated the canon working as intended; no further code change.

**5. "Issue-reporter analysis ≠ root cause" is generalizable — applies to every external bug report.**
Beyond #1205's specific `_lazy_serialize_context` misdirection, this is the failure mode for any bug report citing a code location: the reporter's diagnostic data may be real (`patch_count: 0`, `__str__` strings, exact error trace) AND their proposed fix location may be wrong. The framework cannot afford to take reporter-cited code paths at face value, especially when removing/modifying code at that location would not change observable behavior (the dead-code trap). **The discipline is: trace from observable symptom to actual code path, not from reporter-claimed code path to symptom.** Symptom-up beats path-down.

**Action taken**: Open — tracked in Action Tracker #194 (GitHub #1213). Promote this principle to `CLAUDE.md` under a new "Bug-report triage" section near the existing "Personality" section, with a concrete cross-reference to PR #1206 as the canonical case study.

**6. Generalizable framework patterns that survive review become tracker rows, not just retro prose.**
PR #1206's review surfaced three follow-ups already filed as GitHub issues (#1207 heterogeneous/nested list[Model] shapes, #1208 idempotency-test strengthening, #1209 vulture-based dead-code lint) — applying the v0.9.0 retro Stage 4 post-merge follow-up issue creation. The discipline of "non-blocking review suggestions become tracker rows" prevents loss of generalizable patterns surfaced during review.

**Action taken**: Closed — three follow-up issues filed at #1207, #1208, #1209. Action Tracker rows added below.

### Insights

- **Small drain cadence works.** Four PRs over 2 days post-v0.9.0 GA, mix of security cleanup + production fix + framework correctness + ergonomics polish. No giant batches; each PR scoped to a single concern. Wall-clock from issue-report (#1205) to merge (#1206) was ~30 min — that ratio holds when the reproducer arrives early in Stage 4.
- **CodeQL FP rate stays high in security cleanups.** 15/19 alerts (79%) were FPs. The `sanitize_for_log` sanitizer-declaration gap is the dominant FP source; CodeQL can't model the project's standard sanitizer. Investing in a CodeQL query model file would compound across every future security sweep.
- **`Model.__eq__` is pk-only is a framework-wide design surface.** Anywhere djust uses Python `==` to detect "did this value change", Models need pre-normalization to dicts. The `_sync_state_to_rust` normalize pass is the Phase 1 fix; Phase 2 should be a code-review checklist item that flags `==` comparisons over context values.
- **Variant taxonomy alignment matters.** PR #1204's `RichSelect` variants follow the same `info|success|warning|danger|muted|primary|secondary` vocabulary as `Badge`/`Button`/`Tag`/`Alert`. Permissive validation lets downstream projects ship custom variants without framework changes. The cross-component vocabulary alignment is what makes the design feel coherent — worth promoting as an explicit "djust component design rule" in CLAUDE.md or design docs.

### Review Stats

| Metric | PR #1201 | PR #1203 | PR #1204 | PR #1206 | Total |
|---|---|---|---|---|---|
| Lines added | +93 | +78 | +625 | +297 | +1093 |
| Lines deleted | -10 | -0 | -35 | -44 | -89 |
| Tests added | 0 (suite unchanged) | 3 | (multiple, internal) | 7 | ~10+ |
| 🔴 Findings | 0 | 0 | unknown (no retro) | 0 | 0 |
| 🟡 Findings | 0 (reviewer stall pre-verdict) | 3 (CHANGELOG, comment trim, no-patches branch test) | unknown (no retro) | 3 (heterogeneous shape, nested shape, idempotency strength) | 6+ |
| Findings fixed pre-merge | n/a | 3/3 | unknown | 0/3 (filed as follow-ups) | — |
| Findings filed as issues | 0 | 0 | 0 | 3 (#1207, #1208, #1209) | 3 |
| CI failures pre-merge | 0 | 0 | unknown | 0 | 0 |
| Quality rating | 5/5 | n/a (no retro) | n/a (no retro) | 4/5 | — |

### Process Improvements Applied

**CLAUDE.md**: No additions made during this milestone. Action Tracker #199 (GitHub #1213) will add a "Bug-report triage" section as a follow-up.
**Pipeline template**: No additions made. Action Tracker #196 (GitHub #1210) will add reproducer-first checklist requirement to Stage 4. Action Tracker #197 (GitHub #1211) will add reviewer-prompt budget guidelines.
**Checklist** (`docs/PULL_REQUEST_CHECKLIST.md`): No additions during milestone. Action Tracker #199 will reference the bug-report triage principle if/when promoted.
**Skills** (pipeline-run, pipeline-next, pipeline-retro): No additions. Action Tracker #198 (GitHub #1212) will audit pipeline-bypass merges to harden the retro-gate.

### Open Items

- [ ] Reproducer-first discipline canonicalized in plan-template Stage 4 — tracked in Action Tracker #191 (GitHub #1210)
- [ ] Reviewer-prompt budget guidelines for pipeline-run Stage 11 — tracked in Action Tracker #192 (GitHub #1211)
- [ ] Audit pipeline-bypass merges + harden retro-gate against silent dropout — tracked in Action Tracker #193 (GitHub #1212)
- [ ] "Bug-report triage" section in CLAUDE.md citing #1206 as case study — tracked in Action Tracker #194 (GitHub #1213)
- [ ] Heterogeneous and nested `list[Model]` shapes in change-detection normalize pass — tracked in Action Tracker #195 (GitHub #1207, filed during PR #1206 cleanup)
- [ ] Strengthen idempotency test with explicit zero-patch assertion — tracked in Action Tracker #196 (GitHub #1208, filed during PR #1206 cleanup)
- [ ] Vulture-based pre-push check for unused private methods — tracked in Action Tracker #197 (GitHub #1209, filed during PR #1206 cleanup)
- [ ] (Carried from PR #1201 retro) CodeQL query model declaring `sanitize_for_log` as sanitizer — tracked in Action Tracker #198 (GitHub #1214)
- [ ] (Carried from PR #1201 retro) Pre-commit `mixed-line-ending` cleanup of two `.pxd` files with lingering line-ending issues — tracked in Action Tracker #199 (GitHub #1215)
- [ ] (Carried from PR #1206 retro) Stage 4 plan-template requires reproducer FIRST — deduplicated with Action Tracker #191 above

---

## v0.9.4 — DX wave: HVR auto-enable + Debug Panel time-travel UI (PRs #1190, #1191, #1192, #1193, #1194)

**Date**: 2026-04-28
**Scope**: Five-PR drain closing the v0.9.4 milestone in a single session. Headlined by #1151 (Debug Panel UI for per-component time-travel + forward-replay), shipped as a foundation/capability split (wire-protocol PR-A → UI PR-B). Surrounded by two DX wins (#1190 HVR auto-enable, #1192 process canon) and one test-infra polish drain (#1191). 6 issues closed (#1151, #1185, #1188, #1189, #1143, #1144), 6 new tracker rows opened (#185–#190 → GitHub #1195–#1200).
**Tests at close**: 4047 Python + 1480 JS = 5527 total (+~75 added this milestone).

### What We Learned

**1. Refactor-with-helper guard audit.**
When extracting a helper from N call sites with inline input-validation logic, audit each call site to decide explicitly: push the validation INTO the helper, or keep it AT the call site. The "deferred to helper" assumption is silent — production code keeps working when inputs are well-formed; breaks only on malformed inputs that may not appear in tests. PR #1194 introduced `_sendTimeTravelMessage` and routed `onTimeTravelJumpClick` through it, inadvertently dropping a `typeof index !== 'number'` guard. The DOM dispatch path still validated, so the bug only mattered for programmatic callers — Stage 11 caught it.

**Action taken**: Open — tracked in Action Tracker #185 (GitHub #1195).

**2. Delegated-listener integration test pattern.**
For any "marker class + delegated event listener" feature, unit tests (direct method invocation) and integration tests (real DOM event → registered handler → method) need separate coverage. PR #1194's first version had 17 method-level vitest cases but ZERO integration tests — the `target.closest()` containment check, `parseInt` click-time parsing, and 4-branch dispatch order were entirely untested. Stage 11 caught it; backfill added 6 integration cases (one per click branch + non-tt-button + non-numeric data). Generalizable: every delegated-listener selector branch deserves at least one integration test.

**Action taken**: Open — tracked in Action Tracker #186 (GitHub #1196).

**3. Canon-doc citation discipline (grep-verify before commit).**
Every `file:line`, attribute name, method name, and bash one-liner cited in a canon doc (CLAUDE.md, PR-checklist, ADR) should be `grep`-verified before committing. Stage 11 reviewers will run those greps anyway; pre-empting saves a roundtrip. PR #1192 had 5 inaccuracies in a 3-rule docs PR — wrong line numbers, wrong attribute names, bash placeholder, wrong section ordering, speculative prose claims. None individually catastrophic, but the cumulative effect would have been a canon entry future readers couldn't trust.

**Action taken**: Open — tracked in Action Tracker #187 (GitHub #1197).

**4. Commit-or-rollback handler shape.**
Async handlers with BOTH state mutation AND early-return paths must mutate AFTER the commit point — otherwise the early-return path leaves state in a half-committed shape. PR #1193's `handle_forward_replay` set `view._time_travel_branch_id = new_branch` BEFORE awaiting `replay_event`; on `replayed is None` (handler missing/un-decorated), branch state stayed bumped with no recorded events, and view + client diverged about the active branch. Failure mode is silent (no exception); observability won't flag it. Two clean fix shapes: (a) defer the mutation past all early-return checks, (b) try/except with explicit rollback (only when multiple mutations need atomic rollback).

**Action taken**: Open — tracked in Action Tracker #188 (GitHub #1198).

**5. Edge-case coverage for index/cursor logic.**
When implementing a handler with index or cursor logic, run through cases at `index=0`, `index=len/2`, `index=len-1`, `index=len` (out of range) before declaring done. Four mental cases catches most off-by-one classes. PR #1193's `_build_time_travel_state` and `handle_forward_replay`'s gate answered the same boolean question with different formulas (`cursor < history_len` vs `from_index < history_len_before - 1`); they disagreed at `cursor=len-1, which="before"` with override_params. The mental trace catches it; the four-boundary discipline catches it before Stage 11.

**Action taken**: Open — tracked in Action Tracker #189 (GitHub #1199).

**6. Tautology test detection.**
When a test asserts "this thing happened", check whether the assertion would ALSO pass if the action under test did nothing. If yes, it's a tautology — production state from prior tests, fixtures, or module setup is making it pass for the wrong reason. Failure mode is silent: the test stays green forever even after the function under test silently breaks. Coverage metrics still report it as covered. PR #1190's `test_ready_completes_other_setup_even_when_auto_enable_skipped` asserted `any(isinstance(filters, DjustLogSanitizerFilter))` — but every prior test in the file calls `app.ready()` which adds another filter (no idempotency guard). The assertion would pass even if test #6's own ready() did nothing. Fix pattern: snapshot count BEFORE, assert grew by exactly 1.

**Action taken**: Open — tracked in Action Tracker #190 (GitHub #1200).

### Insights

- **Two-PR foundation/capability split worked again.** PR #1193 (wire protocol) and PR #1194 (UI on top) shipped separately with a tight contract — PR #1193's CHANGELOG explicitly noted what would arrive in PR #1194; PR #1194 referenced PR #1193's contract. A reviewer reading PR #1194 can verify against PR #1193's tests without scrolling through 800 LoC of server-side logic. Same pattern that worked for v0.8.6 View Transitions arc and v0.9.0 PR-A/PR-B/PR-C streaming arc. Worth preserving as a milestone-level pattern.
- **Stage 11 reviewers consistently catch real bugs.** Across 5 PRs this milestone: 11 🟡 findings, all real correctness or test-coverage gaps. Zero 🔴. Self-review missed these; the independent reviewer found them. The cost (~5min per review) is dwarfed by the cost of shipping the bugs. The retro-artifact gate also held: every PR retro posted as a comment.
- **CSP-strict canon (#1175) paid off.** PR #1194 added a new debug panel UI feature emitting HTML, and following the existing delegated-listener + marker-class pattern took zero design effort. The canon entry from v0.9.1 retro arc is doing exactly what canon entries are for: making the right pattern the easy pattern.
- **Pre-commit hooks remain a friction source.** PR #1194's first commit attempt failed because `end-of-file-fixer` rewrote the source file and `build-js` regenerated bundles — required a re-stage and re-commit. Not a defect (the hooks did their job), but a 10-second tax that adds up across milestones. `pre-commit run --files <staged>` before the first commit attempt would eliminate it.
- **Wall-clock budget**: 5 PRs from branch to merge in one session. Average ~45 min per PR (Plan → Implementation → Stage 11 → fix-pass → CI → merge → retro). The pipeline-ship + autonomous --all flag held all the way through; no manual intervention beyond the user's initial `/pipeline-run` invocation.

### Review Stats

| Metric | #1190 | #1191 | #1192 | #1193 | #1194 | Total |
|--------|-------|-------|-------|-------|-------|-------|
| Tests added | 6 | 0 (existing test edits) | 0 (docs only) | 12 | 23 | 41 |
| 🔴 Findings | 0 | 0 | 0 | 0 | 0 | 0 |
| 🟡 Findings | 2 | 1 | 5 | 2 | 2 | 12 |
| Findings fixed pre-merge | 2 of 2 | 1 of 1 | 5 of 5 | 2 of 2 | 2 of 2 | 12 of 12 |
| CI failures | 0 | 0 | 0 | 0 | 0 | 0 |
| Commits | 3 | 2 | 2 | 3 | 3 | 13 |

### Process Improvements Applied

**CLAUDE.md**: New "Process canonicalizations from v0.9.0 retro arc" section added in PR #1192 with 2 rules — Stage-4 first-principles grep canon (#168) + branch-name verify reflex (#169). Section ordering corrected to chronological (v0.9.0 before v0.9.1).

**PR-checklist (`docs/PULL_REQUEST_CHECKLIST.md`)**: Closes-#N rule expanded in PR #1192 to explicitly call out the parenthesized form `(closes #X, closes #Y)` as a known auto-close failure mode (#184).

**Pipeline templates**: No template changes this milestone — the canon items (#1185, #1144, #1143) all landed in repo-level docs. Skill-level changes deferred to a future skill-update PR.

**Vitest config**: PR #1191 narrowed Pattern 2 of `vitest.config.js` to drop the broader `stack.includes('view-transitions')` disjunct that could mask future genuinely-different failures. Same PR added `gc.collect()` to the `_wait_for_one`-warning absence test for deterministic finalization across CPython / PyPy / free-threaded.

### Open Items

- [ ] Refactor-with-helper guard audit pattern — tracked in Action Tracker #185 (GitHub #1195)
- [ ] Delegated-listener integration test pattern — tracked in Action Tracker #186 (GitHub #1196)
- [ ] Canon-doc citation discipline — tracked in Action Tracker #187 (GitHub #1197)
- [ ] Commit-or-rollback handler shape — tracked in Action Tracker #188 (GitHub #1198)
- [ ] Edge-case coverage for index/cursor logic — tracked in Action Tracker #189 (GitHub #1199)
- [ ] Tautology test detection — tracked in Action Tracker #190 (GitHub #1200)

---

## v0.9.2 — Retro-canon drain (PRs #1176, #1178, #1179, #1181, #1182, #1183, #1184 + #1172 skill update)

**Date**: 2026-04-28
**Scope**: Eight-work-unit drain closing 10 v0.9.1-retro follow-up issues. 7 PRs against the djust repo + 1 skill-only update (#1172 → `~/.claude/skills/pipeline-run/SKILL.md`). The drain was mostly polish on top of working v0.9.1 implementations — no real bugs, no headline features. The high-leverage work was the first 3 PRs (process canonicalizations from v0.9.1's retro lessons); PRs 4-8 are pure polish following those rules. 2 follow-up tracker issues filed (#1177 executor-side hooks, #1180 PR-#1179 polish) plus the v0.9.1 follow-ups #1160-#1171 all closed.
**Tests at close**: ~6729 Python + 1477 JS = ~8206 across the suite. ~50 new tests added across the milestone.

### What We Learned

**1. The 4 v0.9.1 retro canonicalizations validated themselves in the very drain that shipped them.**
v0.9.1 retro filed 4 process tracker rows (#180 serial agents, #181 two-commit shape, #182 3-clean-runs, #183 CSP-strict). v0.9.2 codified all 4 (PR #1176 + skill update + PR #1178). Then the remaining 5 PRs *applied* the rules and shipped clean. Zero CHANGELOG cross-contamination after the serial-agents rule was enforced. Three-for-three two-commit-shape splits (verified by Stage 11 reviewers). The dogfood loop closed on a single milestone — meaningful evidence that retro canonicalizations are durable when encoded as template/skill gates, not just CLAUDE.md prose.

**Action taken**: Closed — Action Tracker rows #180, #181, #182, #183 marked Closed (resolved in v0.9.2).

**2. PR #1176 self-defeated on the parenthesized-closes-syntax it was canonicalizing.**
PR #1176's TITLE used `(closes #1173, closes #1174)` — the parenthesized comma-list pattern that v0.9.1 retro tracker #164 already warned about. GitHub's auto-close parser silently missed both. Stage 11 caught it; implementer fixed via PR-body edit. The very PR canonicalizing v0.9.1 retro lessons fell into a v0.9.1 retro failure mode. New tracker row to encode "each closes-reference on its own body line" in the PR-checklist explicitly.

**Action taken**: Open — tracked in Action Tracker #184 (GitHub #1185).

**3. Stage 11 catch rate trended down across the milestone — canonicalizations actually moved the needle.**
PRs 1-4 (#1176, #1178, #1179, #1181) had 6 🔴 + 6 🟡 between them. PRs 5-8 (#1182, #1183, #1184) came back consistently clean: 0 🔴 + 0-2 🟡 each, with two LGTM-clean verdicts (#1183, #1184). The shape that worked: implementer + reviewer both reading the same canonicalized rules from `feature-state.json` / `bugfix-state.json` / CLAUDE.md / PR-checklist. Pre-canonicalization (v0.9.1), the rules lived in retro prose only; reviewers and implementers had to re-derive them per PR. Encoding moved the cost.

**Action taken**: Closed — observation about the canonicalization ROI; no separate action needed (already validated by closing #180-#183).

**4. RETRO_GATE_VIOLATION on PR #1176 — no retro comment posted.**
PR #1176 merged without a `## Pipeline Retro` PR comment, despite being the highest-leverage PR of the milestone (canonicalized 2 v0.9.1 lessons). Detected at Stage 2 of THIS retro. Backfilled in Stage 4 of this retro per the gate-violation protocol. The pipeline-run skill's mandatory retro-artifact gate caught it — but only because this milestone retro ran. Without periodic milestone retros, gate violations would accumulate silently.

**Action taken**: Closed — backfilled in Stage 4 of this retro via `gh pr comment 1176`. Existing Action Tracker #157 (RETRO_GATE_VIOLATION pattern, GitHub #1085) still tracks the broader class.

**5. Polish drains can be 0-defect — but the 🟡 follow-up flow keeps polish from inflating.**
v0.9.2 caught 0 user-facing defects (no real bugs, no security issues). All 🔴s were either CHANGELOG-discipline (PR #1176 R1, R2, R3) or accuracy nits (PR #1170 already-merged) — no behavioral regressions reached the v0.9.0 stable bake. Compare to v0.9.1 which caught 2 real bugs (PR #1170 open-redirect + auto-load) before merge. The "consolidate 🟡s into one follow-up issue per PR" pattern (filed 2 follow-ups: #1177, #1180) keeps polish merging while preserving discovered work for future drains.

**Action taken**: Closed — pattern validated; existing v0.9.0 retro Action Tracker #157 et al. cover the broader 🟡-deferral discipline.

**6. Mechanical-replacement audit ratio stayed high — N similar sites = N tests.**
PR #1183 (cookie namespace polish) found a write-side issue and applied the legacy-cleanup helper to ALL 6 cookie write sites in `theme.js` (not just the 1-2 obvious ones). PR #1184 (data_table polish) extended `NESTED_CONTROL_SELECTOR` from 6 → 9 tags + tested all 3 new tags via JSDOM. Both PRs hit the v0.8.6 retro #1104 ratio (canonicalized in CLAUDE.md). Continued evidence that the rule scales.

**Action taken**: Closed — already canonicalized in CLAUDE.md `## Process canonicalizations from PR retros (2026-04-26 View Transitions arc)` rule #1104. Reinforced this milestone; no new tracker row.

### Insights

- **8 work units in a single autonomous session** — drain spanned the v0.9.1 retro completion forward. Background implementer agents per iteration (1 at a time post-iter-3 lesson) kept the parent-session context tight.
- **Single-iteration retro spans now ~30-90 min wall-clock** end-to-end (seed → impl agent → Stage 11 → fix → CI → merge → retro). Tracking that the canonicalizations didn't slow down the iteration cadence was a quiet win.
- **First milestone where the implementer→reviewer agent pair shared a written rule set** (CLAUDE.md "v0.9.1 retro arc" + canonical templates). Earlier milestones had reviewers re-deriving rules from RETRO.md prose. This may be the durable shape for autonomous-pipeline drains.
- **No new ADRs landed**. All 7 PRs landed under existing ADR/process frames. Expected for a polish-heavy drain.

### Review Stats

| Metric | #1176 | #1178 | #1179 | #1181 | #1182 | #1183 | #1184 | Total |
|--------|-------|-------|-------|-------|-------|-------|-------|-------|
| Tests added | 0 | 0 | 4 | 2 | 6 | 10 | 10 | 32 |
| Doc/CHANGELOG entries | 4 | 4 | 1 | 1 | 1 | 1 | 1 | 13 |
| 🔴 Findings | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3 |
| 🟡 Findings | 1 | 1 | 3 | 2 | 2 | 0 | 0 | 9 |
| 🔴 fixed pre-merge | 3 | — | — | — | — | — | — | 3 |
| 🟡 deferred to follow-up | 1 | — | 3 | — | — | — | — | 4 (in #1177, #1180) |
| Stage 11 verdict | REQ_CHG → APPROVE | APPROVE | APPROVE | APPROVE | APPROVE | LGTM | LGTM | — |
| CI matrix on final merge | 13/13 | 13/13 | 13/13 | 13/13 | 13/13 | 13/13 | 13/13 | clean |

### Process Improvements Applied

**ADRs landed**: None this milestone (pure polish + canonicalization).
**CLAUDE.md additions**: "Process canonicalizations from v0.9.1 retro arc" section (PR #1178) covering #180-#183.
**Skill updates**: `~/.claude/skills/pipeline-run/SKILL.md` "One Implementer Agent Per Checkout" section (#1172, applied directly).
**Pipeline templates**: `feature-state.json` Stage 5/9 two-commit gates; new `bugfix-state.json` with Stage 6 3-clean-runs; `ship-state.json` symmetric Stage 5 gate (PR #1176).
**PR-checklist additions**: "CSP-Strict Defaults" subsection under Security Review (PR #1178).
**Docs**: `docs/guides/security.md` "CSP-Strict Defaults for Framework Code" section (PR #1178); `docs/website/guides/components.md` descriptor-pattern auto-promotion gap note (PR #1181).

### Open Items

- [x] "Closes #N each on its own body line" PR-checklist canon — Action Tracker #184 (GitHub #1185). — resolved in v0.9.4 (PR #1192)
- [ ] PR #1170 row-nav 3 🟡 follow-ups (now closed via PR #1184) — superseded.
- [ ] PR #1179 custom filter polish 3 🟡 follow-ups — GitHub #1180.
- [ ] Executor-side post-stage hook enforcement (Stage 5 / 9 / 6 mechanical gates) — GitHub #1177.

---

## v0.9.1 — Follow-up drain (PRs #1159, #1161, #1163, #1164, #1166, #1168, #1170)

**Date**: 2026-04-27
**Scope**: Seven-PR drain closing 10 v0.9.1 issues from the v0.9.0 retro spawn list + post-rc2 user reports. Two P1 unblockers (#1134 flaky-test bisect, #1121 Rust filter bridge), three ADR-015 deferrals (#1146 A075 / #1147 CSP nonce / #1145 Rust live_render lazy=True), three hygiene items (#1148 / #1149 / #1150), one user-reported bug (#1158 theming cookie namespace), and one feat (#1111 data_table row navigation). 6 follow-up tracker issues filed (#1160, #1162, #1165, #1167, #1169, #1171); #1134's existing tracker was bumped via comment.
**Tests at close**: ~6679 Python + 1461 JS = ~8140 across the suite. ~82 new tests added across the milestone.

### What We Learned

**1. Parallel implementer agents on the same checkout produce CHANGELOG cross-contamination.**
Two background implementer agents running concurrently (iter 3 PR #1163 and iter 4 PR #1164) flipped between branches via pre-commit stash/restore mid-edit. The result: PR #1163's CHANGELOG captured #1164's `[Unreleased]` entries (#1148 + #1149) for code that wasn't in #1163's diff. Stage 11 caught it as a 🔴 must-fix. PR #1164 also had a duplicate `### Fixed` heading from the same race. After serializing iters 5-7, no further contamination. The single-script transformation pattern adopted by iter 4 (write all edits in one Python script + commit immediately) is a fallback, not a primary defense.

**Action taken**: Open — tracked in Action Tracker #180 (GitHub #1172).

**2. Two-commit shape (impl+tests / docs+CHANGELOG) defended against the contamination — and held cleanly across iters 5-7.**
After observing the #1163/#1164 contamination, every subsequent PR (#1166, #1168, #1170) used a two-commit shape: implementation + tests in commit 1, docs + CHANGELOG entry in commit 2 (Stage 9). Stage 11 reviewers explicitly verified each split was clean (per-commit `gh api .../files` checks). Three-for-three validation that deferring CHANGELOG hunks to a single Stage 9 commit eliminates the cross-edit collision class.

**Action taken**: Open — tracked in Action Tracker #181 (GitHub #1173).

**3. "3 clean full-suite runs" verification gate caught a hidden second polluter on PR #1159.**
The bisect agent for #1134 didn't stop at the first hit (in-memory SQLite leak from `test_async_render_path.py`). It kept verifying until 3 clean runs, which surfaced an unrelated second polluter (`sys.modules` rebind in `test_dev_server_watchdog_missing.py`) that wasn't on the original 6-test list. Without the verify-3x gate, the second polluter would have shipped silently — and the next PR would have hit the same flake. Pollution by definition shows up under specific orderings; a single-run pass is insufficient.

**Action taken**: Open — tracked in Action Tracker #182 (GitHub #1174).

**4. Stage 11 reviewer agents caught 3 real 🔴 bugs across the milestone — all fixed inline.**
PR #1163 had CHANGELOG cross-contamination. PR #1170 had two 🔴s: open-redirect via protocol-relative URL (`//evil.com/path` passed the `data-href` allowlist regex `/^(https?:|\/|\.)/`) and silent-fail-no-script-loaded UX (the JS module was sitting in static assets but no `<script>` tag emitted it). All three caught by the independent reviewer agent, none by the implementer's own tests. The "spawn an independent reviewer who hasn't seen my reasoning" pattern is consistently the highest-ROI Stage of the pipeline.

**Action taken**: Closed — Stage 11 mandatory rule already canonicalized in `~/.claude/skills/pipeline-run/SKILL.md` "Stages that MUST NEVER be skipped" section. The 3-real-🔴 hit-rate this milestone reinforces it without needing a new tracker row.

**5. CSP-strict defaults emerged as a cross-PR pattern — worth canonicalizing for v1.0.**
PR #1163 added CSP-nonce-aware activator for `<dj-lazy-slot>` fills. PR #1170 went further — chose to skip inline scripts entirely in favor of an external static JS module that auto-binds on a marker class. The "external module + auto-bind on marker class" shape is strictly more CSP-friendly than "inline script + nonce attribute" and works under stricter CSP policies. Two converging pieces of evidence within one drain that strict-CSP deployments should be a v1.0 design constraint, not an opt-in.

**Action taken**: Open — tracked in Action Tracker #183 (GitHub #1175).

**6. Stage-4 first-principles "grep before architecting" validated for the FOURTH time.**
Plan stage caught material reuse twice in this drain: PR #1161's eager filter registry mirrored the existing custom-tag-handler bridge (`crates/djust_templates/src/registry.rs`) — same `Py<PyAny>` storage shape, same dispatch. PR #1166's `live_render` lazy=True port reused the same eager-registry pattern + introduced the generic `call_handler_with_py_sidecar` so future Rust-path tags don't need to invent their own bridge. Without the grep-before-architecting pass, both PRs would have shipped redundant bridges. Already canonicalized in v0.9.0 retro tracker #168 (#1143); fourth validation strengthens the case.

**Action taken**: Closed — already canonicalized in CLAUDE.md / Action Tracker #168 (GitHub #1143). Reinforced by this milestone; no new tracker row needed.

### Insights

- **7 PRs in a single autonomous session** (drain spans the v0.9.0rc2 release moment forward). Background implementer agents per iteration kept the parent-session context tight. The pattern "spawn implementer agent for stages 4-10, then spawn reviewer agent for stage 11, parent handles 1-3 + 12-14" is repeatable.
- **Follow-up issue density**: 6 tracker issues filed (#1160, #1162, #1165, #1167, #1169, #1171) for ~24 🟡 should-fix findings across the 7 PRs. The "consolidate N 🟡s into one follow-up issue per PR" pattern keeps the merge moving while preserving the polish work for a future v0.9.x batch.
- **Re-balance of "real bugs caught by reviewer" vs "process bugs (CHANGELOG contamination)"**: 2 real bugs (PR #1170 open-redirect + auto-load) vs 1 process bug (PR #1163 CHANGELOG). Suggests the process gates (two-commit shape, serial-agent rule) are doing their job — most 🔴s are now genuine code defects rather than mechanical drift.
- **Bisect-as-pipeline-task** (PR #1159) was the most non-trivial drain item — the implementer agent ran for 31 minutes wall-clock to converge. Pattern works for any pollution-class fix where the investigation IS the work.

### Review Stats

| Metric | #1159 | #1161 | #1163 | #1164 | #1166 | #1168 | #1170 | Total |
|--------|-------|-------|-------|-------|-------|-------|-------|-------|
| Tests added | 9 | 10 | 17 | 5 | 8 | 8 | 25 | 82 |
| 🔴 Findings | 0 | 0 | 1 | 0 | 0 | 0 | 2 | 3 |
| 🟡 Findings | 1 | 6 | 6 | 3 | 1 | 4 | 3 | 24 |
| 🔴 fixed pre-merge | — | — | 1 | — | — | — | 2 | 3 |
| 🟡 deferred to follow-up | 1 | 6 | 6 | 3 | 1 | 4 | 3 | 24 |
| Stage 11 verdict | APPROVE | APPROVE | REQ_CHG → APPROVE | APPROVE | APPROVE | APPROVE | REQ_CHG → APPROVE | — |
| CI matrix on final merge | 13/13 | 13/13 | 13/13 | 13/13 | 13/13 | 13/13 | 13/13 | clean |

### Process Improvements Applied

**ADRs landed**: None this milestone (all 7 PRs landed under existing ADR-014 / ADR-015 frames).
**CLAUDE.md additions**: None this milestone — the 4 tracker rows (#180-#183) are queued for a follow-up CLAUDE.md update once the issues land.
**Skill updates**: None this milestone — the pipeline-run skill canonicalizations from #180/#181/#182 are queued.
**ROADMAP updates**: v0.9.1 milestone added at `dfee2a31` (pre-drain). All 10 originally-queued issues closed by this milestone.

### Open Items

- [ ] Serialize implementer agents per checkout — Action Tracker #180 (GitHub #1172).
- [ ] Two-commit shape canonicalized in pipeline template — Action Tracker #181 (GitHub #1173).
- [ ] "3 clean full-suite runs" verification gate for pollution-class fixes — Action Tracker #182 (GitHub #1174).
- [ ] CSP-strict defaults for new client-side framework code — Action Tracker #183 (GitHub #1175).
- [ ] PR #1170 follow-ups (nested-control tags / test-hook namespace / Python allowlist test) — GitHub #1171.
- [ ] PR #1168 follow-ups (empty-namespaced cookie / namespace validation / JSDOM write test / legacy cleanup) — GitHub #1169.
- [ ] PR #1166 follow-ups (test-isolation flake / asymmetric sidecar) — GitHub #1167.
- [ ] PR #1164 follow-ups (caplog assertions / descriptor doc / dev-env regression) — GitHub #1165.
- [ ] PR #1161 follow-ups (hot-path Mutex / hardcoded autoescape / weak negative test / unused fn / fixture / async filter) — GitHub #1162.
- [ ] PR #1159 follow-up (Redis perf bound) — GitHub #1160.

---

## v0.9.0 — Streaming arc + DevTools polish — shape C (PRs #1128, #1135, #1138, #1139, #1141, #1142)

**Date**: 2026-04-27
**Scope**: Six-PR feature wave before 1.0 testing. Closes #1032 (sticky-LiveView 1.0-blocker), #1043 (Phase 2 streaming arc — split-foundation into PR-A/B/C per retro #1122), #1041 (component-level time-travel), #1042 (forward-replay Redux DevTools parity). Two ADRs (014 sticky auto-detect, 015 Phase 2 streaming). Closes the v0.6.1 retro #116 doc-claim debt about Phase 1 streaming being cosmetic.
**Tests at close**: ~6597 Python + 1427 JS = ~8024 across the suite. v0.9.0rc1 tagged 2026-04-27, GitHub Actions building wheels.

### What We Learned

**1. Stage-4 first-principles pass paid off in 3 of 6 PRs.**
The Plan agent's habit of grepping the existing codebase before locking architecture caught material reframing in three places: #1032 (the cookie/header/handshake transport debate was moot — the WS pipeline already carried survivor info to the tag's render moment); #1041 (`_capture_snapshot_state` was already a clean extension point — ~85 LoC of additions, not a green-field rewrite); #1135 (Phase 1 streaming was a regex-split-after-render with no real TTFB win — closing retro #116 doc-claim debt was a free side-effect). Without the explicit "what does the code already do?" pass, scope estimates would have drifted 2-3× upward.

**Action taken**: Open — tracked in Action Tracker #168 (GitHub #1143).

**2. Split-foundation rule (retro #1122) validated for the third time.**
PR-A foundation soaked as standalone TTFB win → PR-B added user `lazy=True` API + `as_view` dispatch → PR-C added `asyncio.as_completed` parallelism. Each landed independently with its own Stage 11 review. PR-A's `as_view` dispatch wiring was deliberately deferred to PR-B per Stage 11 review feedback — landing wired infrastructure with its first user, not naked. Same shape as the View Transitions PR-A/PR-B arc (#1098/#1107).

**Action taken**: Closed — already canonicalized in CLAUDE.md `## Process canonicalizations from v0.8.6 retro arc` rule #1122.

**3. Branch-name drift caught twice; the post-commit verify rule is incomplete.**
PR-A's foundation commit (8500998f) initially landed on `docs/tutorial-search-as-you-type` (a pre-existing worktree branch with leftover WIP) instead of `feat/streaming-phase2-1043-pr-a`. Caught at push time when `git log -1` showed the wrong branch. PR-C's work started on `main` instead of the PR-C branch — caught when committing showed an unexpected branch in the prompt. The mandatory `git commit && git log -1 --oneline` check (#122) catches commits that don't register, but doesn't catch commits to the WRONG branch. Need a pre-commit `git symbolic-ref --short HEAD` match against the active state file's `branch_name`.

**Action taken**: Open — tracked in Action Tracker #169 (GitHub #1144).

**4. Stage 11 review caught material bugs every time.**
PR-A: 3 🔴 (dead script-mask code in arender_chunks, redundant `a[i:j]+a[j:]` slice, doc Quick-start regression saying "just works" before dispatch was wired). PR-C: 2 🔴 (failed_task identity-fragility on multi-failure, missing T-PRC-4 mid-stream-cancellation test). #1042: 1 🟡 with real impact (dunder dispatch via bare getattr) + 1 real bug uncovered by adding a test (ghost-attr cleanup deleted `time_travel_enabled = False` instance shadow, restoring the snapshot's historical True value). All caught + fixed before merge across 5 PRs that needed REQUEST_CHANGES (PR #1128 was the only one to land Stage 11 APPROVE on first pass).

**Action taken**: Closed — Stage 11 mandatory rule already canonicalized in `~/.claude/skills/pipeline-run/SKILL.md` "Stages that MUST NEVER be skipped" section. The 5/6 hit-rate this milestone reinforces it without needing a new tracker row.

**5. Pre-existing test-isolation flakies grew from 5 to 6 across the milestone.**
#1134 was filed during PR-A. By v0.9.0rc1 release, `test_redis_serialization_performance` was added as the 6th. Each PR had to skip-mark a test and reference #1134. Pre-push hook full-suite run fails; tests pass in isolation. The pollution comes from another test file mutating global state (Django settings, Channels consumer registry, or Redis mock state). Bisecting the polluting test is the proper fix; doing it now would unblock the pre-push hook for every future PR.

**Action taken**: Open — tracked in Action Tracker #170 (GitHub #1134 already exists; bumped to HIGH-priority via comment).

**6. Rust template engine `{% live_render %}` gap surfaced in PR-B integration tests.**
The integration test for `lazy=True` initially used the parent's `template = "..."` inline attribute, which the Rust template engine doesn't recognize for the `{% live_render %}` tag. Test had to be rewritten to drive `Template(...).render(Context(...))` directly via Django's engine. Production users who use `template = "..."` (Rust) for performance can't use `lazy=True`. Documented as out-of-scope for v0.9.0 but worth tracking: register `live_render` as a Rust tag handler so both paths support `lazy=True`.

**Action taken**: Open — tracked in Action Tracker #171 (GitHub #1145).

### Insights

- **6 PRs in a single autonomous session** is the largest milestone-to-date by PR count. Pipeline-run `--all` mode held up across the full arc with one human checkpoint mid-way (the "scope update on v0.9.0 to shape C" message after PR #1128 shipped). Per-PR retros, Stage 11 reviews, and address-findings pushes were consistent quality.
- **Two ADRs landed alongside their first users** (014 with PR #1128, 015 with PR #1135) rather than being written speculatively. Both ADRs gained §"Deferred from..." subsections during implementation as Stage 11 review surfaced things that didn't make this PR's scope. ADR-015's split into PR-A/B/C was the design output of Plan stage, not the upfront framing — the original framing was that #1043 was a single PR.
- **The MANDATORY post-commit verification rule (#122) saved time again** but its incompleteness (branch-name drift) was the only repeat process problem this milestone. Adding the branch-name match would close that gap.
- **Stage 11 reviewer agents caught ~10 material issues** across 6 PRs — defects that would have shipped silently. The "spawn an independent reviewer who hasn't seen my reasoning" pattern is consistently load-bearing.
- **#1134 friction is now visible**: 6 of every full-suite run's failures come from this single root cause. Until the polluting test is bisected, every PR pays a flat 30-second skip-marker tax. Worth prioritizing.

### Review Stats

| Metric | #1128 | #1135 | #1138 | #1139 | #1141 | #1142 | Total |
|--------|-------|-------|-------|-------|-------|-------|-------|
| Tests added | 9 | 18 | 24 | 4 | 10 | 10 | 75 |
| 🔴 Findings | 0 | 3 | 0 | 2 | 0 | 0 | 5 |
| 🟡 Findings | 3 | 4 | 5 | 3 | 3 | 1 | 19 |
| Findings fixed | 3 | 7 | 5 | 5 | 3 | 4 | 27 |
| Stage 11 verdict | APPROVE | REQ_CHG | COMMENT | REQ_CHG | COMMENT | COMMENT | — |
| LoC core (non-test, non-doc) | ~42 | ~520 | ~580 | ~80 | ~115 | ~95 | ~1432 |
| LoC test | ~280 | ~460 | ~700 | ~230 | ~265 | ~250 | ~2185 |

### Process Improvements Applied

**ADRs landed**: ADR-014 (sticky auto-detect, with PR #1128); ADR-015 (Phase 2 streaming, with PR #1135 and §"Deferred from PR-B" subsection added during PR #1138 review covering A075 system check + CSP nonce work).
**Skill updates**: None this milestone — `~/.claude/skills/pipeline-run/SKILL.md` was already at the v0.8.6 state. The branch-name verify check (Finding 3) is queued as a next-milestone skill update.
**CLAUDE.md additions**: None this milestone — saving for the v0.9.0 follow-up retro pass once the v0.9.0rc1 → v0.9.0 stable promotion settles. The Stage-4 first-principles canonicalization (Finding 1) is queued.
**ROADMAP updates**: shape C structuring committed as 4f9e3003; sequencing strategy locked (#1032 → #1043 split → #1041 → #1042).

### Open Items

- [x] Stage-4 first-principles canonicalization in CLAUDE.md — tracked in Action Tracker #168 (GitHub #1143). — resolved in v0.9.4 (PR #1192)
- [x] Branch-name verify check in pipeline-run skill — tracked in Action Tracker #169 (GitHub #1144). — resolved in v0.9.4 (PR #1192)
- [ ] #1134 polluting-test bisect (HIGH-priority bump) — tracked in Action Tracker #170 (GitHub #1134, comment + label bump).
- [ ] Rust template engine `{% live_render %}` tag handler — tracked in Action Tracker #171 (GitHub #1145).
- [ ] A075 system check (sticky+lazy template scan) — tracked in Action Tracker #172 (GitHub #1146).
- [ ] CSP-nonce-aware activator script for `<dj-lazy-slot>` fills — tracked in Action Tracker #173 (GitHub #1147).
- [ ] Replay handler argument validation (defense-in-depth) — tracked in Action Tracker #174 (GitHub #1148).
- [ ] `markdown` package missing from default test env (carryover from v0.8.7 retro) — tracked in Action Tracker #175 (GitHub #1149).
- [ ] Descriptor-pattern component time-travel verification test — tracked in Action Tracker #176 (GitHub #1150).
- [x] Debug panel UI for per-component scrubbing + forward-replay — tracked in Action Tracker #177 (GitHub #1151). — resolved in v0.9.4 (PRs #1193 + #1194)
- [ ] Vitest unhandled-rejection in `view-transitions.test.js` — tracked in Action Tracker #178 (GitHub #1152).
- [ ] `asyncio.as_completed._wait_for_one` warning suppression — tracked in Action Tracker #179 (GitHub #1153).

---

## v0.8.6 — View Transitions PR-B + 3 downstream-consumer data_table issues + async-hooks (PRs #1112, #1113, #1115, #1116, #1117, #1119, #1120)

**Date**: 2026-04-26
**Scope**: Closes the View Transitions arc started in v0.8.5; ships the actual user-facing wrap (PR-B). Resolves 3 downstream-consumer data_table issues that surfaced during the session (#1110, #1111, #1114 HIGH). 7-PR milestone with 16 GitHub issues closed plus the View Transitions ADR-013 row.
**Tests at close**: 4767 Python + 1419 JS = ~6186 across the suite (was 4745 + 1402 = 6147 at v0.8.5 close).

### What We Learned

**1. The two-PR split for high-blast-radius features keeps paying off.**
v0.8.5's PR-A (async signature) + v0.8.6's PR-B (View Transitions wrap) was the third application of "split foundation from capability into separate PRs". PR #1092's earlier attempt at one bundled PR shipped a sync-callback bug that escaped to retroactive Stage 11. Splitting:
  - Localizes review surface (each PR has zero or near-zero must-fixes)
  - Lets the foundation soak through one or more releases before the capability rides on top
  - Forces the dependency to be made explicit (PR #1112's `_inflight` queue was a v0.8.6 PR-B blocker, filed and resolved as a discrete unit before PR-B landed)

**Action taken**: Open — tracked in Action Tracker #163 (GitHub #1122).

**2. Pre-mount/post-mount keyset invariant tests generalize beyond first use.**
PR #1117 introduced `test_pre_mount_default_has_required_template_keys` for `DataTableMixin` (asserting `post_mount_keys ⊆ pre_mount_keys` so future post-mount additions can't drift). PR #1119 added 3 new keys to the same dispatcher; the test caught the keyset alignment automatically without changes. Pattern shape: any framework-level context dict that has both a default form and a runtime-populated form benefits from this test.

**Action taken**: Open — tracked in Action Tracker #164 (GitHub #1123).

**3. RETRO_GATE_VIOLATION hit twice in v0.8.6 (PRs #1119, #1120).**
4th milestone in a row with this gap. Action #157 already tracks "small bookkeeping PRs bypass retro-artifact gate" (filed v0.8.3). The pattern: when I move fast through a batch ($_ALL$ flag in pipeline-run), the retro stage gets dropped silently. The state-file gate works for state-file-tracked PRs but not for branch-only iterations within a multi-PR pipeline. Backfilled both retros at the start of this milestone retro (Stage 2). The structural fix needs to land — either tighten the gate or document an explicit exemption path.

**Action taken**: Open — already tracked in Action Tracker #157 (GitHub #1085). Adding a 4th-strike note via stage 4.

**4. CodeQL `js/tainted-format-string` is a real review checkpoint.**
PR #1120 introduced `console.error(\`[dj-hook] Error in ${label}:\`, e)` where `label` derives from `el.getAttribute('dj-hook')` — user-controlled DOM. CodeQL flagged 2 high-severity warnings post-CI. Fix is parameterized format string: `console.error('[dj-hook] Error in %s:', label, e)`. The %s form is the canonical safe pattern in JS — pulls `label` out of the format string entirely.

**Action taken**: Open — tracked in Action Tracker #165 (GitHub #1124).

**5. Bulk dispatch-site refactors with one helper + tests as the safety net.**
PR #1117 decorated 21 `on_table_*` methods via Python regex script. PR #1120 refactored 9 hook lifecycle sites to a single `_safeCallHook` helper. Both:
  - Used a one-helper-pattern (decorator / wrapper) — duplication eliminated
  - Were validated by a count-based test (`test_handler_count_matches_expected`, `test_all_on_table_methods_decorated`) that catches future additions that forget to follow the pattern
  - Bulk edits saved time but the count-test was the load-bearing correctness gate

**Action taken**: Open — tracked in Action Tracker #166 (GitHub #1125).

**6. The async refactor (v0.8.5 PR-A) delivered concrete ROI in v0.8.6.**
3 distinct features in v0.8.6 leveraged the async signature: PR #1112 (handleMessage queue), PR #1113 (View Transitions wrap), PR #1120 (async-tolerant dj-hook dispatch). PR-A on its own had zero user-visible impact and was a breaking signature change. v0.8.6 paid that back across 3 vectors. Pattern validation: foundational refactors with no immediate user benefit are still net-positive when they enable 2+ downstream features within 1-2 milestones.

**Action taken**: Closed — pattern validated; sufficient evidence in v0.8.5/v0.8.6 retros.

### Insights

- **Stage 11 caught real things in 5/7 PRs (71%).** Of the 7 substantive PRs, 5 had Stage 11 findings that were fixed before merge: #1112 (3 un-awaited `handleMessage` calls — 🔴 must-fix), #1117 (misleading docstring + show_stats follow-up), #1119 (Phase 6 docstring + row_url XSS warning + CSP caveat), #1120 (9-vs-8 count, plus the post-CI CodeQL catch). Without Stage 11 these would have shipped. The "always run Stage 11" rule continues to earn its keep across milestones.

- **Test-pattern surprise: `row|dictsort:col.key|first` is Rust-engine-only.** PR #1119 tests using stock Django's template engine produced empty cells. Solution: assert structural output (attribute presence, conditional branches), not extracted values. Generalizes to any djust template using dict-key-by-filter chains. Worth a CLAUDE.md note.

- **Format-string hygiene as a self-review pattern.** PR #1120 had to fix template-literal logging post-CodeQL. The general rule: when an error log includes user-controlled data (DOM attributes, server frame fields, request body), use `%s` placeholder + parameter, not template literals. The bundled `client.js` line-number reports made the trail harder to follow than necessary.

- **The View Transitions arc shipped end-to-end across 2 milestones with zero rollbacks.** PR-A (v0.8.5) → #1098 fix (v0.8.6) → PR-B (v0.8.6). 3 PRs, zero 🔴 across all 3 Stage 11 reviews after addressing findings, zero post-merge fixes. The split-foundation approach beat PR #1092's failed monolith.

### Review Stats

| Metric | #1112 | #1113 | #1115 | #1116 | #1117 | #1119 | #1120 | Total |
|--------|------:|------:|------:|------:|------:|------:|------:|------:|
| Issues closed | 1 | (ADR-013) | 4 | 7 | 1 | 2 | 0 | 16 |
| Tests added | 6 | 12 | 5 | 0 | 8 | 14 | 5 | 50 |
| 🔴 Stage 11 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 1 |
| 🟡 Stage 11 | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 2 |
| 🟢 Stage 11 | 3 | 0 | 3 | 0 | 3 | 3 | 3 | 15 |
| Findings fixed before merge | 3 | n/a | n/a | n/a | 1 | 3 | 1 | 8 |
| CodeQL post-CI catches | 0 | 0 | 0 | 0 | 0 | 0 | 1 (high) | 1 |
| CI runs to green | 1 | 1 | 1 | 1 | 1 | 1 | 2 (CodeQL refit) | 8 |

### Process Improvements Applied

**CLAUDE.md (PR #1116)**:
- Async-migration regex pass: completeness-grep convention
- ADR scope-estimation counts test-file callers (2-3× src)
- `is None` coalesce vs `kwargs.setdefault` for forwarding mixins
- N similar sites need N tests (not "a representative few")
- CHANGELOG phrasing for additions to existing test files
- `Iterable[T]` over `list[T]` for membership-check parameters
- Dynamic test fixture pattern: `type(name, bases, dict)` over class mutation
- Microtask-faithful test stubs for browser APIs
- Batch-PR issue × file × test mapping table

**Pipeline template / skill**:
- `pipeline-run` skill: removed the "After 3+ tasks in `--all` mode, pause for review" rule per user feedback. Autonomous mode is autonomous.

**Skills**: none updated.

### Open Items

- [ ] 4th-strike RETRO_GATE_VIOLATION (PRs #1119, #1120) — already tracked in Action Tracker #157 (GitHub #1085). Note added.
- [ ] CodeQL `js/tainted-format-string` self-review checkpoint — Action Tracker #165.
- [ ] Two-PR-split-for-high-blast-radius pattern doc — Action Tracker #163.
- [ ] Pre-mount/post-mount keyset invariant test as a generalizable pattern — Action Tracker #164.
- [ ] Bulk-dispatch-site refactor + count-test pattern — Action Tracker #166.
- [ ] v0.8.5 retro never written (this retro session backfilled per-PR retros for #1119/#1120 but a separate v0.8.5 milestone retro is also missing). Track separately — Action Tracker #167.

---

## v0.8.5 — async applyPatches foundation + 3 downstream-consumer gap fixes (PRs #1099, #1102, #1105, #1107)

**Date**: 2026-04-26 (released as v0.8.5rc1)
**Scope**: View Transitions PR-A (foundational `applyPatches` async signature change), `self.defer()` (Phoenix `send(self(), :foo)` parity), and 3 downstream-consumer-reported workarounds-replaced-with-features (`wizard_input_event`, T012 partial-suppress, `wizard_rendered_fields`).
**Note**: Backfill — this milestone shipped without a milestone retro at the time; entry written 2026-04-26 alongside the v0.8.6 retro.

### What We Learned

**1. Foundational refactors are zero-user-benefit in isolation; only the downstream uses justify them.**
PR #1099 (PR-A) made `applyPatches` async. Patch-loop body byte-identical. Public surface (`window.djust.applyPatches`) became a breaking change for any external hook code, with no compensating user-visible improvement in the same PR. The v0.8.6 milestone proved the investment paid back (3 features built on top), but v0.8.5 alone was a breaking signature change for a future capability.

**Action taken**: Closed — pattern validated by v0.8.6 outcomes. No tracker row needed; this is a v0.8.5 → v0.8.6 cross-arc finding noted in v0.8.6's What We Learned #6.

**2. JSDOM async-function-hoisting is a real test-environment quirk.**
PR-A hit it: async function declarations don't hoist to the eval host scope the way sync function declarations do under JSDOM. Solution: explicit `globalThis.djust.applyPatches = applyPatches` namespace export. Future async refactors should plan for this from day one rather than discovering it during test migration.

**Action taken**: Open — covered by CLAUDE.md addition in v0.8.6 PR #1116 (async-migration completeness-grep convention plus this hoisting note can fold into the same section).

**3. Setdefault doesn't overwrite caller-supplied None.**
PR #1102's `wizard_input_event` first attempt used `kwargs.setdefault('dom_event', self.wizard_input_event)`. A caller passing `dom_event=None` got `attrs[None]` in the rendered HTML — broken. Fix is explicit `if kwargs.get('x') is None: kwargs['x'] = default`. Caught by Stage 11.

**Action taken**: Closed — canonicalized in CLAUDE.md via PR #1116 (Action Tracker #103, then folded into v0.8.6 Process Canonicalizations).

**4. Mechanical-replacement PRs need N tests for N sites.**
PR #1102 had 5 mechanical attr-key swaps in `frameworks.py` but tests covered only 4; radio site missed. Stage 11 caught it; added test. Generic enough to canonicalize.

**Action taken**: Closed — canonicalized in CLAUDE.md via PR #1116 (Action Tracker #104).

**5. `Iterable[T]`-not-`list[T]` for membership-check parameters.**
PR #1107's `wizard_rendered_fields` annotated as `list | None`, but the code used `fname in filter_x` which works on any iterable. Stage 11 caught it; widened to documented `Iterable[str]` semantics + tests for tuple and set inputs.

**Action taken**: Closed — canonicalized in CLAUDE.md via PR #1116 (Action Tracker #108).

### Insights

- **Three downstream-consumer gap-fix PRs (#1102, #1105, #1107) all had Stage 11 catches.** Each was small (~30-100 LOC) and "looked easy", and each had one or two genuine 🟡 findings in review. Reinforces the rule: "small change → no Stage 11 catch" is a fallacy; Stage 11 finds non-obvious things across PR sizes.

- **PR-A (#1099) was the first to use the new pipeline-retro state-file flow** (skill update from PR #1083 context). Per-PR retro file landed cleanly; milestone retro was missed at the time (this entry is the backfill). Suggests the pipeline-run → pipeline-retro handoff still misses milestone-boundary detection.

### Review Stats

| Metric | #1099 | #1102 | #1105 | #1107 | Total |
|--------|------:|------:|------:|------:|------:|
| Issues closed | (PR-A foundation) | 1 (#1095) | 1 (#1096) | 1 (#1097) | 3 |
| Tests added | 12 | 14 | 5 | 12 | 43 |
| 🔴 Stage 11 | 0 | 0 | 0 | 0 | 0 |
| 🟡 Stage 11 | 0 | 3 | 2 | 3 | 8 |
| 🟢 Stage 11 | 2 | 0 | 0 | 2 | 4 |
| Findings fixed before merge | 0 | 3 | 2 | 3 | 8 |

### Open Items

- [x] All v0.8.5 process learnings folded into v0.8.6's PR #1116 CLAUDE.md update.
- [ ] No outstanding v0.8.5-specific tracker rows.

---

## v0.8.4 — Inheritance Round-Trip Bug + ROADMAP Sweep (PRs #1086, #1087)

**Date**: 2026-04-26
**Scope**: Two-PR milestone driven by a single high-cost issue. PR #1086 fixed #1081 (`|date` filter producing `&quot;Apr 25, 2026&quot;` in production) with a one-line change in `crates/djust_templates/src/inheritance.rs::nodes_to_template_string` — emit filter args verbatim instead of re-wrapping in `\"…\"`, since `parse_filter_specs` (#787) preserves source-form quotes. PR #1087 swept `ROADMAP.md` for staleness — verified ~30 unchecked items as already-shipped, annotated 12 as genuinely-pending with greppable evidence. v0.8.4rc1 tagged + PyPI verifier cron scheduled.
**Tests at close**: ~6,456 (29 cases in `tests/unit/test_filter_literal_args_1081.py` + 3 new Rust unit tests in `inheritance.rs::tests`).

### What We Learned

**1. Inheritance round-trip identity tests bypassed the parser contract.**
`nodes_to_template_string`'s existing `test_nodes_to_template_string_preserves_filters` test passed because it built `Node::Variable` directly with bare-string args (no quotes). But the parser's contract per #787 is that arg STRINGS RETAIN surrounding quotes (so the dep-tracking extractor can distinguish literals from bare-identifier variable refs). The test bypassed the parser entirely, so the round-trip was broken in production despite passing tests. Surfaced through PR #1086 against the reporter's actual 26,785-char `claims/examiner_dashboard.html` — simple inline templates never hit `nodes_to_template_string` and rendered correctly all along.

**Action taken**: Open — tracked in Action Tracker #158. New rule: every AST round-trip (inheritance, serialization, cache-rebuild) needs a "parse the source, round-trip, re-parse, assert AST equality" test driven from parser output, not from direct AST construction. PR #1086 added that round-trip case (`test_round_trip_through_resolve_inheritance_preserves_date_filter_arg_1081`).

**2. Multi-reopen issues need bit-exact repro before claiming root cause.**
Issue #1081 went through 4 reopens, each with a confidently-stated different root cause from the reporter (and 3 confidently-stated "found it" replies from me). All three of my framework-side theories tested clean against the published cp312 wheel SHA — and were wrong. Posted "smoking gun" comments based on theoretical-flow testing without a runnable script that reproduced against the reporter's exact environment. The actual fix landed in 5 minutes once the user provided direct project access at `/Users/tip/online_projects/Flexion/proposals/nyc_a downstream-consumer agency_claims/downstream-consumer/`.

**Action taken**: Open — tracked in Action Tracker #162. New triage process rule: on any issue with N≥2 reopens, refuse to post a root-cause claim without a runnable script that reproduces against the user's exact environment. Bit-exact diagnostic-script ask precedes any "found it" claim.

**3. Released-wheel matrix is stale relative to Python's release cadence.**
PyPI ships only cp310/11/12. Reporter was on cp314 → source build at install time → untested binary in their env. Spent significant cycles theorizing about cp314-specific PyO3 ABI / Cargo crate-version drift / toolchain delta, all of which were red herrings (the bug reproduces on cp312 too) but the matrix gap genuinely contributed to the misdiagnosis path.

**Action taken**: Open — tracked in Action Tracker #160 (GitHub #1089). Concrete change in `.github/workflows/release.yml`: `python-version: ['3.10', '3.11', '3.12', '3.13', '3.14']`. Issue spec includes design questions for the implementer (PyO3 ABI for 3.14, runner availability, abi3-stable wheel option).

**4. Silent filter parse-failure passthrough hid the real failure mode.**
The `|date` / `|time` filters' `Err(_) => Ok(value.clone())` arm in `crates/djust_templates/src/filters.rs` passes through unchanged when chrono can't parse the value. That was correct behavior for valid use cases (`|default` chained after `|date` to handle null/empty strings), but it surfaced the #1081 bug as "filter is broken" when really the format string had embedded quotes from the doubled-quote shape `""M d, Y""`. One `tracing::debug!("|date filter parse failed for value=... format=...")` line would have collapsed the multi-day investigation to a 5-minute diagnosis.

**Action taken**: Open — tracked in Action Tracker #161 (GitHub #1090). Single-PR scope, ~10-line change spanning the date/time/timesince/timeuntil filter arms.

**5. Stale `collectstatic` is a real "framework looks broken but isn't" trap.**
Mid-investigation, the reporter believed they'd found root cause as stale `staticfiles/djust/client.min.js` (Apr 22 / 0.5.5rc1 era) being served instead of the wheel's fresh copy. Turned out to be a partial truth — they had two distinct issues: stale static + the inheritance round-trip bug. Anyone running djust in production behind WhiteNoise / nginx / a CDN is vulnerable to the stale-static class on every wheel upgrade if they forget the `--clear` flag.

**Action taken**: Open — tracked in Action Tracker #159 (GitHub #1088). Proposed `djust.S0XX` Django system check that hashes `STATIC_ROOT/djust/client.min.js` against the wheel's `python/djust/static/djust/client.min.js` and warns on mismatch.

**6. ROADMAP.md staleness compounds into wasted iteration time.**
When picking the next "Quick Win" task, 6 of the 8 candidates I checked were already shipped — ROADMAP just hadn't been struck through. Each one cost a verification round-trip. PR #1087 swept the Priority Matrix + Quick Wins + Medium Effort + Major Features + Phoenix LiveView Parity Tracker sections (~30 items marked shipped with implementation paths inline; 12 items marked genuinely-pending with greppable evidence inline).

**Action taken**: Closed — shipped in PR #1087. The annotations + `*(verified: no … references in tree)*` markers mean the next person doing this exercise starts from a known-good baseline.

### Insights

- **The release process improvement from Action #155 worked.** v0.8.4rc1's bump-version commit staged 6 files (pyproject + Cargo.toml + Cargo.lock + CHANGELOG + 2 init files) in a single commit with no fix-pass needed. Skill's documented "3 files" was caught and corrected at v0.8.2rc1 via Action #155; this milestone validates the lesson is being applied.
- **User direction is load-bearing.** The "two PRs is the right ceiling before I see them" rule prevented merging #1086 prematurely with the wrong fix-shape. The eventual real fix (`nodes_to_template_string`) replaced what would have been a no-op test-only PR — exactly the kind of correction the review-gate exists for.
- **Direct project access trumps theory-side investigation.** Once the user shared the path to the reporter's downstream-consumer project, identifying the bug took two commands: read the resolved template, see the doubled `""M d, Y""`. Several hours of upstream-side variation testing produced no progress. Future similar investigations: ask earlier.
- **Pure docs-only PRs (#1087) still warrant the retro-artifact gate.** Action #157's "3rd-strike RETRO_GATE_VIOLATION" pattern from v0.8.3 milestones — small bookkeeping PRs bypass the retro gate — repeated here for both #1086 and #1087 (no `pr/feedback/retro-N.md` file). Keeping this milestone's retro entry as the canonical source of lessons rather than backfilling per-PR retro files for a 2-PR milestone where lessons are already captured in the issue+PR conversation.

### Review Stats

| Metric | PR #1086 | PR #1087 | Total |
|--------|----------|----------|-------|
| Tests added (Python) | 29 | 0 | 29 |
| Tests added (Rust) | 3 | 0 | 3 |
| 🔴 Findings (Stage 11) | 0 | 0 | 0 |
| 🟡 Findings | 0 | 0 | 0 |
| Findings fixed | 0 | 0 | 0 |
| CI failures | 0 | 0 | 0 |
| Lines changed (production) | 2 | 0 | 2 |
| Lines changed (tests + docs) | 240 | 222 | 462 |

### Process Improvements Applied

- **CLAUDE.md**: no changes
- **Pipeline template**: no changes
- **Checklist**: no changes (Action #162 will add a triage-checklist line for multi-reopen issues; out-of-scope-for-djust label since pipeline-skill repo owns that)
- **Skills**: no changes (Action #161 will add the parse-failure debug log; pipeline-skill repo follow-up)

### Open Items

- [ ] Item 1 — Inheritance round-trip identity tests must drive from parser output (Action Tracker #158)
- [x] Item 2 — Stale-`collectstatic` Django system check (Action Tracker #159, GitHub #1088) — resolved in v0.8.6 PR #1115
- [x] Item 3 — Expand release wheel matrix to cp313 + cp314 (Action Tracker #160, GitHub #1089) — resolved in v0.8.6 PR #1115
- [x] Item 4 — Debug-log when `|date` / `|time` filter parse fails (Action Tracker #161, GitHub #1090) — resolved in v0.8.6 PR #1115
- [ ] Item 5 — Demand bit-exact runnable repro before posting "root cause confirmed" on N≥2 reopen issues (Action Tracker #162)

### Issues filed during this milestone

- #1088 (tech-debt): Stale-`collectstatic` Django system check
- #1089 (tech-debt): Expand release wheel matrix to cp313 + cp314
- #1090 (tech-debt): Debug-log when `|date` / `|time` filter parse fails

---

## v0.8.3 — Docs Sweep + Pre-push Lint (PRs #1082, #1083)

**Date**: 2026-04-25
**Scope**: Solo-issue milestone for #1075 (the broader stale-MD sweep filed during v0.8.2 PR #1076 follow-up). Two-part PR shipped: 53 stale .md ref fixes across 16 docs files + new `make docs-lint` Makefile target + `scripts/docs-lint.py` + pre-push hook in `.pre-commit-config.yaml`. #1081 (`|date` filter JSON-quoting bug) explicitly deferred — real Rust template-engine bug needing focused attention. Smallest milestone since v0.7.4.
**Tests at close**: ~6,427 (no net change; `scripts/docs-lint.py` is self-validating, no new test file needed).

### What We Learned

**1. Edit-tool failure-mode + smoke-test discipline gap.**
PR #1083's `--changed-only` pathspec fix at `scripts/docs-lint.py` was first attempted via the Edit tool which returned a "File has not been read yet" error. The subsequent smoke-test ran against the unmodified file and reported "12 files scanned" — a plausible-looking number that gave false confidence the Edit had applied. Caught only on a second diagnostic pass that explicitly compared `docs/**/*.md` vs `docs/` pathspecs (12 vs 16). **Lesson: when Edit returns an error, the next smoke-test may falsely succeed against the unmodified file.** A successful smoke-test alone is NOT proof the Edit landed. Verify by either (a) reading the file post-Edit to confirm the change, or (b) constructing a smoke-test whose output explicitly differentiates fixed vs unfixed (here: "expect 16 files, not 12"). Same shape as Action #122 (post-commit verification) but at the Edit-tool level.

**Action taken**: Open — tracked in Action Tracker #156 (GitHub #1084).

**2. Three consecutive RETRO_GATE_VIOLATIONs for small bookkeeping PRs.**
PR #1082 (this milestone's ROADMAP-only setup PR) shipped without a retro at merge time — same shape as PR #1069 in v0.8.1 and PR #1073 in v0.8.2. Three milestones, three small bookkeeping PRs (single-file ROADMAP edits, ~15-20 LOC), all bypassing the retro-artifact gate. Backfilled in Stage 4 each time, but the pattern is established enough now to file as a fixable gate gap, not just an observation. The pipeline-run skill's retro-artifact gate fires for substantive PRs but not for hand-rolled ROADMAP setup PRs that bypass the pipeline-run skill entirely.

**Action taken**: Open — tracked in Action Tracker #157 (GitHub #1085).

**3. Marketing-cluster unlinking pattern preserves readability.**
For 12 of the 53 stale-MD refs, the target file (MARKETING.md, FRAMEWORK_COMPARISON.md, TECHNICAL_PITCH.md, WHY_NOT_ALTERNATIVES.md, MARKETING_NEXT_STEPS.md, README_MARKETING_SECTION.md) doesn't exist anywhere in the repo and there's no canonical replacement. Instead of removing the bullet entirely or linking to a placeholder, **drop the markdown link syntax** — `[Marketing Overview](MARKETING.md)` → plain text `Marketing Overview`. Bullets still read naturally. Pattern: when a referenced file no longer exists and there's no canonical replacement, unlink rather than delete or stub.

**Action taken**: Closed — pattern documented in PR #1083's commit message + this milestone retro; no separate tracker row needed. The fixer script (`/tmp/scratch/fix_stale_md_refs.py`) already encodes the pattern; future fix passes can lift from it.

### Insights

- **Mirror-existing-tooling-shape pattern compounded.** `scripts/docs-lint.py` was lifted from `scripts/roadmap-lint.py`'s structure byte-for-byte. The Action #152 "lift-from-downstream FIRST" pattern from v0.8.2 generalizes cleanly to "lift-from-codebase-precedent FIRST" — works the same way at intra-project scope.
- **Stage 11 review's pathspec finding was empirically verifiable.** The reviewer's claim "git's `docs/**/*.md` glob silently skips depth-1 files" was non-obvious enough to warrant verification — `git diff --name-only` returned 12 files for the glob vs 16 for `docs/`. Empirical verification at fix-pass time + a unit-style smoke-test that explicitly differentiates the two outputs would have caught the Edit failure earlier (see finding #1).
- **#1081 deferral was the right call.** Spending the drain cycle on the docs sweep + lint tooling, while leaving the `|date` filter JSON-quoting bug for explicit attention, kept the milestone tight (1 PR + 1 ROADMAP-setup PR) and avoided coupling unrelated investigation to a mechanical drain.
- **Net djust open-issue queue**: 25 → 24 (closed #1075 by PR #1083). 23 of 24 remaining are skill-level (`out-of-scope-for-djust-drain`); the 1 in-scope is #1081.
- **The retro-artifact gate has now produced 3 violations** in 3 consecutive milestones — all from the same class (small ROADMAP-only PR). Sample size is now sufficient to file the gap formally (finding #2).

### Review Stats

| Metric | #1082 | #1083 | Total |
|--------|-------|-------|-------|
| Tests added | 0 | 0 (script is self-validating) | 0 |
| Production LOC | +14 (ROADMAP) | +203 / -53 (16 docs + 4 infra) | +217 / -53 |
| 🔴 / 🟡 findings | 0 / 0 | 0 / 1 (Important non-blocking, addressed) | 0 / 1 |
| Pre-commit attempts | 1 | 2 (1 fix-pass for `--changed-only` pathspec) | 3 |
| CI retries | 0 | 0 | 0 |
| Quality rating | n/a | 4.5/5 | — |

### Process Improvements Applied

**CLAUDE.md**: No additions.

**Pipeline template**: No changes.

**Checklist**: No additions to `docs/PULL_REQUEST_CHECKLIST.md`.

**Skills**: 2 new tracker rows (#156, #157) — both skill-level, labeled `out-of-scope-for-djust-drain`. Each will need a follow-up PR to the pipeline-skill repo.

### Open Items

Tracked in Action Tracker:
- **#156** — Edit-tool failure-mode + smoke-test discipline gap (PR #1083 caught at fix-pass time).
- **#157** — Three consecutive RETRO_GATE_VIOLATIONs on small bookkeeping PRs (#1069, #1073, #1082) — extend retro-artifact gate or document explicit ROADMAP-PR exemption.

Closed-as-shipped this milestone:
- **#1075** — by PR #1083.

### Status

✅ v0.8.3 drain **COMPLETE**. 2 PRs merged, 1 issue closed-as-shipped. #1081 deferred for focused Rust-engine investigation.

---

## v0.8.2 — Theming Polish & Docs Cleanup (PRs #1073, #1074, #1076)

**Date**: 2026-04-25
**Scope**: 5 in-scope GitHub issues from docs.djust.org's testing — 4 theming/CSS bundled into Group T (PR #1074), 1 docs link cleanup as solo (PR #1076), bracketed by ROADMAP setup (PR #1073). All 5 issues closed-as-shipped. One follow-up issue (#1075) filed for the broader 50-ref / 17-file stale-MD sweep that surfaced at Stage 4 of PR #1076. Smallest milestone since v0.7.4.
**Tests at close**: ~6,427 (+7 new in `test_theming_v082_drain.py`; net delta from v0.8.1's ~6,420 baseline).

### What We Learned

**1. Lift-from-downstream FIRST became a real pattern.**
PR #1074's `prose.css` (#1009) was lifted near-verbatim from docs.djust.org's `static/css/input.css` — a ↔ pack bridge that had already been battle-tested against three theme packs in production. Time-to-implement was a fraction of "design from scratch": ~10 minutes to copy-and-generalize vs. the ~hour a clean-room implementation would have taken. The downstream consumer's working solution was the right starting point. Generalizes: when an issue cites a downstream consumer's working implementation, lift verbatim FIRST (preserves their proven shape), generalize SECOND (wrap with opt-in API like the `prose-djust` class).

**Action taken**: Open — tracked in Action Tracker #152 (GitHub #1077).

**2. Stage 5 smoke-test discipline (Action #145, v0.8.1) caught a real defect — first paid off in this milestone.**
First implementation of `theme_css_link` (#1012) used `state.get("pack")` because in-conversation memory said "ThemeState has dict-like API". `ThemeState` is actually a dataclass — calling `.get()` raises `AttributeError`. Caught at Stage 5 by running the new test (which I wrote BEFORE reading the dataclass definition). The Stage 5 smoke-test discipline filed as Action #145 in v0.8.1's retro is the canonical defense — and it just worked under fire. Two-PR sample, but this is the first time the pattern paid off on its own.

**Action taken**: Closed — Action #145 (Stage 5 smoke-test for new scripts/code) validated under fire in PR #1074 (#1012 dataclass-vs-dict catch). No new tracker row needed; existing #145 (GitHub #1060) covers it.

**3. Stage 11 mark_safe XSS-trace audit produced a one-paragraph review checklist addition.**
PR #1074's `theme_css_link` returns a `mark_safe`'d URL string. The Stage 11 reviewer subagent specifically traced the cookie inputs → through `registry.has_theme/has_preset` validation in `get_state()` → to the URL output, and confirmed there's no XSS surface (cookie inputs are server-validated before reaching the URL). This is a generalizable Stage 11 review pattern that doesn't currently exist in the project's PR checklist. Action: add a "for every new `mark_safe` call, trace inputs to a server-validated source" bullet to `docs/PULL_REQUEST_CHECKLIST.md` Stage 11 review section.

**Action taken**: Open — tracked in Action Tracker #153 (GitHub #1078).

**4. Stage 4 broader-sweep → follow-up issue scope-discipline reinforced.**
PR #1076's investigation of #1010's 4 cited stale .md refs found ~50 more across 17 files. Filed as follow-up #1075 instead of expanding scope to fix all 50. Same scope-discipline pattern that worked for v0.8.1's #1067/#1026 (rejected expanding into the broader observability/views.py path). Two consecutive milestones now demonstrate the pattern: when Stage 4 reveals a broader systemic issue, fix EXACTLY what the cited issue asks for, file follow-up for the systemic remainder. **Validated across 2 milestones; promote to a Stage 4 plan-template note.**

**Action taken**: Open — tracked in Action Tracker #154 (GitHub #1079).

**5. `make docs-lint` proposed alongside #142 `make roadmap-lint`.**
The broader stale-MD sweep that produced #1075 explicitly proposes a `make docs-lint` Makefile target wrapping a python sweep script (mirroring the `make roadmap-lint` shape from Action #142). Two pre-push lint hooks for the doc-tree would prevent the stale-ref class from regressing.

**Action taken**: Closed — `make docs-lint` proposal already documented in #1075's body. No separate tracker row needed; #1075 carries both the cleanup work and the tooling proposal as a paired action.

### Insights

- **5 issues → 2 substantive PRs + 1 ROADMAP PR** via `--group` mode. Group T bundled 4 theming issues (#1009, #1011, #1012, #1013) cleanly because all 4 touched `djust_theming/`; #1010 stayed solo because it was unrelated docs.
- **Stage 11 caught 0 🔴 / 0 🟡 across both substantive PRs.** Lower-novelty work (CSS, lift-from-downstream, small refactor, 4-line docs fix) — fewer surfaces for defects. Stage 11 still validated the structure (e.g. `mark_safe` audit on #1012).
- **Pre-existing patterns reused without prompting**: `globalThis.djustDebug` for the v0.8.0 cache-write log family stayed canonical, the `_assert_benchmark_under` xdist-safe contract was preserved verbatim by the v0.8.1 #1066 PR (no regression observed in v0.8.2 work), Stage 5 smoke-test discipline (Action #145) had its first real-world payoff.
- **Issue queue churn**: 25 open → 21 open at close. Closed 5 (#1009, #1010, #1011, #1012, #1013); filed 1 (#1075). Net -4. The remaining 21 are 17 skill-level (`out-of-scope-for-djust-drain`) + 4 misc.
- **Two RETRO_GATE_VIOLATIONs in two milestones** — PR #1069 in v0.8.1 (also a small ROADMAP PR), #1073 in v0.8.2 (also small ROADMAP PR). Both shipped without retros. Pattern: small bookkeeping PRs (ROADMAP-only, single-line) consistently miss the retro-gate. The pipeline-run skill's retro-gate fires for substantive PRs but not for hand-rolled ROADMAP setup PRs that bypass the pipeline-run skill entirely.
- **First milestone where the new pipeline-retro Stage 3.5 gate ran twice (v0.8.1 + v0.8.2).** The v0.8.1 retro filed 3 new tracker rows (#149-#151); this v0.8.2 retro adds 3 more (#152-#154). Gate is empirically holding — neither retro produced a `prose_only` Action taken: line.

### Review Stats

| Metric | #1073 | #1074 | #1076 | Total |
|--------|-------|-------|-------|-------|
| Tests added | 0 | 7 | 0 | 7 |
| Production LOC | +17 (ROADMAP) | +364 / -2 | +4 / -4 | +385 / -6 |
| 🔴 / 🟡 findings | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| Pre-commit attempts | 1 | 2 (1 ruff F401 auto-fix) | 1 | 4 |
| CI retries | 0 | 0 | 0 | 0 |
| Quality rating | n/a | 5/5 | 5/5 | — |

### Process Improvements Applied

**CLAUDE.md**: No additions this milestone.

**Pipeline template**: No changes.

**Checklist**: `docs/PULL_REQUEST_CHECKLIST.md` will receive the Stage 11 `mark_safe` audit bullet via Action Tracker #153 (skill-level work; lives in pipeline-skill repo for the broader Stage 11 review prompt, plus a parallel PR-checklist entry in djust if the bullet is also appropriate at the project level — Stage 4 plan will decide).

**Skills**: 3 new tracker rows (#152, #153, #154) all skill-level, labeled `out-of-scope-for-djust-drain`. Each will need a follow-up PR to the pipeline-skill repo.

### Open Items

Tracked in Action Tracker:
- **#152** — Lift-from-downstream FIRST pattern (PR #1074 / prose.css canonical example).
- **#153** — Stage 11 `mark_safe` XSS-trace audit bullet (PR #1074 / `theme_css_link`).
- **#154** — Stage 4 broader-sweep → follow-up issue scope-discipline (validated across v0.8.1 + v0.8.2).

Filed as follow-up GitHub issue (not in Action Tracker as a row, but referenced from #154 above):
- **#1075** — broader stale-MD sweep across 17 files + proposed `make docs-lint` Makefile target.

### Status

✅ v0.8.2 drain **COMPLETE**. 3 PRs merged, 5 issues closed-as-shipped, 1 follow-up issue filed. Smallest milestone since v0.7.4. The 4 theming/CSS issues from docs.djust.org's testing all shipped via PR #1074 (Group T bundling) — pattern of bundling related-cluster issues continues to work. v0.8.2 is shippable as a pre-release version (e.g. `0.8.2rc1`) if the user wants, or can roll into v0.9.0 alongside the deferred-features work.

---

## v0.8.1 — Reconcile Drain (PRs #1064–#1069)

**Date**: 2026-04-25
**Scope**: First milestone fully driven by the new pipeline-retro state-file gate. 15 tech-debt issues from the 2026-04-25 reconcile triaged into 7 groups; 5 shipped as substantive PRs (#1064–#1068), 2 closed at Stage 4 as moot/deferred (#1033, #1032), 1 docs-cleanup PR (#1069) bookkeeping the v0.9.0 backlog. 11 issues closed-as-shipped + 4 closed-at-Stage-4 + 5 deferred to v0.9.0 = 20 issues resolved (40 → 20 open tech-debt at close). Same shape as v0.7.4 but with explicit close-without-code paths exercised four times.
**Tests at close**: ~6,420 (+7 new JSDOM tests in PR #1068 mount-batch-fallback + 1 new behavior test in PR #1066 stack-trace-exposure; net delta from v0.8.0's ~6,355 baseline).

### What We Learned

**1. Stage 4 re-classification became the highest-leverage moment of the drain.**
Five separate Stage-4 reads of the cited code rewrote the work plan: **#1045** (regex already centralized; nothing to do), **#1048** (already shipped via PR #1021's 30ms budget bump), **#1033** (intentional naming divergence per migration guide), **#1032** (real v0.9.0 feature, not a refactor), and **#1067 / #1026** (filed as "Style-only follow-up", reading the diff revealed a stack-trace-exposure leak — promoted to Security). Four close-without-code decisions and one classification upgrade, all from re-reading the cited lines BEFORE writing any code. The original tracker entries — written months earlier in different milestones — encoded assumptions that were stale or wrong.

**Action taken**: Open — tracked in Action Tracker #149 (GitHub #1070).

**2. Doc-claim verification (Action #124) extends to prose docs with external references.**
PR #1064's first commit cited 10 PR numbers in `docs/internal/codeql-patterns.md`; the Stage 11 reviewer subagent spot-checked 3 via `gh pr view <N>` and found 7 mismatched. Each was a plausible-sounding hallucination ("#898 sounds like a security PR" — actually `feat(animations): dj-remove`). The fix-pass corrected all 10 against actual merged PR titles. **#124 was filed for code claims; this milestone empirically extends it to prose docs.** Generalization: any documentation that names external artifacts (PRs, issues, commits, file:line refs) must cross-check each citation at write time, not after.

**Action taken**: Open — tracked in Action Tracker #150 (GitHub #1071).

**3. Process error: committed to local main between drain groups (Group F).**
After Group E closed at Stage 4 with no code, I went back to `main` and started Group F's edits without first creating a new feature branch. The commit landed on local main. No origin damage — recovered via `git checkout -B feat/mount-batch-fallback-v081` (preserves commit) + `git checkout main && git reset --hard origin/main` (clean local). The pattern: in a multi-group drain, transitioning between groups is the highest-risk moment for branch-state confusion, because the "previous group's branch is gone" state looks like a clean main but isn't. **At the start of EVERY pipeline group/iteration, run `git checkout -B <branch-name> origin/main` BEFORE the first edit, regardless of working-tree state.** Costs 2 seconds; prevents the recovery dance.

**Action taken**: Open — tracked in Action Tracker #151 (GitHub #1072).

**4. The new pipeline-retro Stage 3.5 gate was dogfooded at PR-level retros throughout this milestone.**
Every per-PR retro (#1064, #1065, #1066, #1067, #1068) included a "Stage 3.5 classification: closed" section anticipating the gate's eventual use. This milestone-level retro is the FIRST formal invocation of the new gate. The gate itself is canonical at `~/.claude/skills/pipeline-retro/SKILL.md` and shipped to two downstream consumers via `johnrtipton/pipeline-skills#2` and `flexion/flexion-ai-claude-plugin#90`. Real-data validation: the gate caught 0 violations in this milestone's findings (because each per-PR retro had already pre-classified as `closed`) — but the discipline of the gate is what made each PR retro author write a canonical `Action taken:` line, not a prose-only one.

**Action taken**: Closed — pipeline-retro Stage 3.5 gate canonicalized at `~/.claude/skills/pipeline-retro/SKILL.md` and synced to flexion plugin (PR #90, commit `7d59524`); empirical validation in this milestone retro is the milestone-level proof.

### Insights

- **`--group` mode collapsed 15 issues into 5 substantive PRs + 2 close-at-Stage-4 decisions.** Initial plan was 7 groups → 7 PRs. Actual: 5 PRs (#1064, #1065, #1066, #1067, #1068) + 2 zero-code closes (E #1033, G #1032) + 1 bookkeeping ROADMAP PR (#1069). Grouping criterion (same files / same pattern) held; close-at-Stage-4 was a separate emergent path.
- **Pre-existing patterns reused without prompting**: `globalThis.djustDebug` (Action #1030 → #1031 reuse), `TYPE_CHECKING`-conditional imports (PR #1064 cites PR #924), `_assert_benchmark_under` xdist-safe contract (#1036 preserved verbatim across move). Action #100's "code-reuse dividend" continues to compound.
- **First-push clean merge ratio: 4/5 substantive PRs.** Only #1066 (test-infra) had a fix-pass (E402 ruff-bounce on first commit), and even that was caught by pre-commit not in CI. #1064 had a Stage 11 fix-pass (PR-citation drift). #1065/#1067/#1068 each shipped clean first-push.
- **Stage 11 caught 1 real defect this milestone (#1064 PR citations).** Lower than prior milestones because the work was lower-novelty (refactors, docs, small features). The streak (Action #125) holds — Stage 11 found something real on a doc PR, not a code PR.
- **Issue queue churn**: 40 open tech-debt → 20 open at close. 11 closed-as-shipped, 4 closed-at-Stage-4, 5 deferred to v0.9.0 ROADMAP. Net: drain succeeded; 17 of the 20 remaining are skill-level (`out-of-scope-for-djust-drain` label) intentionally excluded.
- **No new pre-commit stash/restore reinforcements** for Action #122 this milestone. The skill-level fix landed in `~/.claude/skills/pipeline-run/SKILL.md` post-v0.8.0rc1 is empirically holding.

### Review Stats

| Metric | #1064 | #1065 | #1066 | #1067 | #1068 | #1069 | Total |
|--------|-------|-------|-------|-------|-------|-------|-------|
| Tests added | 0 | 0 | 1 | 0 | 7 | 0 | 8 |
| Production LOC | +166 (docs) | +266 | +168 / -65 | +35 / -8 | +200 / -2 | +14 / -2 | +849 / -77 |
| 🔴 / 🟡 findings | 0 / 1 | 0 / 2 | 0 / 2 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 5 |
| Findings fixed | 1 (PR cites) | 0 cosmetic | 0 cosmetic | 0 | 0 | 0 | 1 / 4 demoted |
| Pre-commit attempts | 1 | 1 | 2 (E402) | 1 | 1 | 1 | 7 |
| CI retries | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Quality rating | 4/5 | 5/5 | 4.5/5 | 5/5 | 4.5/5 | 5/5 | — |

### Process Improvements Applied

**CLAUDE.md**: No additions this milestone. The patterns surfaced (Stage 4 re-classification, doc-claim cross-check, branch-from-target) all belong in the pipeline-skill, not djust's CLAUDE.md.

**Pipeline template**: No changes (the existing `feature-state.json` worked as-is across all 5 substantive PRs).

**Checklist**: `docs/PULL_REQUEST_CHECKLIST.md` already received the v0.7.4 additions (Action #134, #135). No new entries this milestone.

**Skills**:
- `~/.claude/skills/pipeline-retro/SKILL.md` — state-file-driven workflow + Stage 3.5 classification gate canonicalized (committed pre-milestone, dogfooded throughout).
- `~/.claude/skills/pipeline-run/SKILL.md` — MANDATORY Post-Commit Verification (Action #122) section landed pre-milestone.
- Three new tracker rows (#149, #150, #151) filed for Stage 4 re-classification discipline, doc-claim-verbatim-extends-to-prose-docs, and Stage 1 branch-from-target reminder. Each will need a corresponding GitHub issue and a follow-up skill update PR to the pipeline-skill repo.

### Open Items

Tracked in Action Tracker:
- **#149** — Stage 4 re-classification: re-read cited code before assuming tracker classification is right (drain-emergent pattern, 5 instances this milestone).
- **#150** — Doc-claim-verbatim TDD extends to prose docs with external references (Action #124 generalization).
- **#151** — Stage 1 branch-from-target reminder — even between drain groups (Group F process error).

Deferred to v0.9.0+ (already in ROADMAP per PR #1069):
- Component-level time-travel (was #1041)
- Forward-replay through branched timeline (was #1042)
- Phase 2 streaming — lazy-child + true server overlap (was #1043)
- ADR-006 AI-generated UIs (was #1044)
- `{% live_render %}` auto-detect preserved stickies (was #1032)

Skill-level (out-of-scope-for-djust-drain label, tracked in pipeline-skill repo work):
- 17 issues remain open under this label. Will be addressed via PRs to `johnrtipton/pipeline-skills` repo separately.

### Status

✅ v0.8.1 reconcile drain **COMPLETE**. 6 PRs merged, 15 issues from the original drain queue resolved (11 shipped + 4 close-at-Stage-4), 5 deferred to v0.9.0 ROADMAP, 17 skill-level issues labeled and held for separate disposition. The new pipeline-retro state-file gate had its first milestone-level invocation in this retro (this entry was written under the gate; Stage 3.5 classification surfaced the 3 new tracker rows above).

---

## v0.8.0 — Form Status Awareness + Server Actions (PRs #1023, #1024)

**Date**: 2026-04-25
**Scope**: Two of three planned v0.8.0 P2 features shipped: Form Status Awareness (PR #1023, `dj-form-pending` attribute — React 19 `useFormStatus` equivalent) and Server Actions (PR #1024, `@action` decorator — React 19 server-action pattern). Async Streams (Phoenix 1.0 parity) deferred to v0.8.1 — substantial Python + Rust VDOM stream-container work, didn't fit cleanly in this milestone's scope window. Same shape as v0.7.0rc1 (4 of 5 P2 shipped, Islands deferred).
**Tests at close**: ~6,355 (8 JS + 18 Python new = 26 added across 2 PRs).

### What We Learned

**1. Pairing client-side + server-side patterns into a single milestone unlocks React 19 ergonomics.** `dj-form-pending` and `@action` are independently useful but are designed to compose — `dj-form-pending` covers the in-flight UX (during the network round-trip), `@action` covers the post-completion state (after the handler returns). Form authors get the full React 19 form ergonomics by adopting both, with zero per-handler wiring on either side. This was the v0.8.0 thesis from the ROADMAP and it landed cleanly.

**Action taken**: Closed — observational only (cross-feature pairing). No new tracker row.

**2. Iter sizing rule: small-first opens the milestone shipping rhythm.** iter 1 (`dj-form-pending`) was ~80 LOC + 8 tests in 30 minutes. iter 2 (`@action`) was ~175 LOC + 18 tests in ~1 hour, building on infrastructure iter 1 had stress-tested (the form-submit dispatch path). If iter 2 had gone first, the cumulative engineering budget pressure would have been higher and iter 1's scope might have crept. Generalize: **always sequence the smallest design-novel iter first when bundling a multi-PR milestone**.

**Action taken**: Open — tracked in Action Tracker #140 (GitHub #1055).

**3. Building on existing infrastructure is the right shape for compound features.** `@action` doesn't reimplement dispatch — it wraps `@event_handler` and adds the state-tracking layer. Result: same parameter coercion, permissions, rate limits, CSRF guards. The wrapper is small (~50 LOC) because all the heavy lifting was already done. If `@action` had reinvented dispatch, this PR would have been 3-4× larger and Stage 11 would have caught security regressions. **The decorator-wraps-decorator pattern is the canonical shape for "add cross-cutting concern X to existing Y."**

**Action taken**: Closed — observational only (decorator-wraps-decorator pattern; already in @action docstring). No new tracker row.

**4. Scope-cut decision: defer Async Streams to v0.8.1 with explicit precedent reference.** Async Streams is Python + Rust VDOM work (~200 LOC Python + Rust stream container support). At session-end, that's not a clean shipping shape — it would either mean shipping under-tested code or working past the point of diminishing returns. The v0.7.0rc1 / v0.6.0rc1 precedents both cut rc1 with 4-of-5 P2 features shipped, deferring the largest deferred-eligible item. v0.8.0rc1 follows the same shape: 2-of-3 shipped, Async Streams deferred. **The right v0.8.0 isn't "all 3 features at once" — it's "the React 19 form pattern landed correctly, with Async Streams as a focused v0.8.1."**

**Action taken**: ROADMAP updated to mark iter 1 + iter 2 ✅ shipped; Async Streams entry remains under v0.8.0 milestone with a "deferred to v0.8.1" annotation pointing at the precedent.

**5. Pre-commit stash/restore — 8th occurrence, then resolved.** PR #1024 hit it during the Server Actions commit (lock-file drift caused stash, commit registered correctly on retry). At post-rc1 housekeeping, the cumulative case (8 reinforcements in single session — PRs #989, #996, #1007, #1008, #1014, #1015, #1021, #1024) was overwhelming, and **Action #122 was implemented at the skill level**: a new "MANDATORY Post-Commit Verification (Action #122)" section landed in `~/.claude/skills/pipeline-run/SKILL.md` documenting the failure mode, the canonical fix (`git commit -m "..." && git log -1 --oneline`), and the "never skip" rationale. The pattern self-validated on its own commit (the skill-update commit chained the verify and immediately confirmed the new commit hash).

> **Count discrepancy noted in reconcile pass**: PR #1024's per-PR retro logged `Pre-commit attempts: 1`, but this milestone finding lists #1024 among the 8 reinforcements. One of those is off by one. Likely the per-PR retro was written after the successful retry and didn't count the stash/restore cycle as a "pre-commit attempt." Not worth re-litigating — the cumulative pattern is what motivated the skill-level fix, and that fix has now landed.

**Action taken**: Action #122 closed (skill-level). Pattern documented in commit `5d2c44b8` to djust-repo RETRO.md.

**6. Six generalizable framework patterns surfaced in per-PR retros but were not in the milestone-level synthesis.** Caught during a post-rc1 reconcile pass that read PRs #1023 + #1024 retros from GitHub (the comment-form retros, since `pr/feedback/retro-{N}.md` files have not been generated for any PR since #993). Six concrete patterns worth promoting framework-wide:

- **Try/finally for error-path cleanup in async UI state** (PR #1023 — `_handleDjSubmit`). A submit handler that throws (network down, server 500, race with reconnect) must still clear pending state in `finally`, otherwise the form stays disabled forever. The "always clear in finally" rule is the canonical shape for any feature that toggles UI state around an async operation.
- **Sibling-form scope isolation** (PR #1023). `form.querySelectorAll('[dj-form-pending]')` scoped to the submitting form, not `document.querySelectorAll(...)`. Without this, a sibling form's loading state leaks. Generalizes to any DOM-modifying handler: scope to the originating element, not the document.
- **Forward-compat for unknown attribute values** (PR #1023). `dj-form-pending="future-mode"` silently does nothing instead of throwing. Future-extensible without breaking existing usage. Pattern: validate the modes you know about, no-op for the rest.
- **Re-run state semantics for stateful decorators** (PR #1024 — `@action`). On entry: clear stale `error` AND stale `result` simultaneously. Templates never see "old success result + new pending state" or "old error + new success result." Pattern locked with two regression tests (error→success and success→error).
- **Self-init defense for stateful decorator attrs** (PR #1024). If a subclass overrides `__init__` and forgets `super().__init__()`, the decorator initializes `_action_state` on demand instead of `AttributeError`-ing in production. Locked with `test_action_self_initializes_action_state`.
- **Bare-form + called-form decorator** (PR #1024). `@action` and `@action(description="…")` both work via the standard `if func is None` test pattern. Worth canonicalizing in the framework's decorator-authoring docs.

**Action taken**: Open — tracked in Action Tracker #139 (GitHub #1054).

### Insights

- **2 PRs / ~1.5 hours throughput** for v0.8.0. Faster than expected for design-novel features because both features built on stress-tested infrastructure (`_handleDjSubmit` for iter 1, `@event_handler` for iter 2).
- **First-push clean merge rate: 2/2** this milestone. Combined with v0.7.4 (2/2), v0.7.3 (3/3), v0.7.2 (6/6), the streak is now **13 consecutive first-push merges across four milestones**.
- **Action #125 streak now 18 consecutive pipelines** (#990 → #1024). Stage 7 user-flow trace discipline continues to work.
- **Issue queue clean for the third consecutive milestone.** No new issues filed during v0.8.0 work. v0.8.1 scope: Async Streams (the deferred v0.8.0 item).
- **Cross-feature design discipline.** Both v0.8.0 features document the pairing in their CHANGELOG entries — `dj-form-pending` references `@action` and vice versa. Readers see the composition story even if they only adopt one feature.

### Review Stats

| Metric | PR #1023 | PR #1024 | Total |
|---|---|---|---|
| Tests added | 8 (JS) | 18 (Py) | 26 |
| Production LOC | +80 (JS helper + wiring) | +175 (decorator + init + context inject + exports) | +255 |
| Test LOC | +329 | +290 | +619 |
| 🔴 / 🟡 findings | 0 / 0 | 0 / 0 | 0 / 0 |
| Pre-commit attempts | 1 | 1 | 2 |
| Pre-push attempts | 1 | 1 | 2 |
| CI retries | 0 | 0 | 0 |
| First-push clean merge | ✅ | ✅ | 2/2 |
| Bundle delta (gz) | +80 B | 0 (Python only) | +80 B |
| Quality rating | 5/5 | 5/5 | — |

### Process Improvements Applied

**Action Tracker (headline)**:
- #122 → **8th reinforcement, then CLOSED post-rc1** via skill update at `~/.claude/skills/pipeline-run/SKILL.md`. Highest-ROI session-arc technical-debt item resolved.
- #125 → **Validated across 18 consecutive pipelines** (no Stage 11 🔴 since v0.7.0 PR #989).
- No new rows filed this milestone — issue queue clean, design-novel work without retro-actionable findings.

**ROADMAP**: v0.8.0 marked partial-shipped (2 of 3); Async Streams annotated as deferred-to-v0.8.1.

**Pipeline-run / pipeline-ship skills**: No new checklist additions. Action #122 / #129 / #131 / #132 remain skill-level updates that haven't landed.

### Open Items

Tracked as Action Tracker rows above:
- **#122** — Post-commit verification (8th reinforcement)
- **#125** — Stage 7 user-flow-trace (Validated across 18 consecutive pipelines)
- **#129/#131/#132** — Skill-level updates (not exercised in v0.8.0)

Deferred from v0.8.0 to v0.8.1:
- **Async Streams (Phoenix 1.0 parity)** — `stream_async`, `stream_reset`, `stream_delete`, `stream_insert_at`. ~200 LOC Python + Rust VDOM stream-container support. Substantial focused-session work.

### Status

✅ v0.8.0rc1 **RELEASED** (tag pushed 2026-04-25 as commit `12e89fa8`). Form Status Awareness + Server Actions both landed clean; Async Streams deferred to v0.8.1. 2-of-3 shape matches v0.7.0rc1 / v0.6.0rc1 precedent. djust.org pin bumped to `djust>=0.8.0rc1`. Verification cron `trig_01W9pUmAai9DMMzt8owFtNQe` fires at 06:05 UTC to confirm PyPI + GitHub release artifacts. Action #122 (post-commit verification) landed at the skill level immediately post-rc1 — the highest-ROI session-arc technical-debt item is resolved.

---

## v0.7.4 — Retro Follow-ups: process & docs (PRs #1021, #1022)

**Date**: 2026-04-25
**Scope**: All five v0.7.2 + v0.7.3 retro Action Tracker rows that were filed as GitHub issues — now resolved. Two PRs: PR #1021 fixed the py3.14 timing-sensitive CI flake class (#1016 — phase-based time mock for `test_hotreload_slow_patch_warning` + 10ms→30ms budget for `test_broadcast_latency_scales`). PR #1022 bundled the four docs follow-ups into a single PR (closes #1017, #1018, #1019, #1020): two PR-checklist additions plus a new `docs/development/check-authoring.md` consolidating the v0.7.x check-refinement patterns.
**Tests at close**: ~6,326 (no net new tests; PR #1021 only modified existing test files, PR #1022 was docs-only).

### What We Learned

**1. Bundle-by-touched-file is the right grouping for docs PRs.** PR #1022 closed 4 issues (#1017, #1018, #1019, #1020) in a single PR because all four touched either `docs/PULL_REQUEST_CHECKLIST.md` or the new `docs/development/check-authoring.md`. Single review pass, single CI cycle, single merge — vs. four separate PRs each spending ~5-10 min in CI. Bundle eligibility rule: **same target file(s) + same review type (docs vs code) + small scope**. PR #1014's "engine path" Stage 4 plan template addition (Action #129) is the contrasting case — different target files, would not have bundled cleanly.

**Action taken**: Closed — observational only (bundle-by-touched-file is implicit in Action #132 territory). No new tracker row.

**2. `closes #X, #Y, #Z` only auto-closes the first issue.** PR #1022's title and body contained `closes #1017, #1018, #1019, #1020` but only #1017 auto-closed on merge. GitHub respects only the first "closes #N" pattern when comma-separated; subsequent IDs are treated as plain references. The fix is one-`closes`-keyword-per-issue (each on its own line). This gotcha is **already documented** in `docs/PULL_REQUEST_CHECKLIST.md` (Pre-Review Quick Checks: "Multiple issues must be listed one per line — do not combine them on a single line"). Despite that, a docs-PR for retro follow-ups violated the checklist that PR was UPDATING. Pure operator error; cost was three manual `gh issue close` calls.

**Action taken**: Closed — already documented in `docs/PULL_REQUEST_CHECKLIST.md` Pre-Review Quick Checks. Operator-discipline note.

**3. py3.14 fix worked first-push clean.** PR #1021 hardened `test_hotreload_slow_patch_warning` against extra `time.time()` calls from py3.14's asyncio scheduler (replacing the indexed-array mock with a phase-based mock) AND bumped `test_broadcast_latency_scales` budget from 10ms to 30ms. Both fixes landed clean on first push — Action #133's "pick one" suggestion (per-runner tolerance / `@pytest.mark.flaky` / non-required matrix) was answered with "two tailored fixes in one PR." Avoided introducing `pytest-rerunfailures` as a new dep.

**Action taken**: Action #133 closed (resolved by PR #1021).

**4. Pre-commit stash/restore — 7th occurrence.** Same pattern continues. PR #1021 hit it during commit (lock file drift caused stash, then commit rolled back). The cumulative reinforcement count is now 7 (PRs #989, #996, #1007, #1008, #1014, #1015, #1021). **Action #122 implementation is overdue** — but it's a skill-level update (`~/.claude/skills/pipeline-run/SKILL.md`), not a djust-repo task. Filing one more reinforcement note doesn't help; the skill-level work has to actually land.

**Action taken**: Closed — Action #122 closed at skill level 2026-04-25 (~/.claude/skills/pipeline-run/SKILL.md MANDATORY Post-Commit Verification section).

### Insights

- **2 PRs / ~30 min throughput** for v0.7.4. Smallest milestone in session. Sustainable only when the issues are pre-triaged retro follow-ups with explicit acceptance criteria.
- **First-push clean merge rate: 2/2** this milestone. Combined with v0.7.3 (3/3) and v0.7.2 (6/6), 11 consecutive first-push merges across three milestones.
- **Action #125 streak now 16 consecutive pipelines** (#990 → #1022). The Stage 7 user-flow trace discipline filed in PR #989's retro is empirically working.
- **Issue queue genuinely clean again.** No new issues filed during the v0.7.4 drain. Same as end of v0.7.3 — but v0.7.3 had 3 retro-followup issues queued for v0.7.4. v0.7.4 retro filed zero new code-actionable issues; only #122 reinforcement (skill-level, not djust repo).
- **Five Action Tracker rows closed in single milestone** (#133, #134, #135, #136, #137). All were filed during v0.7.2 + v0.7.3 retros and resolved cleanly via the v0.7.4 drain shape. Validates the "retro Action Tracker → GitHub issue → drain milestone" flywheel from end to end.

### Review Stats

| Metric | PR #1021 | PR #1022 | Total |
|---|---|---|---|
| Tests modified | 2 | 0 | 2 |
| Production LOC | 0 | 0 | 0 |
| Test LOC delta | +13 / -8 | 0 | +13 / -8 |
| Doc LOC | +25 (CHANGELOG) | +216 (PR checklist + new guide + CHANGELOG) | +241 |
| 🔴 / 🟡 findings | 0 / 0 | 0 / 0 | 0 / 0 |
| Pre-commit attempts | 2 (#122 7th) | 1 | 3 |
| Pre-push attempts | 1 | 1 | 2 |
| CI retries | 0 | 0 | 0 |
| First-push clean merge | ✅ | ✅ | 2/2 |
| Quality rating | 5/5 | 5/5 | — |

### Process Improvements Applied

**Action Tracker (headline)**:
- #122 → **7th reinforcement** in single session. Skill-level update mandated.
- #125 → **Validated across 16 consecutive pipelines**.
- #133 → **Closed** (PR #1021).
- #134 → **Closed** (PR #1022).
- #135 → **Closed** (PR #1022).
- #136 → **Closed** (PR #1022).
- #137 → **Closed** (PR #1022).

**CLAUDE.md**: No additions this milestone.

**Pipeline-run / pipeline-ship skills**: No new checklist additions in this milestone. Action #122 / #129 / #131 / #132 all remain skill-level updates that haven't landed.

**docs**: New `docs/development/check-authoring.md` shipped — first dedicated check-authoring guide in the repo. Future check-author PRs reference it.

### Open Items

Tracked as Action Tracker rows above:
- **#122** — Post-commit `git log -1 --oneline` verification (7th reinforcement; skill-level update overdue)
- **#125** — Stage 7 user-flow-trace checklist (Validated across 16 consecutive pipelines)
- **#129/#131** — Stage 4 engine-path checklist (not exercised in v0.7.4)
- **#132** — pipeline-dev eligibility heuristic (not implemented)

Deferred from v0.7.4: None — all 5 originally-triaged issues shipped.

### New issues filed during v0.7.4 (candidates for v0.7.5/v0.8.0)

- **None.** Issue queue is clean for the second consecutive milestone. v0.8.0 scope must come from ROADMAP next-features (Server Actions, Async Streams, Form Patterns).

### Status

✅ v0.7.4 user-facing scope **COMPLETE**. All five originally-triaged retro follow-ups resolved. Ready for `v0.7.4rc1` cut.

---

## v0.7.3 — Check Refinements (PRs #1008, #1014, #1015)

**Date**: 2026-04-25
**Scope**: Three checks-area issues filed during the v0.7.2 drain triaged and resolved as a focused single-area drain. PR #1008 fixed `djust.C011` to detect stale/placeholder `output.css` (not just totally-missing files). PR #1014 stopped `djust.A070` from false-positiving on `{% verbatim %}`-wrapped `dj_activity` examples on docs / marketing pages. PR #1015 scoped `djust_theming.W001` contrast checks to the active preset by default (eliminating ~480 noise warnings on a typical project). All three behaviors landed in the system-checks subsystem; no other code paths touched.
**Tests at close**: ~6,326 Python (32 added across 3 PRs: 5+3 updated for #1008, 12 for #1014, 4+6 updated for #1015).

### What We Learned

**1. Single-area drains run unprecedented-fast — 3 PRs in ~1 hour.** v0.7.3 was the tightest drain in the session. Three issues, all in `python/djust/checks.py` or `python/djust/theming/checks.py`, all reported by docs.djust.org users with explicit repros and suggested fix sketches. Compare to v0.7.2 (6 PRs / ~3-4h, four design-novel + two template-fill) and v0.7.1 (4 PRs / ~2h, one design-novel + three template-fill). v0.7.3 was 0 design-novel + 3 template-fill: each fix had a clear "extend this helper" or "add this guard" shape. **The takeaway is not just "single-area is fast" but "user-reported issues with repro+fix-sketch are the optimal drain-iteration unit"** — the cognitive load of "what's the right shape?" is paid by the reporter, leaving the implementer to translate the sketch into production code + tests.

**Action taken**: Closed — observational only (user-issue triage prioritization). No new tracker row.

**2. Misleading existing tests are a load-bearing bug shape.** PR #1008's existing test `test_c011_passes_when_output_exists` had been writing an 18-byte placeholder file and asserting no C011 fired — exactly the bug #1003 was about. The test codified the broken behavior. Updating that test was part of the fix; without the update, the new content-sniff helper would have shipped with a contradictory existing-test that "passed for the wrong reason." Generalized: when a check claims to test X but its fixture exemplifies the broken behavior the issue describes, updating the test is mandatory, not optional. **Filed as Action #135** with proposed mitigation: a one-paragraph note in the PR review checklist + reviewer prompts.

**Action taken**: Action #135 filed. Concrete mitigation queued for next housekeeping pass — one-paragraph addition to `docs/PULL_REQUEST_CHECKLIST.md`.

**3. CSS `:has()` and whitespace-preserving redaction are now established patterns.** PR #1014 added the whitespace-preserving redaction pattern for line-number-aware regex scanners (filed as Action #136); PR #1007 (v0.7.2) had introduced CSS `:has()` for layout. Both are reusable across future check-authoring and CSS-shipping work — `:has()` for "wrap-when-attribute-present" CSS rules; whitespace-preserving redaction for "ignore this region of text but keep line numbers aligned" regex preprocessors (e.g. future `{% comment %}` / `<script>` redaction). Pattern documentation belongs in a check-authoring guide section.

**Action taken**: Action #136 filed (whitespace-preserving redaction pattern). Existing v0.7.2 docs already cover `:has()` use in the forms guide.

**4. Helper extraction creates clean test seams.** PR #1015's `_contrast_check_scope()` and `_presets_to_check()` helpers made the scope-decision testable in isolation — 4 small tests covered the four branches (default-active, opt-in-all, missing-preset, unknown-value) without dragging in the full Django settings stack. Compare to inline branching in the iterator: would have required full-stack mocking for each test. Generalized: when a check's behavior depends on a config-driven scope, extract the decision into a named helper. **Filed as Action #137**.

**Action taken**: Action #137 filed. Pattern documentation queued for the check-authoring guide.

**5. Pre-commit stash/restore — 6× reinforced in single session, mandate is unambiguous.** PRs #1008, #1014, #1015 each hit the gotcha (#4, #5, #6 occurrences respectively). Combined with #989, #996, #1007 from earlier, six occurrences in a single 24-hour drain session. **Action #122 status: top of next housekeeping pass, not optional.** The cumulative working-around cost (re-stage, retry, verify each commit) has long exceeded the implementation cost (one bash check in the skill markdown). Filing one more reinforcement isn't going to make the case any clearer.

**Action taken**: Closed — Action #122 closed at skill level 2026-04-25 (~/.claude/skills/pipeline-run/SKILL.md MANDATORY Post-Commit Verification section).

**6. CI was clean every time.** All three v0.7.3 PRs landed first-push clean — no CI retries, no flakes (no py3.14 timing issues this drain), zero 🔴 or 🟡 findings. Action #125 (Stage 7 user-flow trace) streak now extends to **14 consecutive pipelines** without a Stage-7-miss-Stage-11-catch defect (PRs #990 through #1015). The discipline is empirically working.

**Action taken**: Closed — observational only (Action #125 streak status update). No new tracker row.

### Insights

- **3 PRs / ~1 hour throughput** is the new ceiling for issue drains. Sustainable only when the queue is single-area + user-reported with repro+fix-sketch.
- **First-push clean merge rate: 3/3** (no CI retries this drain). Combined with v0.7.2's 6/6, the streak is now 9 consecutive first-push merges.
- **Cumulative-cost-of-not-implementing pattern.** Action #122's six reinforcements illustrate a general failure mode: a low-cost-to-implement skill update gets deferred while its cumulative working-around cost grows linearly with PR count. Worth noting as a meta-observation for future "implement this in the skill before next milestone" items — they should land at the END of the milestone they're filed in, not the START of the next one.
- **No new issues filed during the v0.7.3 drain** (verified: only the 3 originally-triaged remain). Issue queue genuinely clean for the first time since v0.7.0. v0.7.4 will need scope from outside the drain (ROADMAP next-features, not drain follow-ups).
- **Checks subsystem maturity.** v0.7.3 was three checks-area refinements in a row, all driven by real user reports. The check infrastructure shipped in v0.4-v0.6 is now in production-pressure mode; refinements like these are the right shape for v0.7.x patch releases. v0.8.0 should focus on new feature surface (ROADMAP P1/P2 items) rather than further check tuning unless new issues land.

### Review Stats

| Metric | PR #1008 | PR #1014 | PR #1015 | Total |
|---|---|---|---|---|
| Tests added | 5 | 12 | 4 | 21 |
| Tests modified | 3 (existing fixture updates) | 0 | 6 (autouse fixture) | 9 |
| Production LOC delta | +37 / -3 | +60 | +44 / -3 | +138 / -6 |
| Test LOC delta | +112 | +220 | +180 | +512 |
| 🔴 findings (Stage 7+8+11) | 0 | 0 | 0 | 0 |
| 🟡 findings | 0 | 0 | 0 | 0 |
| Pre-commit attempts | 2 (#122 4th) | 2 (#122 5th) | 2 (#122 6th) | 6 |
| Pre-push attempts | 1 | 1 | 1 | 3 |
| CI retries | 0 | 0 | 0 | 0 |
| First-push clean merge | ✅ | ✅ | ✅ | 3/3 |
| Quality rating | 5/5 | 5/5 | 5/5 | — |

### Process Improvements Applied

**Action Tracker (headline)**:
- #122 → **6th reinforcement** in single session (PRs #989, #996, #1007, #1008, #1014, #1015 all hit pre-commit stash/restore). Status: top of next housekeeping pass, not optional.
- #125 → **Validated across 14 consecutive pipelines** (no Stage 11 🔴 since v0.7.0 PR #989). Empirically working.
- #135 → **New** ("misleading existing tests are a load-bearing bug shape" pattern note for PR review checklist).
- #136 → **New** (whitespace-preserving redaction pattern for line-number-aware regex scanners).
- #137 → **New** (scope-decision helpers belong as named functions).

**CLAUDE.md**: No additions this milestone.

**Pipeline-run / pipeline-ship skills**: No new checklist additions in this milestone. Action #122 (post-commit verification) and Action #132 (pipeline-dev triage criteria from v0.7.1) both remain "must implement before next milestone" but are skill-level updates that didn't land in v0.7.3 either.

**Skills**: No skill changes during the v0.7.3 drain itself — the discipline is consistent enough that pipeline-ship continues to work for small surgical bug fixes (PR #1008) and pipeline-run-equivalent flow continues to work for everything else.

### Open Items

Tracked as Action Tracker rows above:
- **#122** — Post-commit `git log -1 --oneline` verification (6th reinforcement; implementation is now the very next housekeeping action)
- **#125** — Stage 7 user-flow-trace checklist (Validated across 14 consecutive pipelines)
- **#129/#131** — Stage 4 engine-path checklist (not exercised in v0.7.3; nothing touched the template-engine surface)
- **#132** — pipeline-dev eligibility heuristic (not implemented; skill-level update)
- **#133** — py3.14 timing-sensitive flake class (not exercised in v0.7.3; py3.14 was clean across all 3 PRs)
- **#134** — PR-checklist `_FRAMEWORK_INTERNAL_ATTRS` reminder (not exercised in v0.7.3)
- **#135** *(new)* — Misleading existing tests pattern note
- **#136** *(new)* — Whitespace-preserving redaction pattern note
- **#137** *(new)* — Scope-decision helper extraction pattern note

Deferred from v0.7.3:
- None — all 3 originally-triaged issues shipped.

### New issues filed during v0.7.3 (candidates for v0.7.4 drain)

- **None.** Issue queue is genuinely clean for the first time since v0.7.0. v0.7.4 scope must come from ROADMAP next-features rather than drain follow-ups.

### Status

✅ v0.7.3 user-facing scope **COMPLETE**. All three originally-triaged checks-area issues resolved. v0.7.3rc1 cut at commit `acb90e6b` (tag pushed 2026-04-25); GitHub Actions release workflow building wheels.

---

## v0.7.2 — Production Fixes & DX Polish — Issue Drain (PRs #998, #999, #1000, #1001, #1002, #1007)

**Date**: 2026-04-24
**Scope**: Six issues triaged and resolved in a single drain pass following v0.7.1rc1. Two real production bugs (`watchdog`-import NameError, Rust renderer `__str__` semantics gap), one docs+observability fix (`s3_events` `key_template` convention), one infra add (weekly real-cloud CI matrix for upload writers), one user-facing UX feature (inline radio buttons), one policy decision (ADR-012, close-without-code on `_FRAMEWORK_INTERNAL_ATTRS` rename). Six PRs total — 5 code-changing + 1 ADR-only.
**Tests at close**: ~6,294 Python (44 added: 3 + 18 + 3 + 3 auto-skip + 12 + 5 Rust unit; one milestone with cross-language coverage of every shipped change).

### What We Learned

**1. Drain pace correlates inversely with design novelty — confirmed across 2 milestones now.** v0.7.2 shipped 6 PRs in ~3-4 hours with first-push clean merges on all of them (one unrelated py3.14 flake retry on PR #1001). v0.7.1 shipped 4 PRs in ~2 hours under similar conditions. v0.7.0 took a full session day for 4 design-novel PRs. The pattern: when the issue queue is dominated by one-PR-shaped follow-ups (small bugs with reporter-provided repros, docs+observability fixes, ADR-only policy closes, and pattern-mirror features), drains run very fast. v0.7.2's mix was 2 design-novel (#968 Rust Display impl, #991 `:has()` CSS contract) + 4 template-fill (#994 stub classes, #964 docs+log, #963 workflow YAML+scaffold tests, #962 ADR). All six landed clean.

**Action taken**: Closed — observational only (drain-pace correlates with design-novelty). No new tracker row.

**2. Three-option design surfaces beat single-option proposals when the API shape isn't obvious.** PR #1007 (#991 inline radios) had three plausible API shapes: form-level flag, widget-attr+CSS, or template-tag variant. Surfacing all three with explicit pros/cons in a comment unblocked the user's choice in a single round-trip. Total design time: minutes, not hours. Once the user picked B (widget-attr+CSS), implementation was a mechanical translation — 12/12 tests green on first authoring pass (after one diagnostic step on Django widget mechanics). Compare to PR #976 (v0.6.1 time-travel) where the implementer invented an API shape unilaterally and got the wrong one — three rework cycles before alignment.

**Action taken**: Open — tracked in Action Tracker #141 (GitHub #1056).

**3. CSS `:has()` is now in the project's CSS toolkit.** PR #1007 used the `:has()` parent selector to walk up from a marked `<input>` to its containing wrapper — solving the "Django `attrs={...}` lands on the form-control element, not its container" problem with zero new Python. Browser support stable since late 2023 across Chromium 105+ / Safari 15.4+ / Firefox 121+, all current minimums in 2026. Worth adopting for any future "wrap-when-attribute-present" CSS rule. Documented one-paragraph note in the forms guide alongside the inline-radios section so future contributors see the pattern.

**Action taken**: No code change. Pattern documented in `docs/website/guides/forms.md`. Future similar features (inline checkboxes, segmented controls, etc.) follow the `[data-dj-X]` + `:has()` template.

**4. Pre-commit stash/restore gotcha — third reinforcement in a single 24-hour window.** PR #1007 hit it (ruff reformatted the test file during pre-commit, original commit didn't register). PRs #989 (1st) and #996 (2nd) hit it earlier in the session. Three occurrences in one drain. Action #122 (post-commit `git log -1 --oneline` verification step in the pipeline-run skill) was filed in v0.6.1 retro and reinforced in v0.7.1; this milestone makes it an unmistakable papercut. **The cost of NOT implementing it is now per-PR**, and the implementation cost is one bash check in the skill markdown.

**Action taken**: Closed — Action #122 closed at skill level 2026-04-25 (~/.claude/skills/pipeline-run/SKILL.md MANDATORY Post-Commit Verification section).

**5. py3.14 timing-sensitive flake class is real and worth a strategy.** PR #1001 hit `test_hotreload_slow_patch_warning` failing on py3.14 only (3.12/3.13 passed). PR #990 (v0.7.0) hit `test_broadcast_latency_scales[10]` failing on py3.14 only. Both passed on rerun. Two distinct tests, both timing-sensitive, both py3.14-specific. The py3.14 GitHub Actions runner appears to have different timing characteristics than py3.12/3.13 — slower scheduler granularity, GIL changes, or runner contention. **Generalized into Action #133** (renamed from the single-test #126).

**Action taken**: Action #133 filed as a class-level entry covering both tests + the strategy (per-runner tolerance OR `@pytest.mark.flaky(reruns=2)` OR move py3.14 to non-required check). Track next 3-4 py3.14 runs; if a third test joins the class, prioritize the fix.

**6. Close-without-code via ADR is the right shape for policy decisions.** PR #1002 / ADR-012 closed #962 (the long-deferred `_FRAMEWORK_INTERNAL_ATTRS` rename question) without writing any code. The ADR lists 4 alternatives with rejection reasons + the mitigation (PR review checklist reminder filed as Action #134). The mechanism beats letting the question rot in retro Open Items across multiple milestones. v0.6.1 retro carried "decide #962" as an open item; v0.7.0 retro deferred it again; v0.7.1 retro repeated the deferral. v0.7.2 closed it. **The shape — issue → ADR → mitigation as Action Tracker row → close — is now the canonical close-without-code pattern.**

**Action taken**: Closed — observational only (close-without-code via ADR pattern; already documented). No new tracker row.

### Insights

- **Six issues, six PRs, ~3-4 hours.** Throughput unprecedented in the project. Sustainable only when the queue is template-fill-heavy.
- **First-push clean merge rate: 6/6** (one CI retry on a flake — counted as clean since the rerun passed without a code change). v0.7.1 also achieved 4/4. Action #125 (Stage 7 user-flow trace) now empirically validated across 11 consecutive pipelines (#990 + #993 + #995 + #996 + #997 + #998 + #999 + #1000 + #1001 + #1002 + #1007). The class of defect that plagued #976/#988/#989 (code does a thing, thing doesn't reach the user) has not recurred since #125 was filed in v0.7.0.
- **Cross-language test discipline is the v0.7.2 milestone's most-extended feature of the test suite.** PR #999 introduced 5 Rust unit tests on `Value::Object::Display` PLUS 13 Python integration tests through `render_template` — locking the contract at both the Rust API surface and the user-facing PyO3 entry point. Future similar Rust+Python contracts should follow the dual-layer pattern. Bookend with PR #993 (v0.7.1, Stage 5b dual-engine recovery via cross-template testing) — both PRs proved that a single language's test suite is structurally insufficient for hybrid-codebase features.
- **Issue arrival rate during the drain.** Three new issues appeared (#1003, #1004, #1005) during the v0.7.2 drain — none from the drain PRs themselves (verified). The signal: external (or scheduled-job) issue creation runs at ~1 issue per 1-2 hours of dev time on a healthy project. Plan future drains to budget headroom for issues that arrive *during* the drain.
- **`make ci-mirror` (shipped v0.7.1) demonstrably works as the pre-push safety net.** All v0.7.2 PRs passed CI on first push (modulo the unrelated py3.14 flake on #1001). The `ci-mirror` discipline catches coverage / xdist / sub-suite-skip surprises that would otherwise burn a CI cycle.
- **Auto-merge cron agents stayed quiet.** v0.6.1rc1 / v0.7.0rc1 / v0.7.1rc1 verification crons all ran in the background without interfering with the drain. The scheduling pattern (cron at known time + emit confirmation comment) scales without operational overhead.

### Review Stats

| Metric | PR #998 | PR #999 | PR #1000 | PR #1001 | PR #1002 | PR #1007 | Total |
|---|---|---|---|---|---|---|---|
| Python tests added | 3 | 13 | 3 | 0 | 0 | 12 | 31 |
| Rust tests added | 0 | 5 | 0 | 0 | 0 | 0 | 5 |
| Auto-skip integration tests | 0 | 0 | 0 | 3 | 0 | 0 | 3 |
| Production LOC delta | +22 | +18 | +17 | 0 | 0 | 0 | +57 |
| Test LOC | +112 | +225 | +56 | +158 | 0 | +169 | +720 |
| Doc LOC | +25 | +25 | +35+33 | +20 | +102 (ADR) | +71 | +311 |
| 🔴 findings (Stage 7+8+11) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 🟡 findings | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Pre-commit attempts | 1 | 1 | 1 | 1 | 1 | 2 (#122) | 7 |
| Pre-push attempts | 1 | 1 | 1 | 1 | 1 | 1 | 6 |
| CI retries | 0 | 0 | 0 | 1 (#133 flake) | 0 | 0 | 1 |
| First-push clean merge | ✅ | ✅ | ✅ | ✅ (after rerun) | ✅ | ✅ | 6/6 |
| Quality rating | 5/5 | 5/5 | 5/5 | 4.5/5 | 5/5 | 5/5 | — |

### Process Improvements Applied

**Action Tracker (headline)**:
- #122 → **Reinforced 3rd time** in single session (PRs #989, #996, #1007 all hit pre-commit stash/restore). Status updated; implementation now top of next housekeeping pass.
- #125 → **Validated across 11 consecutive pipelines** (no Stage 11 🔴 since v0.7.0 PR #989). Status: thoroughly validated; no further follow-up required for the rule itself.
- #126 → **Generalized into #133** (timing-sensitive py3.14 flake class — now covers both `test_broadcast_latency_scales[10]` and `test_hotreload_slow_patch_warning`).
- #133 → **New** (py3.14 timing-sensitive CI flake class — broader than #126).
- #134 → **New** (PR review checklist reminder for `_FRAMEWORK_INTERNAL_ATTRS` — ADR-012 mitigation).

**CLAUDE.md**: No additions this milestone.

**Pipeline-run / pipeline-ship skills**: No new checklist additions in this milestone — Action #122 (post-commit verification) and Action #132 (pipeline-dev triage criteria from v0.7.1) both remain "must implement before next milestone" but are skill-level updates that didn't land yet.

**Skills**: pipeline-ship validated as the right tool for small surgical bug fixes (PR #998 — 15-LOC fix, no planning stage, full quality gates kept); used pipeline-run-equivalent flow for production changes; ADR-only flow validated for policy closes (PR #1002).

### Open Items

Tracked as Action Tracker rows above:
- **#122** — Post-commit `git log -1 --oneline` verification step (3rd reinforcement; implementation now overdue)
- **#125** — Stage 7 user-flow-trace checklist (Validated across 11 consecutive pipelines)
- **#129/#131** — Stage 4 engine-path checklist (not exercised in v0.7.2; nothing touched the template engine surface)
- **#132** — pipeline-dev eligibility heuristic (not implemented; skill-level update)
- **#133** *(new)* — py3.14 timing-sensitive flake class
- **#134** *(new)* — PR-checklist `_FRAMEWORK_INTERNAL_ATTRS` reminder (ADR-012 mitigation)

Deferred from v0.7.2:
- None — all 6 originally-triaged issues shipped or closed.

### New issues filed during v0.7.2 (candidates for v0.7.3 drain)

- **#1003** — `djust.C011` doesn't catch stale/placeholder `output.css` (only totally-missing file). Bug, P2.
- **#1004** — `djust.A070` false positive on `{% verbatim %}`-wrapped `dj_activity` examples. Bug, P2.
- **#1005** — `djust_theming.W001` should only run contrast checks on the active pack, not all discovered packs. Tech-debt, P2.

These appeared during the v0.7.2 drain (not caused by the drain PRs themselves). Triage candidates for v0.7.3.

### Status

✅ v0.7.2 user-facing scope **COMPLETE**. All six originally-triaged issues resolved. Ready for `v0.7.2rc1` cut.

---

## v0.7.1 — Sub-path Deploys, DX Tooling, and Test Hygiene (PRs #993, #995, #996, #997)

**Date**: 2026-04-24
**Scope**: Four PRs merged in ~2 hours. PR #993 introduced the `{% djust_client_config %}` template tag (dual-registered for Django + Rust engines per the `djust_markdown` precedent) + `window.djust.apiPrefix` / `djust.apiUrl(path)` helpers, replacing the hardcoded `/djust/api/` prefix in `48-server-functions.js` and unblocking sub-path deploys (`FORCE_SCRIPT_NAME`). PR #995 added `make ci-mirror` — a DX target that mirrors the exact CI pytest invocation locally. PR #996 swapped the hand-rolled Redis mock in `test_security_upload_resumable.py` for `fakeredis` (net −19 LOC + real Redis semantics). PR #997 mirrored PR #993's pattern onto the SSE fallback transport (`03b-sse.js:44`), closing the same class of bug there.
**Tests at close**: 6,250+ Python (15 added in #993; 0 in #995; 0 net in #996 (refactor); 3 added in #997 = 18 net new tests across the milestone).

### What We Learned

**1. Pattern-reuse is the v0.7.1 milestone dividend.** PR #993 established the `{% djust_client_config %}` + meta-tag + `djust.apiUrl()` pattern with full design thinking (planner-first, Stage 5b dual-registration recovery, Stage 12 parity-test fix-pass). PR #997 reused the pattern mechanically — 3 tests, +46 B bundle, first-push clean merge in a fraction of the engineering time. This is exactly what "templates ship dividends" means in practice: the second instance of an established pattern takes a fraction of the effort. Both `djust_markdown` (v0.7.0) and `djust_client_config` (v0.7.1) are now canonical examples of dual-engine tag registration; future template-tag work has two reference implementations.

**Action taken**: Closed — observational only (pattern reuse in PR body). No new tracker row.

**2. Ship velocity unprecedented in session — and the difference is structural, not cosmetic.** v0.7.1 merged 4 PRs in ~2 hours, all first-push clean merges, zero CodeQL retries, zero 🔴 findings at Stage 11. v0.7.0 took a full session day for 4 PRs with 3 consecutive 🔴 caught at Stage 11. The structural difference: 3 of the 4 v0.7.1 PRs were template-fill "apply established pattern" work (PR #995 Makefile tooling, PR #996 mock swap, PR #997 mirror of PR #993). Only PR #993 required new design thinking. **The right takeaway is not "we got faster" but "small bounded PRs that apply known patterns are the optimal velocity unit."** When the Action Tracker is full of one-PR-shaped follow-ups (mock swap, tooling target, pattern mirror), drains run very fast.

**Action taken**: Closed — observational only (velocity-vs-design-novelty planning input). No new tracker row.

**3. Stage 11 🔴 streak holding at 0 across 5 pipelines.** PRs #990, #993, #995 (no Stage 11 — pipeline-dev), #996 (no Stage 11 — pipeline-dev), #997 → all 0 🔴 at Stage 11 (or no regressions in pipeline-dev's live-verify substitute). Action #125 (Stage 7 user-flow trace), introduced after the #976/#988/#989 streak of broken-end-to-end PRs, is demonstrably working — that class of defect has not recurred since #125 was filed. Two-PR validation became five-PR validation in a single session.

**Action taken**: Open — tracked in Action Tracker #125 (GitHub #1047) — validated across 18+ pipelines.

**4. pipeline-dev (condensed flow) empirically validated for tooling/test-only PRs.** PR #995 (Makefile target — pure DX) and PR #996 (test-only mock swap — pure refactor) both used the condensed pipeline-dev flow: no subagent reviews, no separate Stage 7/8/11. Both shipped clean and the user manually live-verified each. Calling this empirically: when a PR doesn't change production code paths, the full 14-stage pipeline is overhead. **Heuristic: PR is pipeline-dev-eligible if it touches only one of {Makefile, scripts/, docs/, tests/}** AND **has zero changes under `python/djust/` or `crates/`**. Production code changes always go through `pipeline-run`.

**Action taken**: Open — tracked in Action Tracker #132 (GitHub #1052).

**5. Pre-commit hook stash/restore gotcha hit twice in the session.** PR #996 hit it (ruff cleaned an unused import in the stashed tree, original commit didn't register, had to re-stage). PR #989 hit it earlier in the v0.7.0 milestone. Both occurrences had the same shape: pre-commit reformat in the stashed working tree silently drops the original commit. Action #122 (`git log -1 --oneline` post-commit verification) was filed in v0.6.1; this milestone's data confirms the priority — the failure mode is real and recurring.

**Action taken**: Closed — Action #122 closed at skill level 2026-04-25 (~/.claude/skills/pipeline-run/SKILL.md MANDATORY Post-Commit Verification section).

**6. The action-tracker flywheel demonstrably works — `make ci-mirror` exists BECAUSE of the gap Stage 5b of PR #993 surfaced.** PR #993's Stage 5b caught the dual-engine 500 because the FULL pre-push suite ran (not the targeted subset Stage 6 ran). That was filed as a process note under #129. The very next PR (PR #995) shipped `make ci-mirror` — a one-shot way to run the full CI test invocation locally, which is the same gap closed for ALL future PRs. This is the Action Tracker working as a flywheel: one pipeline's lesson became the next pipeline's tooling fix in the same session. Worth calling out as a victory for the process itself, not just for the individual PRs.

**Action taken**: Closed — observational only (action-tracker flywheel observation). No new tracker row.

### Insights

- **Velocity correlates inversely with design novelty.** v0.7.1: 4 PRs / ~2 hours, 1 design-novel + 3 template-fill. v0.7.0: 4 PRs / full session day, 4 design-novel. When the Action Tracker is full of one-PR-shaped follow-ups, drains run very fast. Plan milestones to alternate: design-novel sprint → template-fill drain → design-novel sprint.
- **First-push clean merge rate of 4/4 this milestone.** Compare v0.7.0: 0/4 first-push merges (each PR had Stage 7 or Stage 11 fixes before merge). The combination of Action #125 (Stage 7 user-flow trace) + pattern reuse (no novel design surface in 3 of 4 PRs) is the cleanest path-to-merge observed in any milestone.
- **Pipeline-dev complements pipeline-run rather than replacing it.** PR #993 (production code, novel design) → pipeline-run, full 14 stages, Stage 5b recovery. PRs #995/#996 (tooling, test-only) → pipeline-dev, condensed. PR #997 (production code, mirrored pattern) → back to pipeline-run with full reviews. The right tool per PR shape, not a monoculture.
- **Bundle delta stays small.** PR #993: +148 B gz. PR #997: +46 B gz. Combined: ~194 B gz across two production-feature PRs. The "no-build-step" client.js philosophy held — apiUrl + meta-tag reader + sseUrl helper in under 200 B together.
- **Auto-merge cron agents (v0.6.1rc1, v0.7.0rc1) ran successfully in parallel with the v0.7.1 drain.** v0.6.1rc1 fired cleanly earlier in the session; v0.7.0rc1 fired at 20:47 UTC during this milestone. No interference with the v0.7.1 PR pipelines.
- **Template-reuse PRs need their own retro discipline.** PRs #995/#996/#997 didn't get individual retro files (they ran condensed flow). For pipeline-dev runs, the milestone retro IS the per-PR retro. That's correct for low-novelty PRs, but track in case any milestone-only retro proves insufficient for surfacing class-of-bug findings.

### Process Improvements Applied

**Action Tracker (headline)**:
- #100, #101, #123 → Closed by PRs #995, #996, #993 respectively.
- #130 → New + closed same-day by PR #997 (SSE FORCE_SCRIPT_NAME — same class of bug, pattern mirror).
- #131 → New (generalize Action #129's "Engine path" Stage 4 bullet beyond template tags to any feature touching template rendering).
- #132 (proposed; tracked under "Open Items" below) → pipeline-run skill should list pipeline-dev-eligible PR shapes explicitly.
- #125 status upgraded: "Validated across 5 pipelines."
- #122 reinforced: 2nd session-occurrence of pre-commit stash/restore gotcha.

**CLAUDE.md**: No additions this milestone.

**Pipeline-run / pipeline-ship skills**: No new checklist additions this milestone — Action #132 (pipeline-dev triage criteria) is the next candidate but not yet drafted.

**Skills**: pipeline-dev empirically validated for tooling/test-only PRs (PRs #995, #996). Recommend formalizing eligibility criteria in the next skill update.

### Review Stats

| Metric | PR #993 | PR #995 | PR #996 | PR #997 | Total |
|---|---|---|---|---|---|
| Python tests added | 5 | 0 | 0 (refactor) | 3 | 8 |
| JS tests added | 6 | 0 | 0 | 0 | 6 |
| Regression tests added | 1 | 0 | 0 | 0 | 1 |
| Stage 12 dual-engine parity tests | 3 | 0 | 0 | 0 | 3 |
| **New tests total** | **15** | **0** | **0** | **3** | **18** |
| 🔴 findings (Stage 7+8+11) | 0 | n/a (pipeline-dev) | 0 | 0 | 0 |
| 🟡 findings | 1 (Stage 11) | n/a | 0 | 0 | 1 |
| Findings fixed pre-merge | 1/1 | n/a | n/a | n/a | 1/1 |
| Pre-commit attempts | 1 | 1 | 2 (stash/restore retry) | 1 | 5 |
| Pre-push attempts | 2 (Stage 5b) | 1 | 1 | 1 | 5 |
| CI retries | 0 | 0 | 0 | 0 | 0 |
| Stage loops | 1 (Stage 5b) | 0 | 0 | 0 | 1 |
| Bundle delta (gz) | +148 B | 0 | 0 | +46 B | +194 B |
| Quality rating | 5/5 | 5/5 (pipeline-dev) | 5/5 (pipeline-dev) | 5/5 | — |

### Open Items

Tracked as Action Tracker rows above:
- **#122** — Post-commit `git log -1 --oneline` verification step (Reinforced — 2nd occurrence in the session)
- **#125** — Stage 7 user-flow-trace checklist row — **Validated across 5 pipelines**
- **#129** — Stage 4 engine-path declaration for template tags — **Partially validated; generalized into #131**
- **#131** — Engine-path Stage 4 bullet generalized beyond template tags
- **#132** — pipeline-run skill should list pipeline-dev-eligible PR shapes (Makefile / scripts/ / docs/ / tests/-only changes; zero `python/djust/` or `crates/` deltas) — filed as tracker row

Deferred from v0.7.1:
- None — milestone scope was narrow (FORCE_SCRIPT_NAME class + DX tooling + test hygiene). All planned items shipped.

### Status

✅ v0.7.1rc1 **RELEASED** (tag pushed 2026-04-24 as commit `88487dc6`). FORCE_SCRIPT_NAME / sub-path deploy support landed across `@server_function` / `@event_handler(expose_api=True)` / SSE fallback transports. DX gap (`make ci-mirror`) closed. Test hygiene (fakeredis swap) closed. GitHub release `v0.7.1rc1` published with wheels for cp310/cp311/cp312 (Linux x86_64, macOS x86_64/arm64, Windows) + sdist; djust.org pin bumped to `djust>=0.7.1rc1`. Verification cron `trig_01NEJySzkLBjyy9Dc3behF1v` fires at 22:49:59 UTC to confirm PyPI + GitHub release artifacts.

---

## v0.7.0 — Navigation, RPC, Activity, Admin Widgets, and Streaming Markdown (PRs #986, #988, #989, #990)

**Date**: 2026-04-24
**Scope**: Four of five planned P2 items shipped. dj-prefetch + `@server_function` RPC (#986), `{% dj_activity %}` block tag + `ActivityMixin` (React 19.2 `<Activity>` parity) (#988), Django admin widget slots + `BulkActionProgressWidget` + `@admin_action_with_progress` + A072/A073 system checks (#989), and Rust-side streaming Markdown via `pulldown-cmark 0.12` + `{% djust_markdown %}` + A090 check + Python `djust.render_markdown` helper (#990). Islands of interactivity (P3) deferred to v0.7.1.
**Tests at close**: ~130 new tests across the milestone (16 Py + 8 JS in #986; 17 Py + 6 JS in #988; 32 Py in #989; 24 Rust + 14 Py/tag + 3 A090 in #990).

### What We Learned

**1. Action #125 (Stage 7 user-flow trace) was the milestone's most important process finding.** Three consecutive pipelines (PRs #976 v0.6.1, #988, #989) each had Stage 11 catch a 🔴 that Stage 7 had rubber-stamped. All three followed the same shape: *code does a thing, but the thing doesn't reach the user.* PR #986 — `JsonResponse(..., encoder=DjangoJSONEncoder)` outside the invoke try/except (response-layer). PR #988 — fire-and-forget `loop.create_task` flush breaking the documented same-round-trip contract (transport-layer). PR #989 — `HttpResponseRedirect` returned from an `@event_handler` silently dropped (dispatch-layer). #125 was introduced in PR #989's retro and applied for the first time in PR #990 — which returned APPROVE with 0 🔴, breaking the 3-pipeline streak. Single-PR correlation, not proof; but the signal is strong enough to consider #125 a validated process upgrade pending one more data point.

**Action taken**: #125 filed and marked Validated (single datapoint) in the Action Tracker. Stage 7 output template grows a "User flow trace" section per user-visible feature.

**2. Action #124 (doc-claim-verbatim TDD) paid off twice in two milestones.** Introduced in PR #988's retro (4th consecutive milestone with doc-vs-code drift), applied for the first time on PR #989 (5 rule tests RED-first: log level, A073 gate, LRU cap, generic-error split, cooperative cancel — each caught a real drift). Applied again on PR #990 where the pattern demonstrated its peak value: 9/9 Stage 7 fix-pass tests passed first-run against the unmodified implementation (strongest possible positive signal), AND 2 pulldown-cmark 0.12 API surprises (`ENABLE_HTML` non-suppression; `ENABLE_GFM_AUTOLINK` nonexistent) were caught by RED rule tests *before* the XSS surface could ship open. #124 evolved from aspirational process reminder to executable TDD discipline over the course of two PRs.

**Action taken**: Open — tracked in Action Tracker #124 (GitHub #1046) — validated partial.

**3. Planner-first continues to be the highest-leverage stage.** Every one of the 4 PRs had the Plan agent make structurally-significant corrections to the briefing before Stage 5. PR #986: added WIRING_CHECK + URL-route-precedence analysis. PR #988: flagged `live_tags.py` (not `djust.py`), JS slot `49-` (not `24-`), `11-event-handler.js` (not `09-event-binding.js`), `_FRAMEWORK_INTERNAL_ATTRS` was a no-op. PR #989: re-scoped from a stock-admin `DjustAdminMixin` (would have duplicated ~60% of `admin_ext/`) to `DjustModelAdmin` slot extension — saved a milestone of future consolidation. PR #990: identified PyO3 registration belongs in `djust_live` (not `djust_templates`), tag handler in `parser.rs`/`renderer.rs` (not `tags.rs`). Four pipelines, ~14 planner corrections, one structural wrong-turn avoided.

**Action taken**: Closed — observational only (planner-first locked, no new work). No new tracker row.

**4. Rust + PyO3 cross-language PRs introduce new security-surface risks.** PR #990 surfaced two pulldown-cmark 0.12 API surprises — both caught by RED rule tests, not by reading the crate's documentation first. `Options::ENABLE_HTML` omission does NOT suppress `Event::Html` emission; a custom event filter (`sanitise_event`) is required. `Options::ENABLE_GFM_AUTOLINK` doesn't exist in 0.12 (was renamed/restructured upstream). If the RED tests had been any less thorough, both would have shipped silently.

**Action taken**: Filed Action #128 — for any external crate (Rust or Python) whose API forms part of a security boundary (markdown/HTML parsers, regex/escapers, serde deserializers, URL parsers), Stage 4/5 must read the *actual doc.rs entry for the specific API surface we use* — not just the README or a copy-pasted example. Stage 4 plan template grows a "linked doc.rs section" row.

**5. Quality trajectory across the milestone trended up.** PR #986 4/5 (2 🟡 at Stage 11, clean merge). PR #988 4/5 (3 🔴 + 2 🟡 at Stage 7, single-commit fix-pass). PR #989 3/5 (1 🔴 at Stage 11 — redirect-drop — pulled the feature back from shipping cosmetically broken). PR #990 4.5/5 (0 🔴 at Stage 11; streak broken; #125 validated). Stage 11 🔴 count trended 1 → 1 → 1 → 0 across the milestone — correlating directly with the introduction of Action #124 in #988 and Action #125 in #989.

**Action taken**: Closed — observational only (quality trajectory positive signal). No new tracker row.

**6. CodeQL pattern — `py/undefined-export` on lazy `__all__` — continued.** PR #989 tripped `py/undefined-export` on `BulkActionProgressView` listed in `__all__` behind a `noqa: F822` suppression; ruff's local lint was silenced, CodeQL's independent analyzer flagged it. Same taxonomy as the three prior v0.6.x/v0.7.0 CodeQL-catches-what-ruff-missed occurrences (PRs #966, #970, #975). Action #121 (centralize `_SCRIPT_CLOSE_TOLERANT_RE`) is unrelated but same shape — lint suppressions drifting into CodeQL visibility.

**Action taken**: Open — tracked in Action Tracker #146 (GitHub #1061).

### Insights

- **Sequential quality improvement curve.** Stage 11 🔴 count trended 1 → 1 → 1 → 0 across the 4 PRs. The improvement correlates with Action #124 introduction (#988) and Action #125 introduction (#989 retro → applied in #990). This is the first milestone where the Action Tracker demonstrably shifted the failure curve mid-flight.
- **Cross-language PRs cost ~1.5 hours more than solo-language.** PR #990 was Rust + Python + PyO3 + docs; the other three were Python-only (with small JS additions). Cross-language wiring verification has to be checked at every seam (Rust → PyO3 → Python helper → Python tag handler → Django template → HTML render). Not a bug, an observation — budget explicitly for future multi-language pipelines.
- **`--group` mode not useful at this scale.** All 4 v0.7.0 PRs shipped solo. Natural groupings didn't emerge because each feature touched a distinct subsystem (RPC/dispatch, mixins/client, admin_ext, Rust+PyO3). `--group` remains correct for "same domain, disjoint files" batches (like v0.5.0/v0.5.1 batches of small polish items) — not for primitive-level features.
- **PR retros are now a genuinely useful signal source.** This milestone retro was composed from 4 quality retros (retro-986.md, retro-988.md, retro-989.md, retro-990.md) in ~10 minutes. The Stage 11 code-review front-matter + the retrospective sections combine into a dense, citation-ready input for milestone-level synthesis. Previous milestones had to reconstruct findings from PR comments and CI logs — this one read like a research paper.
- **Planner + Action #125 + Action #124 compose cleanly.** Planner corrects the plan before Stage 5. #125 traces the user flow at Stage 7. #124 locks doc claims at Stage 7. Each adds a non-overlapping review surface. PR #990 was the first pipeline where all three landed cleanly at once — the cleanest review result followed naturally.
- **Every v0.7.0 PR surfaced a doc-vs-code drift issue.** #986 (`function_error` envelope promise unimplemented), #988 (hidden-ancestor-anywhere gate rule + bare-identifier A070 false-positive), #989 (log-level drift + redirect-drop headline claim), #990 (no-autolinks guide claim unlocked). 4-for-4 milestone pattern. #124's "doc-claim-verbatim tests BEFORE implementation" discipline now non-optional.

### Process Improvements Applied

**Action Tracker (headline)**: Mid-milestone addition of Actions #124 and #125 is the most important delivery of this retro. #124 introduced at #988 and validated on #989/#990. #125 introduced at #989 and validated on #990.

**CLAUDE.md**: No additions this milestone. The #124/#125 disciplines live in the pipeline stage checklists rather than CLAUDE.md — they're review-stage rules, not global conventions.

**Pipeline-run / pipeline-ship skills**: Stage 7 output template now includes a "User flow trace" section per user-visible feature (#125). Stage 4 plan template now includes a "linked doc.rs section for each external security-boundary API" row (#128).

**Checklist additions**:
- Stage 7: for every user-visible feature, trace the happy-path user story end-to-end (HTTP request → server dispatch → response envelope → browser render/navigation). Not just "read the diff." (#125)
- Stage 7: for every documented rule in the guide/docstring, point to the asserting test. (#124)
- Stage 9: re-count tests AFTER Stage 7/12 fix-pass deltas; diff against CHANGELOG before the final docs pass. (#127)
- Stage 4: for any external crate (Rust or Python) whose API forms part of a security boundary, read the actual doc.rs entry for the specific API surface we use. (#128)

### Review Stats

| Metric | PR #986 | PR #988 | PR #989 | PR #990 | Total |
|---|---|---|---|---|---|
| Python tests added | 16 | 17 | 32 | 14 | 79 |
| JS tests added | 8 | 6 | 0 | 0 | 14 |
| Rust tests added | 0 | 0 | 0 | 24 | 24 |
| A-check tests added | 0 | 0 | 6 (A072+A073) | 3 (A090) | 9 |
| Stage 7 fix-pass tests added | 0 | 6 | 5 | 9 | 20 |
| Stage 12 doc-lock tests added | 0 | 0 | 0 | 1 | 1 |
| **New tests total** | **24** | **29** | **43** | **51** | **~147** |
| 🔴 findings (Stage 7+8+11) | 0 | 3 (Stage 7) | 1 (Stage 11) | 0 | 4 |
| 🟡 findings | 4 | 4 | 7 | 10 | 25 |
| Findings fixed pre-merge | 3/4 | 7/7 | 8/8 | 10/10 | 28/29 |
| Pre-commit attempts | 1 | 1 | 1 | 1 | 4 |
| CI retries | 0 | 0 | 1 (CodeQL `py/undefined-export`) | 1 (flaky perf test) | 2 |
| Stage loops | 0 | 0 | 0 | 0 | 0 |
| Bundle delta (gz) | +832 B | +643 B | 0 | 0 | +1,475 B |
| Quality rating | 4.5/5 | 4/5 | 3/5 | 4.5/5 | — |

### Open Items

Tracked as Action Tracker rows #123–#128 above:
- **#123** — FORCE_SCRIPT_NAME / mounted sub-path support for JS clients (GitHub #987; v0.7.1 target)
- **#124** — doc-claim-verbatim tests before implementation — **Validated (partial)**
- **#125** — Stage 7 user-flow-trace checklist row — **Validated (single datapoint; confirm next pipeline)**
- **#126** — flaky perf test triage (`test_broadcast_latency_scales[10]`)
- **#127** — Stage 9 test-count recount rule
- **#128** — external-crate doc.rs read before implementation for security-surface deps

Deferred from v0.7.0 to v0.7.1:
- **Islands of interactivity (P3)** — see ROADMAP.md Priority Matrix line 44 and parity tracker line 1042. No implementation started; framework carrier (`{% live_render %}`, `register_block_tag_handler`) already exists. Target: v0.7.1.

### Status

✅ v0.7.0 user-facing scope **COMPLETE (4 of 5 P2 shipped)**. Four P2 features merged: dj-prefetch + `@server_function`, `{% dj_activity %}`, Django admin widgets + bulk progress, streaming Markdown. Islands of interactivity deferred to v0.7.1. Ready for `v0.7.0rc1` cut.

---

## v0.6.1 — Hot Reload, Streaming, and Time-Travel Debugging (PRs #974–#976)

**Date**: 2026-04-24
**Scope**: Three developer-experience deliverables shipped in a single autonomous pipeline: Hot View Replacement (React Fast Refresh parity), Phase 1 streaming initial render (chunked HTTP response), and time-travel debugging with a state-history ring buffer. AI-generated UIs (ADR-006) and Phase 2 streaming were deferred to v0.6.2.
**Tests at close**: 6,216 Python / 1,360 JS.

### What We Learned

**1. Stage 11 remains indispensable — demonstrated twice this milestone.** PR #975 had a 🟡 doc-overclaim that Stage 11 caught (guide described "browser parses head while server computes body" when Phase 1 only delivers transport-layer chunked transfer). PR #976 had **two 🔴 that pre-commit missed entirely** — a dead-WS-path in the tab click handler (`globalThis.djust.websocket` doesn't exist) and a snapshot reference-aliasing bug. The three-layer review model (Self-Review + Security + Stage 11) is not over-engineered: pre-commit Self-Review is necessary but NOT sufficient. Stage 11's independent runtime-data-flow trace catches things Self-Review cannot.

**2. The snapshot-aliasing bug was a latent v0.6.0 bug in `enable_state_snapshot`.** Same `_capture_snapshot_state` helper. `self.items.append(...)` after snapshot was rewriting every prior snapshot via reference because the "snapshot" held the live container. Nobody had tested mutation-after-capture for two milestones. Fixed in PR #976 with a `json.loads(json.dumps(...))` roundtrip — which also silently fixes the v0.6.0 state-snapshot feature. **Action #115**: any capture function needs a test that exercises mutation after capture.

**3. Scaffolding-no-plumbing pattern struck twice more — now reliably caught, but shifted shape.** PR #976 alone had 3 instances in a single PR (actor/component paths uninstrumented; timeline click handler missing; client history never populated) — all caught by pre-commit Self-Review. The pattern is now reliably surfaced by Self-Review on first pass. BUT Stage 11 then caught a fourth scaffolding bug of a different flavor: `globalThis.djust.websocket` was an invented API shape, not a missing wire. **Action #113/#114**: pre-commit should grep for stubbed API shapes in JSDOM tests; implementation agents must grep "how do other callers do X" before writing send-path code.

**4. Planning agent's "reuse existing infrastructure" finding saved PR #974.** Planner read `hotreload()` handler at `websocket.py:3305` and discovered it already did template re-render + VDOM diff + patch send. HVR became a ~70 LOC additive pre-step instead of a parallel pipeline. ~130 LOC saved, divergent bug surface avoided. This is the sixth consecutive iteration where planner-first design caught an integration-shape decision before the implementer duplicated work.

**5. Doc-accuracy-vs-code-reality is the sticky final 🟡.** PR #975 guide overclaimed "server overlap" when Phase 1 delivers only transport-layer chunked transfer. Same pattern as v0.6.0 PR #969 (sticky LiveView demo), PR #971 (package sunset described non-existent `djust.admin`), and PR #972 (cProfile single-run disclaimer). Five consecutive PRs with this finding class. **Action #116**: require implementation agents to trace the data flow of each claimed benefit before writing user-facing docs — the implementer wrote "browser parses head while server computes body" without tracing `get()` to verify that's actually what happens.

### Insights

- **Retro-artifact gate (shipped v0.5.7) held through three more PRs — zero dropouts.** Pattern is locked in for the rest of v0.6.x.
- **Three-layer review model stays canonical.** Every PR ran Self-Review + Security + Stage 11. When Self-Review missed, Stage 11 caught. When Self-Review caught, Stage 11 validated. No PR would have been safe with fewer than all three layers.
- **"Grep before you invent" is the next planner check.** The `globalThis.djust.websocket` greenwashing bug is subtle: the test passed, the feature looked wired, the review of the implementation couldn't tell at a glance whether the API was real. The only defense is requiring implementers to cite the existing caller of any symbol they consume — or planning agents to surface "here is how existing code sends WS frames."
- **Bundle-size budget held.** +1.2 KB gzipped across three features (HVR 357 B + time-travel debug 789 B + client 80 B; streaming 0 B), under the notional 2 KB-per-module soft ceiling per PR.
- **Latent bugs in merged features get found during neighbor-feature work.** The v0.6.0 `_capture_snapshot_state` aliasing bug would not have been found by a test-it-harder sweep — it took time-travel debugging (PR #976) using the same helper differently to expose the mutation path. General lesson: feature-adjacency audits sometimes find more than targeted sweeps.
- **Implementation agent's self-reported regression counts are unreliable.** PR #974 fix-pass reported "4046 passed" when the actual full suite was 6085 — agent must have run a filtered subset. Always verify full-suite count against `make test` tail output yourself.

### Process Improvements Applied

During the milestone we shipped the three features without pausing for skill/CLAUDE.md edits. Follow-ups to address in a post-milestone sweep (tracked in Action Tracker):

- **pipeline-run skill** — add `git log -1 --oneline` post-commit sanity check (Action #122).
- **pipeline-run skill** — pre-commit Self-Review should grep for stubbed JSDOM API shapes (Action #113).
- **Planning stage** — require "how do other callers do X" check for any client-side WS/API-consuming feature (Action #114).
- **Implementation agents** — must trace data-flow of claimed benefits before writing user-facing docs (Action #116).
- **Test discipline** — mutation-after-capture required for any snapshot/capture function (Action #115).
- **Codebase** — centralize `_SCRIPT_CLOSE_TOLERANT_RE` (Action #121 — third hit of the same CodeQL rule).

### Review Stats

| Metric | PR #974 | PR #975 | PR #976 | Total |
|---|---|---|---|---|
| LOC | +1,901 | +700 | +2,100 | +4,700 |
| Python tests added | 23 | 39 | 40 | 102 |
| JSDOM tests added | 3 | 0 | 8 | 11 |
| Bundle delta (gz) | +357 B | 0 | +789 B debug + 80 B main | +1,226 B |
| 🔴 pre-commit | 2 | 1 | 3 | 6 |
| 🔴 Stage 11 | 0 | 0 | 2 | 2 |
| 🟡 pre-commit (total) | 4 | 5 | 6 | 15 |
| 🟡 Stage 11 | 0 | 1 | 3 | 4 |
| CodeQL iterations | 0 | 2 (script-regex) | 0 | 2 |
| CI iterations | 1 | 2 | 2 | 5 |

### Open Items

Tracked as Action Tracker rows #113–#122 above:
- **#113** — pre-commit Self-Review greenwashing-catcher (stubbed API shape grep)
- **#114** — planning-stage "grep for how OTHER callers do X" check
- **#115** — mutation-after-capture test discipline
- **#116** — doc-accuracy data-flow trace for implementation agents
- **#117** — component-level time-travel (v0.6.2)
- **#118** — forward-replay through branched timeline (v0.6.2)
- **#119** — Phase 2 streaming: lazy-child + true server overlap (v0.6.2)
- **#120** — ADR-006 AI-generated UIs (deferred to v0.6.2)
- **#121** — shared `_SCRIPT_CLOSE_TOLERANT_RE` constant (tech-debt, 3rd CodeQL hit)
- **#122** — post-commit `git log -1` sanity check in pipeline-run skill

Row #105 (substring-matching tests sweep) from v0.6.0 marked Closed — resolved by discipline across all three v0.6.1 PRs (HTML-parsed assertions consistently used).

### Status

✅ v0.6.1 user-facing scope **COMPLETE**. Three headline developer-experience features merged: Hot View Replacement, streaming initial render (Phase 1), and time-travel debugging. Ready for `v0.6.1rc1` cut. ADR-006 AI-generated UIs and Phase 2 streaming deferred to v0.6.2.

---

## v0.6.0 — Production Hardening, Interactivity, and Advanced UX Primitives (PRs #885–#973)

**Date**: 2026-04-23
**Scope**: v0.6.0 shipped as 9+ merged features across multiple autonomous-pipeline sessions. This retro consolidates the 9 PRs merged in the final autonomous run (#965, #966, #967, #969, #970, #971, #972, #973) plus the earlier-merged v0.6.0 work (dj-mutation, dj-sticky-scroll, dj-track-static, runtime-layout-switching, WS compression). Headline features shipped: dj-transition CSS enter/leave, FLIP + skeleton animation, embedding primitive, sticky LiveViews (ADR + demo + scroll/attr preservation), service-worker advanced features, package-consolidation sunset, performance profiling guards, and `@starting-style` browser-native confirmation.
**Tests at close**: 6070+ Python, 1349+ JS.

### What We Learned

**1. Pre-commit Self-Review is load-bearing, NOT optional on "small" PRs.**
The three-layer review model (Self-Review + Security + Stage 11) caught 19 🔴 pre-commit findings across 4 PRs where Stage 11 alone would have let many ship. Two PRs (#969 sticky demo, #971 package sunset) tried to skip Self-Review as "mostly docs" and each got caught with multiple 🔴 accuracy bugs (5 total) at Stage 11 instead. The skipping-is-cheaper hypothesis is falsified; the cost of fixing 🔴 at Stage 11 (commit cycle + CI re-run + review re-post) exceeds the cost of a 3-minute Self-Review agent. **Rule going forward: Self-Review runs on EVERY PR with new code, including templates, JS modules, and user-facing docs with code examples.**

**2. "Scaffolding shipped, plumbing missing" is a distinct failure mode.**
PR #970 (SW advanced features) had wire-level scaffolding (message handlers, frame types) but 2 of 3 features were dead code end-to-end — no application path invoked the new APIs. Tests passed because they fired the wire handlers directly. Self-Review caught all three (`_clientState` never populated, popstate race, `cacheVdom`/`lookupVdom` never called). **Lesson: every new exported function/method needs a WIRING_CHECK — grep for callers; if the only callers are tests, the feature is unwired.**

**3. Substring-match tests mask critical bugs.**
PR #966 (embedding primitive) had 6 🔴 of which most were caught BECAUSE the fix pass mandated rewriting tests to use `html.parser` instead of substring matching. The original tests asserted `'view_id="X"' in rendered_html` — which passed while `view_id=X` was actually lodged OUTSIDE the tag as text content (not as an attribute). **Lesson: any test that checks rendered-HTML attributes must parse HTML. Applied consistently in subsequent PRs (#967, #969, #970) and caught zero new regressions of the same class.**

**4. Planner-discovered scope icebergs.**
PR #966 planning agent found that djust had `hasattr`-gated stubs for child-view embedding but no production implementation. Sticky LiveViews (originally one ROADMAP line) became 3 PRs (#967 attr preservation, #969 ADR + demo, supporting scroll preservation). Per-phase scope stayed tractable (~1-2k LOC per PR) instead of a single 2,600-LOC blob. **Lesson: planning-stage subagent is worth its context cost when the ROADMAP entry touches the framework's surface — the planner catches iceberg patterns that implementers miss.**

**5. Browser-native features sometimes mean zero framework work.**
PR #973 (`@starting-style`) was a pure docs PR — djust's VDOM insert path already honors browser-native CSS, so the deliverable was confirming + documenting rather than implementing. **Lesson: before committing to framework support for a new browser capability, ask whether the native capability already works through the existing insert path.**

**6. CI xdist is incompatible with pytest-benchmark stats.**
PR #972 caught this the hard way — local tests passed (sequential) but CI failed all 8 benchmarks because `benchmark.stats["mean"]` raises under `--benchmark-disable` (auto-set by xdist). Fix is a small guard helper. **Lesson: local verification must include `pytest -n auto --benchmark-disable` for any PR adding benchmark assertions — that's the exact CI invocation.**

### Insights

- **Three-layer review (Self-Review + Security + Stage 11) is canonical for v0.6.x.** Every PR that ran all three had 0 🔴 at Stage 11. Every PR that skipped one had 🔴 or 🟡 at Stage 11.
- **Planning subagent for features touching core framework surfaces.** Saved 2-3 scope icebergs across the run.
- **Mandatory retro-artifact gate (from v0.5.7).** Zero retro dropouts across 9 PRs.
- **Bundle-size budget held.** Nine new features added ~3.8 KB gzipped cumulatively to the client — well under a notional 10 KB-per-minor budget.

### Process Improvements Applied

**CLAUDE.md additions needed**:
- "Parse HTML, don't grep" rule for rendered-HTML tests.
- "WIRING_CHECK" — grep for non-test callers of every new exported function/method.
- "Run `pytest -n auto --benchmark-disable` locally before push if adding benchmark assertions."

**Pipeline-run skill updates needed**:
- Never skip Self-Review on any PR with new code, INCLUDING templates/docs with code examples.
- Post-retry commit messages: don't pull from `git log HEAD --pretty=%B` (risks pulling wrong message after hook-modified retry).

**Checklist additions**:
- New template tag / JS module → grep for 3 real caller sites before marking Implementation stage passed.
- New benchmark assertion → verify under both `--benchmark-only` AND `-n auto --benchmark-disable`.

**Skill updates shipped during the run**:
- (none — deferred to post-milestone sweep)

### Review Stats

| Metric | #965 | #966 | #967 | #969 | #970 | #971 | #972 | #973 | Total |
|---|---|---|---|---|---|---|---|---|---|
| Python tests added | 21 | 24 | 22 | 2 | 37 | 0 | 8 | 0 | 114 |
| JS tests added | 12 | 7 | 13 | 2 | 12 | 0 | 0 | 0 | 46 |
| 🔴 Pre-commit | 5🟡 | 6 | 5 | 0 | 3 | 0 | 0 | 0 | 19 🔴 / 15 🟡 |
| 🔴 Stage 11 | 0 | 0 | 0 | 3 | 0 | 2 | 0 | 0 | 5 |
| CodeQL iters | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 1 |
| CI iters | 1 | 2 | 1 | 2 | 1 | 1 | 2 | 1 | 11 |
| Bundle +gzipped | +780 B | +368 B | +680 B | +6 B | +1749 B | 0 | 0 | 0 | +3583 B |

### Open Items

Tracked as Action Tracker rows #105–#112 above. Note: row #83 (stale docstring `websocket.py:494` from #966) was resolved in PR #969 and is already closed upstream. Each new open row needs a GitHub issue (tech-debt label) created as a follow-up run — the implementer did not create GH issues inline.

### Status

✅ v0.6.0 milestone **CLOSED (rc1 to cut next)**. 9 features shipped from this autonomous run plus earlier-v0.6.0 work (dj-mutation, dj-sticky-scroll, dj-track-static, runtime-layout-switching, WS compression). Remaining v0.6.0 wish-list items (ADR-006 AI-generated UIs, Streaming initial render, Time-travel debugging, Hot View Replacement) are substantial and become v0.6.x / v0.7.0 work. The milestone as shipped is defensible and complete.

---

## v0.5.7 — Deployment Ergonomics & Upload Feature Family (PRs #957–#959, 2026-04-23)

**Date**: 2026-04-23
**Scope**: Deployment-friction framework fixes (A010 proxy-trusted deployments, `get_state()` internal-attr filter) + the three upload-transport features that branched from PR #819's `UploadWriter` (pre-signed S3, GCS + Azure backends, resumable across WS disconnects).
**Tests at close**: 3445 Python + 1292 JS (baseline) → +110 regression cases across the 3 PRs (14 framework + 50 upload-writer + 46 resumable); security-tests coverage 64.72% → 89.55%.

### What We Learned

**1. v0.5.7 was a narrow-scope milestone and it worked — 5 issues, 3 PRs, clean first-push on 2 of 3.**
Only PR #959 (resumable uploads) failed first CI — and the failures were substantive (under-coverage on new security-relevant modules + 7 CodeQL alerts) not spurious. The tight scope (5 issues rather than a sprawling 30-issue batch) meant each PR was big enough to matter but small enough to review in one sitting. Compare with the v0.5.6 "Security & Code-Scanning Cleanup arc" — 16 PRs, hugely productive but hard to trace individual decisions through.

**Action taken**: Closed — observational only (narrow-scope milestone bundling pattern). No new tracker row.

**2. ADR-first development scaled the largest PR honestly.**
PR #959 (resumable uploads, ~3148 LOC) started with ADR-010 drafted on paper: wire protocol, state-store contract, failure modes, security considerations. Writing the ADR first forced clarity that showed up directly in the implementation — clean protocol messages (`upload_resume` / `upload_resumed`), narrow state-store interface (`get/set/update/delete`), explicit TTL semantics, deliberate choice of "409 on concurrent resume" over takeover. Estimated 500-700 LOC from the issue body; actual was 3148 LOC once ADR + JS client + IndexedDB persistence + 2 state backends + 55 tests are counted. **The ADR page count is a better LOC predictor than the issue body.**

**Action taken**: Closed — observational only (ADR page count predicts LOC). No new tracker row.

**3. Injection-seam testing without SDK installs is now the pattern.**
PR #958 added `client=` / `service_client=` kwargs on `GCSMultipartWriter`, `AzureBlockBlobWriter`, and `PresignedS3Upload`. Tests pass a mock client; the optional `djust[s3]` / `djust[gcs]` / `djust[azure]` extras don't need to be installed for CI to run. 50 tests, ~0.15s wall time. **This is the right pattern for any future cloud-SDK contrib modules.**

**Action taken**: Closed — observational only (injection-seam testing convention). No new tracker row.

**4. Shared error taxonomy upfront saved downstream churn.**
`UploadError` / `UploadNetworkError` / `UploadCredentialError` / `UploadQuotaError` in `djust.uploads.errors` + re-exported from `djust.uploads` means apps `except UploadError` without knowing which backend won. The 3 writers (S3, GCS, Azure) translate their SDK-specific exceptions into the shared taxonomy at raise-time. Stage 11 verified the translation table is consistent across all three.

**Action taken**: Closed — observational only (shared error taxonomy guidance). No new tracker row.

**5. CI coverage threshold caught a real under-coverage.**
PR #959's first push: 64.72% security-tests coverage, below the 75% gate. The gap was dominated by new PR files (`uploads/storage.py` 26%, `uploads/resumable.py` 17%, `uploads/views.py` 0%), NOT `error_handling.py` alone as the failure text suggested. The fix pass wrote 82 new tests reaching 89.55%. **The 75% coverage threshold is doing its job**, and the failure revealed that local testing before push missed the CI-mirror invocation — worth a `make ci-mirror` target.

**Action taken**: new Action Tracker row → `make ci-mirror` target to run the exact CI pytest invocation locally (Action #100).

**6. Retro-artifact gate (shipped in #950) worked zero dropout.**
All 3 v0.5.7 PRs had retros posted before `completed_at` was set. Pipeline-run skill's MANDATORY gate caught what the drain-phase subagents kept missing. The prior 3 dropouts (PRs #946, #955, #956) were the last occurrences.

**Action taken**: Closed — observational only (retro-artifact gate locked, no new work). No new tracker row.

### Insights

- **Narrow-scope milestones beat sprawling ones** for both review quality and retro coherence. v0.5.6 shipped 16 PRs in one arc — hard to retrospect. v0.5.7 shipped 3 PRs in one arc — every decision is traceable.
- **Upload-transport family as a v0.5.7 grouping was perfect.** #819 laid the foundation; v0.5.7 added 4 backends + 1 protocol. Same mental model per PR, same error taxonomy, consistent testing pattern. Future cloud-SDK additions (R2, B2, MinIO) are drop-in by following the established pattern.
- **Coverage failures are a gift, not a nuisance.** The 64.72% → 89.55% jump in PR #959 isn't cosmetic — the new tests covered real error paths (WatchError retry, TooLarge rejection, TTL expiration, full writer lifecycle). A future bug in any of those paths will be caught by tests that wouldn't have existed without the CI gate.
- **CodeQL's error-level alerts on new PRs catch what self-review misses.** PR #959's 7 alerts (log-injection, 4 ineffectual statements in Protocol bodies, 1 empty-except, 1 unvalidated dynamic method call) were all real findings — not noise. The unvalidated dynamic-call alert in particular was a structural JS issue in `handleUploadResumed` that could have been a security bug.
- **Breaking rename is still deferred.** #762 shipped as a non-breaking filter via `_FRAMEWORK_INTERNAL_ATTRS`. v0.7.0 can revisit the `_*` rename — carries the same "one deliberate breaking change per milestone" discipline that #927 (drop py3.9) used.

### Review Stats

| Metric | PR #957 | PR #958 | PR #959 | Total |
|--------|---------|---------|---------|-------|
| Tests added | 14 | 50 | 55 (46 Python + 9 JSDOM) | 119 |
| LOC | ~180 | +2486/-13 | +3148/-13 | +5814/-26 |
| 🔴 Findings (Stage 11) | 0 | 0 | 0 | 0 |
| 🟡 Findings (Stage 11) | 0 | 0 | 0 | 0 |
| 🟢 nits (Stage 11) | 0 | 3 | 0 | 3 |
| CI failures (first push) | 0 | 0 | 1 (coverage + 7 CodeQL) | 1 |
| Re-commit cycles | 1 | 1 | 2 | 4 |
| ADRs written | 0 | 0 | 1 (ADR-010) | 1 |

### Process Improvements Applied

**CLAUDE.md**: No additions needed — patterns from v0.5.6 arc (sanitize_for_log, url_has_allowed_host_and_scheme, set-membership allowlists) re-applied cleanly.

**Pipeline template**: No structural changes. The 14-stage pipeline held up across 3 PRs with CI re-run logic working as designed.

**Skills**: The `pipeline-run` retro-artifact gate (from #950) was put to the test and worked zero-dropout across 3 PRs. That's the first milestone with gate-verified retros.

**ADR series**: ADR-010 (resumable uploads) adds to the series after ADR-007 (package taxonomy), ADR-008 (HTTP API), ADR-009 (mixin side-effect replay). ADR-010's structure — wire protocol + state contract + failure modes + security considerations + trade-offs vs alternatives (tus.io) — is a template for future protocol-adding ADRs.

### Open Items

- [ ] Action Tracker #100 — `make ci-mirror` target to run the exact CI pytest invocation locally (prevents v0.5.7 #959's coverage-failure class)
- [ ] Action Tracker #101 — `fakeredis` dev dependency to replace hand-rolled Redis mocks in `test_security_upload_resumable.py`
- [ ] Action Tracker #102 — v0.6.0 or v0.7.0 decision point: breaking rename of framework-internal attrs to `_*` prefix (filter shipped non-breaking in v0.5.7; rename is still on the table)
- [ ] Action Tracker #103 — real-cloud CI matrix job (weekly cadence) for S3 / GCS / Azure upload writers — all v0.5.7 tests mock the SDKs
- [ ] Action Tracker #104 — document `key_template` convention (`uploads/{uuid}/{filename}`) more prominently so `s3_events.parse_s3_event`'s UUID-prefix extraction works as expected

---

## v0.5.2 / v0.5.5 RC churn — drain session second wave (PRs #868–#883, 2026-04-22)

> **RETROACTIVE BACKFILL — written 2026-04-25.** This entry was filled in
> during the v0.7.3 retro pass after auditing tag-vs-retro coverage. The
> work below shipped through `v0.5.2rc1` → `v0.5.5rc1` (all cut on
> 2026-04-22) but never got its own milestone entry — the
> "Tech-debt drain session" entry above covers PRs #859–#867; the
> Security arc covers #898+; this entry fills the #868–#883 gap. The
> "What We Learned" section is intentionally lean: synthesizing
> in-the-moment learning from `git log` ~3 days later produces
> historical-record value but not the action-tracker-feeding signal
> fresh retros capture.

**Date**: 2026-04-22 (single-day RC iteration)
**Scope**: 11 follow-up PRs continuing the tech-debt drain that started in #859–#867. Shipped through 4 RC cuts (v0.5.2rc1, v0.5.3rc1, v0.5.4rc1, v0.5.5rc1) within ~24 hours as additional fixes landed. Mix of tech-debt close-outs (#869, #870, #873, #875, #876), small-API additions (#871 render_slot via Rust engine, #874 `djust.db.untrack()`), v0.6.0 P1 foundations (#872 pre-minified client.js, #878 declarative UX attrs `dj-mutation` / `dj-sticky-scroll` / `dj-track-static`, #883 WebSocket per-message compression toggle), and one docs PR (#877 block-handler nesting + loader-access).
**Tests at close**: ~3,200 Python + ~1,200 JS at the time of v0.5.5rc1 (estimate from CHANGELOG; precise number not preserved).

### Issue → PR map (continuation of the drain)

| Cluster | PR | Issues closed / Feature shipped |
|---|---|---|
| dep-extractor harness | #869 | #786 (broaden partial-render correctness) |
| Rust assign-tag handler | #870 | #805 (warn on non-dict return) |
| render_slot Rust path | #871 | #861 (end-to-end via Rust engine) |
| **pre-minified client.js** | #872 | v0.6.0 P1 distribution — ~37 KB gzipped target |
| template dep-tracking | #873 | #787 (filter-arg deps), #806 (for-iterable getattr) |
| `djust.db.untrack()` | #874 | #809 (signal-receiver cleanup helper) |
| PostgresNotifyListener | #875 | #808 (cross-loop-use guard) |
| `assign_async` cancellation | #876 | #793 (concurrent same-name) |
| docs: block-handler nesting | #877 | #803, #804 (loader-access surface) |
| **declarative UX attrs** | #878 | v0.6.0 — `dj-mutation`, `dj-sticky-scroll`, `dj-track-static` |
| **WebSocket compression** | #883 | v0.6.0 — per-message-deflate toggle |

### What We Learned (retroactive)

**1. The original drain session's pattern continued landing follow-ups for ~24 hours.** PRs #859–#867 were the "first wave" (issue clusters with explicit tracker rows); #868–#878 were the "second wave" (smaller standalones + Rust-path fixes that needed the first wave landed first). #883 was a v0.6.0 feature that piggybacked on the same RC train. The v0.5.2 / v0.5.3 / v0.5.4 / v0.5.5 RC tags are essentially "mid-drain checkpoints" — useful for downstream pinning but not separate milestones.

**Action taken** (retroactive): None. Pattern observation only — drains naturally produce multiple RC cuts during the same active period. Future drain reporting should bundle them rather than treat each RC tag as a separate milestone-shaped event.

**2. v0.6.0 features shipped under v0.5.x RC tags.** PRs #872 (pre-minified client.js), #878 (declarative UX attrs), and #883 (WS compression) were all explicitly tagged "v0.6.0" in their commit subjects but rode the v0.5.x RC train because the larger v0.6.0 milestone wasn't yet cut. This is a normal pattern for "early v0.6.0 work landing on the v0.5.x branch" but worth noting: the v0.6.0 retro at PR #885+ correctly synthesizes those features as v0.6.0 work, so the RC tag they happened to ship under is essentially incidental.

**Action taken** (retroactive): None. The v0.6.0 retro's "PRs #885–#973" range is canonical for v0.6.0 features regardless of which RC tag they shipped under. This entry exists for tag-traceability; the feature-narrative belongs in the v0.6.0 retro.

### Insights

- **RC churn during a single drain is normal.** Five v0.5.x RC tags in 24 hours (v0.5.2rc1 → v0.5.6rc1) reflects the rapid iteration shape of the late-v0.5.x sprint, not five separate milestones.
- **Forward-shipping pattern**: features tagged "v0.6.0" landed in v0.5.x RCs because the milestone wasn't cut. Future practice should either (a) cut a tag at the v0.5.x boundary BEFORE landing v0.6.0 features, or (b) accept the forward-shipping and document it in the v0.6.0 retro (which is what happened — PRs #872, #878, #883 are correctly counted under v0.6.0).
- **Backfilled retros have lower signal.** This entry is intentionally lean. Compare to the fresh retros above (each with 3–6 "What We Learned" findings, action tracker rows, review stats) — the in-the-moment retro practice captures things that a `git log` mining session can't reproduce. Honest acknowledgment of the cost of deferring retros.

### Status

✅ Tag-coverage gap closed. v0.5.2rc1, v0.5.3rc1, v0.5.4rc1, v0.5.5rc1 RC tags now have a retro entry that explains what shipped under them. The v0.5.6rc1 → v0.5.7 / v0.6.0 work (PRs #885+) is covered in the existing v0.6.0 retro and Security & Code-Scanning Cleanup arc entry below.

---

## Security & Code-Scanning Cleanup arc (PRs #898–#931, 2026-04-22/23)

**Date**: 2026-04-22 / 2026-04-23
**Scope**: 16 PRs closing the Dependabot and CodeQL dashboards. Started the arc with ~1130 open CodeQL + 23 Dependabot alerts; ended with 0 Dependabot + ~37 note-level CodeQL (mostly cyclic-import notes pending final rescan). Arc also shipped 2 v0.6.0 animation features as bookends (#898 `dj-remove`, #904 `dj-transition-group`).
**Tests at close**: 3,428 Python + 1,279 JS (~75 regression cases added across the arc; base count unchanged by hygiene work)

### What We Learned

**1. Static analysis catches real bugs tests don't.** The arc surfaced ~12 pre-existing latent bugs during cleanup audits — none of which were caught by the existing test suite because they either lived on cold paths, in dead branches, or in behavior tests never exercised:
- `BuildTimeGenerator.generate_manifest` — bool attribute shadowed a method; first deployment call would `TypeError` (#923)
- `str.format()` with embedded CSS braces — `KeyError` on `{ font-family }` placeholder collision (#923)
- Markdown preview reflective XSS — raw `<script>` / `javascript:` URLs rendered unescaped (#925)
- `SignupView` / admin_ext open-redirect via unvalidated `next=` (#920)
- Storybook path-traversal via user-controlled filename (#920)
- Gallery render stack-trace leak — `f'{exc}'` into HttpResponseNotFound body (#918)
- Dead `if False` conditional referencing `InvalidTemplateLibrary` (#926)
- Duplicate `InteractionStyle` class definition shadowing the original (#928)
- Duplicate `INTERACT_MINIMAL` / `INTERACT_PLAYFUL` instance definitions with different field values (#928)
- `FormArrayNode` drops inner template content — block body parsed but never rendered (#929 → #930)
- `tag_input` widget missing `name=` attribute — form submissions drop the value (#929 → #932)
- `gallery/registry.py get_gallery_data` never consumes `discover_*` results (#929 → #933)

**Action taken**: Each fixed in the PR that surfaced it, where in scope; where scope-creep risks surfaced a latent bug in a dead branch, filed as a dedicated follow-up (#930, #932, #933).

**2. CodeQL's taint model doesn't recognize custom sanitizers.** Our `sanitize_for_log` helper, `url_has_allowed_host_and_scheme` used with explicit early returns, and `frozenset` membership allowlists all ARE correct — but CodeQL treats custom helpers as taint pass-throughs. ~33 alerts were dismissed across the arc with specific per-site justifications. Canonical CodeQL-recognizable patterns:
- Literal `s.replace('\n', '').replace('\r', '')` (log-injection)
- Django's `url_has_allowed_host_and_scheme` with `if not ...: return default_url` early return (url-redirection)
- `frozenset({...})` membership checks (path-injection)
- `if TYPE_CHECKING:` blocks for `__getattr__` lazy imports (undefined-export)

**Action taken**: Pattern documented in each retro. Filed #934 to add a CodeQL MaD model extension for `sanitize_for_log` — structural fix for the FP class rather than per-alert dismissal.

**3. Stage 11 grep-adjacent-files discipline prevents scope misses — 5+ consecutive confirmations.** Initial implementation fixes the flagged sites; Stage 11 greps the same file (or the codebase) for the SAME pattern and finds more. Examples across this arc:
- #898: IME composition regression outside the flagged `dj-remove` sites
- #918: `Http404(f"Unknown category: {category_slug}")` at line 690 not in the CodeQL report
- #920: `HttpResponseRedirect(hook_redirect)` in `mixins/request.py:75` and `auth/mixins.py:21`
- #923: `FormArrayNode` dead variable hinting at the #930 latent bug
- #929: `tag_input` and `gallery/registry` latent bugs surfaced by investigating "why is this variable dead?"

**Action taken**: Open — tracked in Action Tracker #138 (GitHub #1053).

**4. Breaking changes are justified when ecosystem has moved on.** PR #927 dropped Python 3.9 (EOL 2025-10-05, 6 months past). Four Dependabot alerts had been blocked by the py3.9 floor for months because orjson/pytest/python-dotenv/requests had all dropped 3.9 in CVE-fix releases. One principled breaking change closed the whole class. PR #909 took the softer hand: narrowed Django ceiling to `<6` in pyproject.toml without dropping 5.x from the lockfile — both patterns worked.

**5. The theming cyclic-import refactor (PR #928) had massive ROI.** Single PR, ~8 files edited, closed **872 `py/unsafe-cyclic-import` alerts** via one structural move: extract types to `_types.py`, extract shared instances to `_constants.py`, break the `_base.py` → `presets.py` → `themes.X` → `_base.py` cycle. Also surfaced 2 pre-existing latent bugs (duplicate `InteractionStyle`, duplicate `INTERACT_MINIMAL`/`PLAYFUL`). ~110 alerts closed per file touched — the highest-ROI PR of the arc by a wide margin. Confirmed next-day on rescan (2026-04-23): open alert count dropped from ~1130 to 37.

**6. Tests verify behavior; CodeQL's note-severity verifies hygiene.** Recurring question from the user: "is this not covered by our tests?" The answer: unused imports, unused vars, duplicate imports, overly-broad exception catches — ZERO runtime impact, so tests pass whether present or absent. This is the niche static analysis fills. PRs #929 and #931 delivered the mechanical cleanup pass (~90 note-level alerts across 49 files) without introducing any test failures.

### Insights

- **First-pass coverage + Stage 11 adjacent-grep = ~12 latent bugs caught** that tests missed. The two-stage review is load-bearing.
- **Dismissals are fine when the justification is SPECIFIC.** Generic "won't fix" ages badly. Per-site reasons ("set-membership allowlist at X:Y clears taint; CodeQL MaD model would recognize") survive review.
- **CodeQL error-severity rescan lag is ~24h**. PR #928's expected 873 closures confirmed on next-day rescan.
- **`--admin` merges with `REVIEW_REQUIRED` block** were used consistently when CI passed and self-review + Stage 11 completed. This session's pipeline policy; worth documenting as the default for hygiene PRs.
- **Pre-existing main test failures** (`test_api_response`, `test_observability_eval_handler`, `test_observability_reset_view`) surfaced ~3 times during this arc. Filed as #935 (not caused by this arc).
- **Test-count-drift across CHANGELOG/ROADMAP artifacts** — 3rd recurrence across this session + retro-885. Needs automation (tracker row #93).

### Review Stats (aggregated across the arc)

| Metric | Total |
|---|---|
| PRs shipped | 16 (2 v0.6.0 features + 14 security/quality) |
| Dependabot alerts closed | 27 (23 via #909 + 1 via #917 + 4 via #927) → **0 open** |
| CodeQL alerts fixed | ~980 (872 cyclic-import via #928 + ~110 across the other 15 PRs) |
| CodeQL alerts dismissed with justification | ~33 |
| Latent pre-existing bugs surfaced | 12 |
| Tests added | ~75 (regression cases across retros) |
| Pre-existing bugs fixed in-arc | 6 (surfaced via hygiene refactors) |
| Re-commit cycles per PR | ~1.3 average (most clean-landed) |

### Process Improvements Applied

**CLAUDE.md**: Pending — add security-pattern snippets:
- `sanitize_for_log` for user-controlled log args
- `url_has_allowed_host_and_scheme` with early returns for redirect targets
- `frozenset` set-membership allowlists for path inputs
- `if TYPE_CHECKING:` for `__getattr__` lazy imports
- "When removing a dev tool/dep, grep 5 surfaces: config, automation, source imports, user-facing docs, internal docs" (PR #917 lesson)
- Dependency-refresh playbook — check classifier compat, set ceilings, re-lock, verify (PR #909 lesson)

**Pipeline template**: No structural changes; the existing 14-stage template held up across 16 PRs. Stage 11's grep-adjacent-files discipline proved load-bearing again.

**Skills**: Pipeline-run evolved implicitly:
- `--admin` merge fallback for `REVIEW_REQUIRED` block on hygiene PRs
- `gh api --jq` pattern for bulk-dismissals (avoids Python JSON pipe stderr pollution — PR #926 lesson)
- `git stash && grep && git stash pop` pre-existing-failure verification pattern
- `gh api /code-scanning/alerts --paginate` for triage-table generation (PR #913 lesson) — consider scripting as `scripts/codeql-triage.sh`

**CodeQL config**: No changes to `.github/codeql/codeql-config.yml`. The 33 dismissals were per-alert rather than rule-wide. #934 filed to add a MaD model extension for `sanitize_for_log` to close the FP class structurally.

### Open Items (deferred to follow-up — see Action Tracker rows 80–93)

- [ ] #930 — FormArrayNode drops inner template content (filed, pending fix)
- [ ] #932 — `tag_input` missing `name=` attribute (filed)
- [ ] #933 — `gallery/registry` dead `discover_*` path (filed)
- [ ] #934 — CodeQL MaD model for `sanitize_for_log` (filed)
- [ ] #935 — 3 pre-existing main test failures (filed)
- [ ] `_registry.py` F401 alerts — explicit `# noqa` if rescan still flags (row #83)
- [ ] 3 `py/mixed-returns` — per-function judgment (noted in retro-931)
- [ ] 3 `js/unused-local-variable` from PR #925/#931 — scanner rescan pending
- [ ] `dispatch.py:295` vs `observability:399` message consistency (row #87)
- [ ] `inspect.getsource` test quality follow-up (row #88)
- [ ] `javascript:` scheme + HTTPS downgrade + null-byte storybook tests (row #89)
- [ ] Full audit of `HttpResponseRedirect`/`redirect()` call sites (row #90)
- [ ] Shared `conftest.py` staff-user fixture (row #91)
- [ ] `docs/internal/codeql-patterns.md` cheat sheet (row #92)
- [ ] Automate CHANGELOG test-count validation (row #93 — 3rd recurrence)

---

## Tech-debt drain session (PRs #859–#867, 2026-04-22)

**Date**: 2026-04-22
**Scope**: Autonomous drain of 35 open tech-debt issues accumulated from Stage 11 reviews across the v0.5.0 / v0.5.1 cycle. Grouped into thematic clusters; shipped 8 PRs closing 24 issues. The remaining 11 were deferred with explicit rationale (async-design / Rust-engine / multi-repo work).
**Tests at close**: Each cluster PR added regression coverage — typecheck 19→28→29 cases, middleware 9→13 cases, uploads 27→31, testing utilities 21→25, db-notify 39→43, plus JS test deltas in ignore_attrs.test.js, service_worker.test.js, virtual_list.test.js, and error_overlay.test.js.

### Issue → PR map

| Cluster | PR | Issues closed |
|---|---|---|
| typecheck follow-ups | #859 | #850, #851, #852 |
| service worker + middleware | #860 | #827, #828, #829, #830 |
| slot coverage | #862 | #789, #790 (+ filed new #861) |
| morph / dj-ignore-attrs | #863 | #815, #816, #817 |
| uploads | #864 | #823, #824, #825 |
| testing utilities | #865 | #843, #844 |
| streams / virtual | #866 | #798, #799, #801 |
| db_notify smalls | #867 | #810, #811, #812, #813 |

### What We Learned

**1. Stage 11 still catches real defects even on tech-debt PRs.** Every single cluster PR tonight had Stage 11 surface something non-trivial that Stage 7 (self-review) missed: `AnnAssign` dropped in the AST extractor (#859); `PermissionDenied` masked as 500 (prior PR #856); `_api_request` flag set after mount (prior PR #856); `dj-hook` not re-bound after the instant-shell swap (#860 — the doc I wrote claimed MutationObserver was the mechanism; it wasn't, and the test-client-to-main parity wasn't wired at all); `{% cycle ... as row_class %}` locals binding missing (#859); the `isIgnoredAttr` empty-token match edge case (#863). At 9 consecutive PRs, "Stage 11 is load-bearing" has proven itself beyond argument.

**Action taken**: Closed — already-locked rule (`feedback_pipeline_discipline.md` memory + RETRO.md historical record). No new tracker row.

**2. `Closes #X, #Y, #Z` in PR bodies only auto-closes the FIRST issue.** Several PRs merged with their second and third referenced issues still open. Fixed manually via 11 `gh issue close` calls; fixed the remaining PR bodies (#866, #867) to use `Closes #X` on separate lines — those auto-closed correctly on merge.

**Action taken**: Closed — already documented in `docs/PULL_REQUEST_CHECKLIST.md` Pre-Review Quick Checks. Operator-discipline note.

**3. Branch-per-cluster → every CHANGELOG touches the same Unreleased section → every merge has a conflict.** Eight PRs, eight CHANGELOG conflicts. Each conflict was trivial to resolve (both sides wanted to add a bullet) but it added ~3–5 min of ceremony to every merge. Total tax: probably 30 minutes of pure git-surgery across the session.

**Action taken**: Closed — observational only (consolidated CHANGELOG PR consideration). No new tracker row.

**4. The drain surfaced a real bug.** While writing end-to-end coverage for #790 (`{% render_slot slots.col.0 %}`), the test exposed that the Rust engine returns empty string for *any* `{% render_slot %}` invocation — not just the dotted-path case. Filed as #861. The handler's own Python logic works correctly in isolation. Users today have to extract slot content in Python (`assigns["slots"][name][0]["content"]`); the documented `{% render_slot %}` tag is silently broken via the Rust path. Net: #790 closed at the handler level, plus one new bug discovered that has real user impact.

**Action taken**: #861 on the backlog; worth prioritizing since it misrepresents shipped functionality in `docs/website/guides/components.md`.

**5. A coverage gate in CI surfaced when the grab-bag PR didn't exercise the same test files the gate measures.** The `security-tests` job covers `djust.security` + `djust.uploads` + `djust.validation` and runs `tests/unit/test_security_*.py`. My #864 added defensive code in `djust.uploads` covered by `tests/unit/test_upload_writer.py` — not in the security glob. Coverage dropped to 63%, fail-under=75% failed. Fix: added `test_upload_writer.py` to the security-tests glob (CI config change). The lesson: coverage gates can catch "I added defensive code without the test file being in the right suite" but the diagnosis takes a round trip.

**Action taken**: Added `test_upload_writer.py` to the security-tests CI run in PR #864. No rule change — but future PRs that add defensive code in `djust.uploads` should check the security-tests glob.

**6. Deferred items had clear rationale.** The 11 issues I didn't tackle fall into four buckets: (a) async/event-loop design (#793 #808 #809), (b) Rust template engine (#787 #803 #804 #805 #806), (c) multi-repo archival work (#778), (d) actual features disguised as tech-debt (#797 ResizeObserver). Documenting *why* each was deferred — rather than silently skipping — means "morning me" can pick up any of them with full context.

### Insights

- **Cluster size of 3–4 issues per PR was the sweet spot.** Smaller clusters felt ceremonial (each has its own Stage 11 agent, CI run, merge conflict). Larger clusters would have strained Stage 11's reviewing capacity.
- **Autonomous execution works when every stage is gated.** Eight PRs, eight Stage 11 reviews, eight CI cycles — no regressions on main. The rate-limiter was Stage 11's quality bar, not my output.
- **Deferred ≠ ignored.** Every deferred issue has a written reason in the `### Open Items` list, so the deferral survives context loss. This is the compounding value of writing things down.
- **The `render_slot` bug (#861) is the session's most valuable finding.** Not because it was fixed — it wasn't — but because without the drain, the test-coverage-gap reported in the review of #788 would have stayed silent. Coverage drove bug discovery.

### Open Items (carried forward)

- [ ] #861 — render_slot Rust integration returns empty (new bug; high user value)
- [ ] #786 — broaden dep-extractor correctness harness matrix (tests only, safe)
- [ ] #787 — extract filter-arg vars in `extract_from_variable` (Rust dep tracker)
- [ ] #793 — assign_async concurrent same-name cancellation semantics (async design)
- [ ] #797 — variable-height virtual list items via ResizeObserver (feature; ROADMAP-worthy)
- [ ] #803 — block-handler loader access (deferred from #802; Rust)
- [ ] #804 — parent-tag propagation for nested custom-tag handlers (Rust)
- [ ] #805 — warn when assign_tag_handler returns non-dict (~5 LOC Rust; pair with other Rust items)
- [ ] #806 — extend `Context::resolve` to for-iterables over Model instances (Rust + Django interop)
- [ ] #808 — PostgresNotifyListener event-loop binding across async_to_sync (real bug; async design)
- [ ] #809 — `untrack()` helper for `@notify_on_save` receiver cleanup (new public API)
- [x] #778 — ADR-007 package-shim sunset (multi-repo archival) — Shipped as v99.0.0 DeprecationWarning shims across djust-{auth,tenants,admin,theming,components}, 2026-04-22

---

## v0.5.1 — HTTP API Headline + Testing, Forms & Developer Experience (PRs #834–#849, #853)

**Date**: 2026-04-21
**Scope**: Auto-generated HTTP API (ADR-008 headline), testing utilities, dj-dialog, inputs_for formsets, dev error overlay, type-safe template validation, plus batch-landed state/computation primitives (`@computed` memoization, dirty tracking, `unique_id`, context provider/consumer) and form-polish (`dj-no-submit`, `dj-trigger-action`, scoped `dj-loading`). Closed the 82 pre-existing test failures that had blocked normal merges for the entire v0.5.1 cycle (PR #841).
**Tests at close**: 3,312 Python + 1,206 JS passing (1 flaky perf test that passes on re-run)

### What We Learned

**1. Stage 11 is load-bearing — 6 PRs in a row had Stage 7 pass + Stage 11 find real defects.**
PRs #814, #837, #840, #842, #846, #849 all passed Stage 7 (self-review) cleanly and then Stage 11 (subagent code review) found bugs that would have shipped. Examples: `AnnAssign` unhandled in the typecheck extractor (#849), `get_default_prefix()` always returning `"form"` regardless of custom prefix (#846), `absolute_max` miscap allowing `max_num=5` to grow to 1005 rows (#846), `follow_redirect` docstring promising a `RuntimeError` it never raised (#842). The pattern is strong enough to be mandatory — the pipeline's "never skip Stage 11" rule paid off every single time.

**Action taken**: Closed — already-locked rule (`feedback_pipeline_discipline.md` memory + RETRO.md historical record). No new tracker row.

**2. Directly-to-main pushes are a real risk — caught by permission denial.**
During the Stage 11 fix pass on PR #846, I pushed the fix commit (335cce26) to main instead of the feature branch because I never switched back after a sync. Recovery was awkward (close the stale PR as superseded, file docs-bookkeeping PR #847). Later, the same mistake was prevented by a permission rule the user had installed. The rule is cheap to add and expensive to work without.

**Action taken**: Closed — already mitigated by user-installed permission rule preventing direct main pushes.

**3. Pre-existing test failures block normal merge flow until fixed.**
The v0.5.1 cycle started with 82 failing tests. Every PR in the cycle before #841 was forced to use `--admin` bypass on merge. This eroded the signal from CI — a green CI run meant nothing because red CI also merged. PR #841 fixed all 82 in four clusters (missing test shims, stale `@layer` expectations, real code bugs like CSS prefix drift, and one wrong test assumption). After #841, every subsequent PR merged via the normal path.

**Action taken**: Open — tracked in Action Tracker #144 (GitHub #1059).

**4. Dogfooding before committing prevents shipping broken UX.**
The first `djust_typecheck` implementation correctly flagged everything the static analysis *could* resolve, but running it against the demo project surfaced 230+ lines of false positives because it only looked at `get_context_data` literals. The second pass added `self.foo = ...` AST extraction, reducing the noise to acceptable levels. Without the demo-project dry run, it would have shipped unusable.

**Action taken**: Open — tracked in Action Tracker #145 (GitHub #1060).

**5. Batching related small features into one PR is the right default.**
The state-primitives batch (#837: `@computed`, `is_dirty`, `unique_id`, context sharing) and form-polish batch (#840: `dj-no-submit`, `dj-trigger-action`, scoped `dj-loading`) were ~100-150 LOC each and shipped as single PRs. Four separate PRs would have produced 4x the review surface for low marginal signal. The `--group` flag in pipeline-run matched this correctly.

**Action taken**: Closed — observational only (batching small features 'keep grouping' guidance). No new tracker row.

**6. `window.DEBUG_MODE` gate pattern works for dev-only JS.**
The error overlay (#848) is gated on `window.DEBUG_MODE`, which the `djust_tags` template tag sets based on Django `DEBUG`. Production ships the code but it early-returns before rendering — and since Django strips the `traceback`/`debug_detail`/`hint` fields from the error frame in non-DEBUG, there's nothing to leak even if the guard were bypassed. Defense in depth without runtime cost.

**Action taken**: Closed — observational only (DEBUG_MODE gate continue-pattern guidance). No new tracker row.

### Insights

- **Five P2 items + the HTTP API headline + pre-existing-test-fix in one milestone** is feature-rich for a minor version. The HTTP API alone is a strategic inflection (unlocks mobile/S2S/CLI/AI-agent consumers via OpenAPI 3.1 schema); batching it with DX improvements produces a milestone that's both strategically meaningful and developer-facing.
- **Tech-debt issues should be filed *during* Stage 11, not after.** Stage 11 on #849 identified 3 follow-ups; filing #850/#851/#852 immediately from the review findings kept them visible. The alternative (mentioning in retro, hoping to remember) leaks.
- **Autonomous overnight execution (`--all --group`) works when CI is green and Stage 11 is mandatory.** 5 feature PRs + 1 docs PR + a release-bump PR in one session, zero regressions, each PR independently reviewed and merged.
- **"The typecheck command surfaces false positives" is the feature, not the bug.** Three silencing tiers (template pragma, per-view `strict_context`, project-wide `DJUST_TEMPLATE_GLOBALS`) mean developers triage on first run and then accumulate trust. This matches how linters ship.

### Review Stats

| Metric | #835 (HTTP API) | #837 | #840 | #841 | #842 | #845 | #847 | #848 | #849 | Total |
|--------|-----------------|------|------|------|------|------|------|------|------|-------|
| Tests added | ~40 | ~20 | 11+4 | 0 (fixed 82) | 21 | 8 | 0 | 10 | 19 | ~133 |
| 🔴 Findings | tbd | 1 | 1 | — | 4 | — | — | — | 1 | 7 |
| 🟡 Findings | tbd | ~3 | 2 | — | 3 | 1 | — | 4 | 3 | ~16 |
| Findings fixed pre-merge | all | all | all | — | all | 1 | — | 0 (minor) | C1 | all Cs |
| Stage 7 → Stage 11 delta | — | 3 | 1 | — | 4 | 0 | — | 0 | 1 | 9 |

### Process Improvements Applied

**CLAUDE.md**: No structural changes. Memory entries added for "never skip Stage 11" load-bearing rule and "pipeline autonomous — don't stop to ask next-task scope" behavioral adjustment.
**Pipeline template**: Continues to use the djust-local `.pipeline-templates/` with mandatory Stage 11 + 13 + 15. WIRING_CHECK + downstream-app name-leak scan applied to every PR.
**Skills**: No new skills. `/pipeline-run --all --group` validated across 5 feature PRs.

### Open Items

- [ ] `djust_typecheck` template-tag blind spots — Action Tracker #55 (#850)
- [ ] `djust_typecheck` MRO walk for self-assigns — Action Tracker #56 (#851)
- [ ] Extract `_walk_subclasses` / `_is_user_class` (3x duplication) — Action Tracker #57 (#852)
- [ ] `follow_redirect` multiple-redirect semantics — Action Tracker #58 (#843)
- [ ] `handle_async_result` callback not invoked in `render_async` — Action Tracker #59 (#844)
- [ ] v0.5.1rc3 → v0.5.1 stable release — PR #853 awaiting merge authorization

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

**Action taken**: Closed — Fixed in PR #770. CSP injection test added.

**5. Pre-existing lint in upstream packages requires per-file-ignores, not fixes.**
The theming and components packages had dozens of pre-existing ruff violations (E741 for `l` in HSL code, F841 unused variables, E402 conditional imports, F524 CSS-in-format-strings). Fixing them would have created divergence from upstream and risked behavioral changes. The right approach: `[tool.ruff.lint.per-file-ignores]` in pyproject.toml with comments explaining why.

**Action taken**: Added per-file-ignores for theming and components. Fixed only the one actual syntax error (Python 3.9 f-string in `kbd.py`).

**6. `logout_view` accepting GET was a CSRF vulnerability.**
The djust-auth package's `logout_view` was a plain function that called `logout(request)` on any HTTP method. An attacker could log users out via `<img src="/accounts/logout/">`. Changed to POST-only with `HttpResponseNotAllowed(["POST"])`.

**Action taken**: Closed — Fixed in PR #770. Test added for GET → 405.

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
- [x] Ship final standalone package versions as deprecation shims — Action Tracker #17 (#778) — Done 2026-04-22 (5 sibling repos tagged v99.0.0)

---

## v0.5.0 — Feature Rollout (PRs #784–#826)

**Date**: 2026-04-21
**Scope**: Ten v0.5.0 roadmap items shipped in a single pipeline-run session spanning ~26 hours: the "true #783 fix" (#784), P0 dep-extractor hardening (#785), Component System (#788), async rendering (#792), large-list DOM perf (#796), Rust template parity (#802), PostgreSQL LISTEN/NOTIFY → push (#807), hook polish (#814), UploadWriter (#819), and Service Worker core (#826). Closes the v0.5.0 milestone.
**Tests at close**: ~270 new tests (Python 1264 passing / JS 1174 passing / Rust 620 passing, 0 regressions)

### What We Learned

**1. The strategic-enabler pattern compounds — ship the primitive, reuse it.**
PR #788's `register_block_tag_handler` was the primitive that carried seven subsequent PRs: #792 (`{% dj_suspense %}`), #796 (`stream_prune` op), #802 (`register_assign_tag_handler` sibling), #807 (consumer group send), #814 (`{% colocated_hook %}`). Each subsequent PR got smaller because the prior PR laid a reusable primitive. IntersectionObserver in #796 played the same role across virtual lists and viewport sentinels. The `Arc<HashMap<String, PyObject>>` sidecar in #802 spared a `Value: Serialize` refactor.

**Action taken**: Closed — observational only (primitive-first design locked as default for v0.6+). No new tracker row.

**2. Stage 11 (independent code review) kept catching real defects Stage 7 (self-review) missed — three times.**
- **#796**: `window.djust.pushEvent` didn't exist — the viewport-event feature was broken end-to-end. Unit tests asserted on the dispatched CustomEvent, never on the server round-trip. Fixed via `window.djust.handleEvent` + regression test.
- **#814**: `</Script>` mixed-case escape gap — tag-breakout risk in the colocated-hook script envelope. Fixed with case-insensitive regex + all-casing regression test.
- **#819**: Raw boto3 exception strings leaked IAM ARNs / bucket names / endpoints to the browser via `entry._error`. Also a docstring example that contradicted the security callout. All three (and a second leak in the S3 doc example) fixed in one follow-up commit with a pinning regression test (`test_error_messages_do_not_leak_raw_exception_text`).

Pattern worth pinning: Stage 7 tends to miss security/correctness issues in **doc examples** and at **contract boundaries** (what reaches the client). **Action taken**: Session-wide "fix ALL findings in one push" discipline held — each was resolved in a single follow-up commit before merge, not deferred.

**3. "Closed without code" claims must be verified against the reporting downstream test.**
PR #779 was originally credited with closing #783 in both the ROADMAP and the issue tracker. A downstream consumer downstream test (`test_autofill_then_next_step_works`) stayed red. Re-opening exposed that #779 only fixed the Python-side `id()` comparison; the real bug was in `extract_from_nodes` silently dropping deps from nested `Include`/`InlineIf`. PR #784 fixed the actual root cause; PR #785 added the P0 correctness harness (compile-time `Node` variant exhaustiveness + Rust unit tests on `extract_per_node_deps` + Python byte-identical partial-vs-full oracle) so a third instance of this bug class cannot ship silently.

**Action taken**: ROADMAP attribution corrected in PR #784. Dep-extractor now has three complementary guards.

**4. ROADMAP drift — grep before implementing.**
PR #792 found that `temporary_assigns` was already implemented — the ROADMAP claimed "completely absent from djust today" when in fact the machinery was wired through multiple modules. Caught during the Stage 6 codebase survey. Pivoted to regression-test-only + ROADMAP correction. Second time this session — PR #784 also found #783 attribution stale.

**Action taken**: Open — tracked in Action Tracker #142 (GitHub #1057).

**5. Pre-commit commit-loop friction amplifies each fix attempt.**
PR #814 took six commit attempts to land. Root causes compounded: eslint `no-new-func` at the `new Function` call site, ruff E402 import-order in tests, AND the pre-commit hook's stash-restore cycle itself re-triggering each fix. Running formatters+linters against the *exact staged files* BEFORE `git commit` — not reacting after the hook fails — would have cut this to 1 attempt.

**Action taken**: Closed — Action #122 closed at skill level 2026-04-25 (~/.claude/skills/pipeline-run/SKILL.md MANDATORY Post-Commit Verification section).

**6. Client.js weight drifted past manifesto budget.**
CLAUDE.md claims ~5 KB client JS; the session added roughly +25 KB raw (355 KB → 380 KB): +15.7 KB in #796 (virtual lists + infinite scroll), +6.4 KB in #814 (colocated hooks), ~0 in #826 (service worker is a separate file). The 5 KB number is aspirational/gzipped; needs an explicit clarification in the manifesto or a bundle-split plan before v0.6.

**Action taken**: Filed as follow-up #800 (tracker row #29). Does not block v0.5.0 release.

**7. Ghost-branch drift in subagent workflows.**
PRs #788, #814, #819, and #826 each saw at least one commit land on a phantom `pr-NNN` branch instead of the feature branch, requiring cherry-pick recovery. PR #826 hit it twice. Appears to happen when subagents operate in fresh git contexts and something (likely `gh pr checkout` state) sets up a tracking branch silently. Has not lost work yet, but adds recovery overhead.

**Action taken**: Open — tracked in Action Tracker #143 (GitHub #1058).

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

**Action taken**: Closed — superseded by Action #122 closure (predates the post-commit verification skill update).

**4. Pipeline discipline matters — skipping review stages has consequences.**
The first two PRs (#708, #710) were merged without running the Code Review and Retrospective stages. The post-merge review of #710 found a real issue (missing try/finally) that should have been caught pre-merge. PR #714 followed the pipeline correctly and the review found no issues.

**Action taken**: Closed — already-locked rule (`feedback_pipeline_discipline.md` memory + RETRO.md historical record). No new tracker row.

**5. The Rust template engine's Django compatibility is a long tail.**
PR #720 fixed `|date` filter on bare `DateField` values ("2026-03-15") — it only supported RFC 3339 datetimes. The fix is 6 lines (NaiveDate fallback to midnight UTC), but the pattern is clear: every new production use case surfaces another Django filter edge case. The Rust engine now handles 2 of Django's ~5 date input types. The midnight-UTC assumption is correct but means `{{ date_field|date:"H:i" }}` renders "00:00", which could surprise developers.

**Action taken**: Fixed in PR #720. Tracking remaining compatibility gaps as Action #13.

**6. Review-to-action-item pipeline completed a full cycle.**
All 4 items in PR #721 originated as findings from PRs #708 and #710 reviews. They were tracked in the Action Tracker, filed as GitHub issues (#715-#718), and resolved in a batch PR. This is the pipeline working as designed. However, the #721 review found that the CSRF HTML-escape fix used a manual `.replace()` chain instead of the existing `filters::html_escape()` utility — an implementation gap where the developer didn't search for existing utilities before writing new code.

**Action taken**: Closed — Fixed in PR #721. Action #9 filed for the utility duplication.

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
