# CLAUDE.md

This file provides guidance to Claude Code when working with the djust framework.

## Project Overview

djust is a hybrid Python/Rust framework bringing Phoenix LiveView-style reactive server-side rendering to Django. Rust handles performance-critical operations (template rendering, VDOM diffing, HTML parsing) via PyO3; Python provides the developer-facing API.

## Build & Test Commands

```bash
make install          # Full install (Python + Rust build)
make install-quick    # Python-only install (skip Rust rebuild)
make build            # Build Rust extensions (release)
make dev-build        # Build Rust extensions (dev, faster)

make test             # Run all tests (Python + Rust)
make test-python      # Python tests only
make test-rust        # Rust tests only

make lint             # Run linters (ruff, clippy)
make format           # Format all code (ruff format, cargo fmt)
make check            # Linters + tests

make start            # Dev server on :8002 (uvicorn, auto-reload)
make start-bg         # Dev server in background
make stop             # Stop background server
```

### Running specific tests

```bash
pytest python/                        # All Python tests
pytest python/djust/tests/test_foo.py # Single file
pytest -k "test_name"                 # By name pattern
cargo test                            # All Rust tests
cargo test -p djust_vdom              # Single crate
```

## Project Structure

```
djust/
├── python/djust/           # Python package
│   ├── live_view.py        # LiveView base class
│   ├── component.py        # LiveComponent base
│   ├── forms.py            # FormMixin (real-time validation)
│   ├── websocket.py        # LiveViewConsumer (Channels)
│   ├── auth.py             # Authentication & authorization (check_view_auth, mixins)
│   ├── decorators.py       # @event_handler, @cache, @debounce, @permission_required, etc.
│   ├── config.py           # Configuration system
│   ├── presence.py         # Presence tracking (PresenceMixin, CursorTracker)
│   ├── streaming.py        # StreamingMixin (real-time partial DOM updates)
│   ├── uploads.py          # File uploads (binary WebSocket frames)
│   ├── routing.py          # live_session() URL routing helper
│   ├── testing.py          # LiveViewTestClient, SnapshotTestMixin, LiveViewSmokeTest
│   ├── checks.py           # Django system checks (C/V/S/T/Q categories)
│   ├── management/commands/ # djust_audit (security audit), djust_check (system checks)
│   ├── mixins/             # LiveView mixins (navigation, model binding, etc.)
│   ├── templatetags/       # Django template tags
│   ├── tenants/            # Multi-tenant support
│   ├── backends/           # Presence backends (memory, redis)
│   └── static/djust/       # Client JS (~87 KB gzipped, 388 KB raw, 35 source modules)
├── crates/
│   ├── djust/              # PyO3 bindings (entry point for Python)
│   ├── djust_core/         # Core types, serialization, context
│   ├── djust_templates/    # Rust template engine
│   └── djust_vdom/         # Virtual DOM + diffing
├── examples/demo_project/  # Demo app (counter, forms, etc.)
├── tests/                  # Integration tests
├── docs/                   # Documentation
│   └── PULL_REQUEST_CHECKLIST.md
├── Makefile
├── pyproject.toml
└── Cargo.toml              # Workspace root
```

## Code Style

### Python
- **Formatter/linter**: Ruff (runs automatically via pre-commit hooks)
- **Logging**: Use `%s`-style formatting, never f-strings: `logger.error("Failed for %s", key)`
- **Type hints**: Required for all public APIs
- **Docstrings**: Django/Google style for public methods/classes

### Rust
- **Format**: `cargo fmt` (enforced)
- **Lint**: `cargo clippy` — address all warnings
- **Error handling**: Use `Result` types; no `unwrap()` in library code

### JavaScript
- Client JS size budget: current ~87 KB gzipped (388 KB raw across 35 source modules in `static/djust/src/`); pre-minified distribution target for v0.6.0 is ~37 KB gzipped / ~30 KB brotli. When adding a feature, measure its gzipped delta — aim under 2 KB gzipped per new module. Top 3 modules (`12-vdom-patch.js`, `09-event-binding.js`, `03-websocket.js`) are 42% of the budget; reducing them requires structural care. No new dependencies without discussion.
- **No `console.log`** without `if (globalThis.djustDebug)` guard — unguarded logging is auto-rejected
- New JS feature files in `static/djust/src/` must have corresponding test files in `tests/js/`

## Security Rules

These are **hard requirements** — violations are auto-rejected in PR review:

1. **Never** `mark_safe(f'...')` with interpolated values — use `format_html()` or `escape()`
2. **JS string contexts** use `json.dumps()` for escaping (not `escape()`)
3. **No `@csrf_exempt`** without documented justification
4. **Logging**: `%s`-style formatting only — never `logger.error(f"...")`
5. **No bare `except: pass`** — always log or re-raise
6. **No `print()` in production code** — use the logging module
7. **No `console.log`** in JS without `if (globalThis.djustDebug)` guard

## Workflow Expectations

- **Conventional commits**: `fix:`, `feat:`, `docs:`, `refactor:`, `security:`, `test:`, `chore:`
- **Always run tests** before pushing (`make test`)
- **Pre-commit hooks** run automatically: ruff, ruff-format, bandit, detect-secrets
- **Pre-push hooks** run the full test suite (~900 tests, ~40s)
- **Review against** `docs/PULL_REQUEST_CHECKLIST.md` before marking PRs ready
- After completing a set of related changes, commit with a descriptive conventional commit message

## Testing Expectations

- All new code needs tests (unit and/or integration)
- New JS feature files in `static/djust/src/` need corresponding tests in `tests/js/`
- Bug fixes require regression tests
- Run the full suite before push; let pre-push hooks run
- Tests must be deterministic — no flaky tests
- Test imports must match actual module paths (a common rejection reason)
- `feat:` and `fix:` PRs must update CHANGELOG.md

## Key Patterns

### LiveView
```python
from djust import LiveView

class MyView(LiveView):
    template_name = 'my_template.html'

    def mount(self, request, **kwargs):
        self.count = 0

    def increment(self):
        self.count += 1

    def get_context_data(self, **kwargs):
        return {'count': self.count}
```

### Event Handlers — always use `@event_handler`
```python
from djust.decorators import event_handler

@event_handler()
def search(self, value: str = "", **kwargs):
    """Use 'value' param for @input/@change events"""
    self.query = value
```

### Public/Private variable convention
- `_private` — internal state, not exposed to templates
- `public` — auto-exposed to template context and JIT serialization

### Background Work — `start_async()` and `@background`
For long-running operations (API calls, AI generation, file processing), use `AsyncWorkMixin` (included in `LiveView` base) to flush loading state immediately and run work in background:

```python
from djust import LiveView
from djust.decorators import event_handler, background

class ReportView(LiveView):
    @event_handler
    def generate_report(self, **kwargs):
        self.generating = True  # Sent to client immediately
        self.start_async(self._do_generate)  # Runs after response sent

    def _do_generate(self):
        self.report = call_slow_api()  # Background thread
        self.generating = False  # View re-renders when done

# Or use @background decorator for automatic start_async wrapping:
class ContentView(LiveView):
    @event_handler
    @background
    def generate_content(self, prompt: str = "", **kwargs):
        self.generating = True
        self.content = call_llm(prompt)  # Entire handler runs in background
        self.generating = False
```

Key features:
- `start_async(callback, *args, **kwargs)` schedules background work with optional named tasks
- `cancel_async(name)` cancels scheduled or running tasks
- `handle_async_result(name, result=None, error=None)` optional callback for completion/errors
- `@background` decorator wraps entire handler to run via `start_async()`
- Loading states persist through background work via `async_pending` flag
- Always catch exceptions in callbacks to prevent client stuck in loading state

## Template Filters

The Rust template engine supports **all 57 Django built-in filters** in `crates/djust_templates/src/filters.rs`. HTML-producing filters (`urlize`, `urlizetrunc`, `unordered_list`) handle their own escaping internally and are listed in `safe_output_filters` in `renderer.rs` to prevent double-escaping.

## Bug-report triage

When investigating an issue with a code-location citation:

1. **Trust the symptom, not the cited path.** The reporter's diagnostic data
   (error messages, patch counts, observable behavior) is the load-bearing
   evidence. The code location they cite is their hypothesis, which may be
   wrong — even when the cited code looks like a perfect match for the
   symptom (e.g., a dead-code fallback that produces the exact bytes the
   reporter saw).

2. **Trace from observable symptom to actual code path.** Write a reproducer
   test FIRST (Stage 4 of the bugfix pipeline already requires this).
   Confirm the reproducer fails. Then trace the data flow from where the
   symptom appears (output, error, missing patch) BACKWARDS through the
   framework until you find the offending code. This is symptom-up.

3. **Don't trust path-down hypotheses.** If you start at the reporter-cited
   location and try to verify the bug from there, you'll burn time when
   the location is wrong.

4. **Canonical case study**: PR #1206 (#1205 list[Model] VDOM fix). Reporter
   cited `python/djust/mixins/jit.py:_lazy_serialize_context` — a method
   with a `str(model)` fallback that exactly matched the reported symptom
   (`__str__` strings in serialized context). The method had **zero call
   sites** — dead code. The actual bug was upstream in
   `python/djust/mixins/rust_bridge.py:_sync_state_to_rust` change-detection
   comparing `list[Model]` via `Model.__eq__` (pk-only). Reproducer-first
   TDD surfaced the real path; trying to fix the reporter-cited code would
   have been a no-op.

5. **`_framework_attrs` snapshot-order invariant (#1393).** Any new attr
   assigned in `LiveView.__init__` must be placed BEFORE or AFTER the
   `self._framework_attrs = frozenset(self.__dict__.keys())` line based on
   whether it is framework state (reset on reconnect) or user state
   (persisted, change-tracked). See the comment block at
   `python/djust/live_view.py:518` for the rule + examples.

6. **Multi-reopen issues require bit-exact runnable repro before "root
   cause confirmed" (#1389, PR #1086).** Theory-testing against synthetic
   test cases is INSUFFICIENT — it confirms only that THE FRAMEWORK
   behaves a certain way, not that THIS USER'S BUG matches the theory.
   PR #1086 had 3 "root cause" comments based on framework-side theory
   testing; all three were wrong. The actual fix landed only after
   gaining direct project access to reproduce against the user's exact
   environment.

## Common Pitfalls

- **Ruff F509**: `%`-format strings containing CSS semicolons trigger false positives. Separate HTML (`%s` substitution) from CSS (static string) and concatenate.
- **VDOM form values**: Ensure form field values are preserved during updates. See `VDOM_PATCHING_ISSUE.md`.
- **Pre-commit reformatting**: If commit fails due to ruff auto-format, re-stage and commit again.
- **Hot reload integration (v0.9.0+)**: djust auto-enables HVR from its
  own `DjustConfig.ready()` whenever `DEBUG=True` and `watchdog` is
  installed. Downstream consumers do NOT need to add
  `enable_hot_reload()` to their own `AppConfig.ready()`. Existing
  explicit calls keep working (idempotent). Opt out via
  `LIVEVIEW_CONFIG['hot_reload_auto_enable']: False`. Tests skip the
  auto-enable via `PYTEST_CURRENT_TEST` so pytest sessions don't spawn
  a watchdog thread per test. Don't wrap `uvicorn` in
  `watchfiles` / `--reload` for djust dev servers — that's process
  restart and drops view state; djust's HVR is strictly better
  (preserves form input, scroll position, counters).

## Process canonicalizations from v0.6.x–v0.8.x retro arcs (backfill)

Fourteen rules distilled from Action Tracker rows accumulated across the v0.6.1, v0.7.0, v0.7.1, v0.7.2, v0.8.0, v0.8.1, and v0.8.2 retro arcs. Filed as GitHub issues by `/pipeline-retro --reconcile` (2026-04-25 sweep) and v0.8.x retro Stage 4 filings; canonicalized here in PR #TBD as the v0.9.1-7 cleanup batch before cutting release v0.9.1.

### Stage 4 (Planning) additions

- **External-crate doc.rs read for security-surface dependencies (#1050).**
  Any external crate (Rust or Python) whose API forms part of a security
  boundary must have its doc.rs / official-docs entry read at Stage 4/5
  for the specific API surface used. PR #990 surfaced two
  `pulldown-cmark 0.12` API corrections only because RED tests failed:
  `Options::ENABLE_HTML` omission does NOT suppress `Event::Html`, and
  `Options::ENABLE_GFM_AUTOLINK` doesn't exist in 0.12. Luck saved the
  XSS surface that time. Stage 4 plan template should grow a
  "linked doc.rs section for each external security-boundary API" row.

- **Engine-path declaration generalized (#1051).** Any feature that
  touches the template rendering pipeline — filters, tags, context
  processors, custom blocks, post-processing hooks, registry-style APIs —
  must declare which engine(s) (Python / Rust) the user templates run
  through. PR #993 caught a dual-engine bug ONLY because the pre-push
  full-demo suite ran; targeted Stage 6 subsets miss it. Class of bug:
  any code path participating in user template rendering can silently
  work in one engine and 500 in the other. Generalizes Action #129.

- **Multi-PR milestone iter sequencing (#1055).** When bundling a
  multi-PR milestone, sequence the smallest design-novel iter first.
  Smaller iters lock in design contracts that later iters can verify
  against. Generalized from v0.8.0's iter 1 (`dj-form-pending`) → iter 2
  (`@action`) sequencing.

- **API shape options considered (#1056).** Stage 4 plan template
  should grow an "API shape options considered" row for greenfield UX/API
  features (where the API shape isn't dictated by an existing design).
  Surface 2-3 options with explicit pros/cons before implementation.
  Pattern proven on PR #1007 (3-option radio API, picked B, 12/12 tests
  green first authoring pass).

- **Lift-from-downstream FIRST (#1077).** When an issue cites a
  downstream consumer's working solution (e.g., "docs.djust.org wrote
  the bridge in its own input.css"), lift the reference impl verbatim
  FIRST, generalize SECOND. Skips a design-from-scratch phase that risks
  producing an incompatible variant. Empirical: PR #1074's `prose.css`
  was lifted ~91 lines verbatim from docs.djust.org's `input.css`,
  fraction of clean-room time; reference impl was already battle-tested
  against three theme packs in production.

- **Broader-sweep → follow-up issue scope-discipline (#1079).** When
  Stage 4 investigation reveals a broader systemic issue beyond what
  the cited issue asks for, fix EXACTLY what the issue cites and file
  a follow-up issue for the systemic remainder. Resists scope creep
  while preserving the systemic finding. Validated 2× in v0.8.x:
  v0.8.1 PR #1067 (security-leak found during style-only fix; stayed
  scoped) and v0.8.2 PR #1076 (4 cited stale .md refs, found ~50 more
  across 17 files; stayed scoped to the 4 cited, filed #1075 for the
  rest).

### Stage 7 (Self-Review) additions

- **Greenwashing-catcher: grep for stubbed JSDOM API shapes (#1037).**
  Pre-commit Self-Review should grep for JSDOM API stubs that no
  source ever assigns. Failure mode: tests stub `globalThis.djust.foo`,
  no production code populates it, real path is something else
  (e.g., `window.djust.liveViewInstance.sendMessage`). Tests pass; real
  surface is broken. Add Stage 7 grep: if a JSDOM test stubs `djust.X`
  and nothing in source assigns it, flag.

- **Doc-claim-verbatim TDD before implementation (#1046, supersedes
  #1040).** For every feature with non-trivial semantics (gate rules,
  error envelopes, state contracts), write doc-claim-verbatim tests
  BEFORE writing implementation. The test cases ARE the doc claims.
  Stage 7 checklist should grow a "for each documented rule, point to
  the asserting test" row. Empirical pattern: 4 consecutive milestones
  (v0.6.0, v0.6.1, v0.7.0 PRs #986/#988/#989) hit doc-vs-code drift as
  Stage 11 🔴/🟡 findings before this rule was canon. Subsumes the
  earlier "trace data-flow before writing docs" rule (#1040), which
  was aspirational rather than executable.

- **Stage 7 user-flow trace for user-visible features (#1047).** For
  every user-visible feature, trace the happy-path user story end-to-end:
  HTTP request → server dispatch → response envelope → browser render /
  navigation. 3 consecutive pipelines (PRs #986/#988/#989) had Stage 7
  rubber-stamp diffs that Stage 11 proved were broken end-to-end —
  same shape (code does a thing; thing doesn't reach the user).
  Validated across PRs #990, #993, #995, #996, #997 (all 0 🔴 at
  Stage 11 after this rule was filed informally).

### Stage 9 (Documentation) addition

- **Test-count recount after fix-pass deltas (#1049).** Stage 9 must
  re-count tests AFTER Stage 7/12 fix-pass deltas and update the
  CHANGELOG test-count line before the final docs pass. PR #990
  CHANGELOG claimed "38 total" but actual was 41 (docs author cited
  Stage 5 count, not post-fix-pass). Stage 11 caught it. Two milestones
  with small CHANGELOG test-count drift before this rule was canon.

### Stage 11 (Code Review) addition

- **`mark_safe` XSS-trace audit (#1078).** For every new `mark_safe`
  call, trace inputs to a server-validated source. Reviewer-discretionary
  practice has worked (PR #1074's reviewer subagent traced cookie
  inputs through `registry.has_theme/has_preset` validation in
  `get_state()` and confirmed no XSS surface), but making it a Stage 11
  checklist item locks the discipline. Bullet also added to
  `docs/PULL_REQUEST_CHECKLIST.md` Security Review section.

### Cross-stage rules

- **Mutation-after-capture test discipline (#1039).** Every snapshot /
  capture function needs a test that exercises mutation AFTER the
  capture call, asserting the captured state is unchanged. The
  `_capture_snapshot_state` reference-aliasing bug existed unnoticed
  for two milestones (v0.6.0 `enable_state_snapshot` + v0.6.1
  time-travel). Generalize: capture shouldn't share refs with the
  source; test by mutating source post-capture and checking the
  capture's value.

- **Dogfood pass for new CLI tools (#1060).** Any CLI tool that reports
  on project state gets a dogfood pass against the demo project before
  commit. v0.5.1 `djust_typecheck` originally produced 230+ lines of
  false positives; a dogfood pass against the demo caught it pre-commit.
  Bullet also added to `docs/PULL_REQUEST_CHECKLIST.md` Code Quality
  section.

- **Doc-claim TDD extends to prose docs with external citations
  (#1071).** Action #124 (doc-claim-verbatim TDD) was filed for code
  claims; PR #1064 surfaced the same failure mode in PROSE docs: the
  new `docs/internal/codeql-patterns.md` cheat sheet cited 10 PR
  numbers, of which 7 were plausible-sounding hallucinations. Stage 11
  reviewer caught all 10. Generalize: any prose doc that names external
  artifacts (PR numbers, issue numbers, commit hashes, file:line refs)
  must cross-check each citation at write time, not after. Use
  `gh pr view <N>` / `gh issue view <N>` / `git log` per citation
  before commit.

## Process canonicalizations from PR retros (2026-04-26 View Transitions arc)

Each rule below was a Stage 11 finding or retro-tracker item from the View
Transitions PR-A → PR-B arc and the downstream-consumer gap-fix arc. Canonicalized
here so the next migration / mechanical-replacement / mixin-forwarding /
filter-shape PR doesn't repeat the failure mode.

- **Async-migration regex pass: ALWAYS run a completeness-grep after** (#1100).
  After `sed`-style adding `await` to every `funcName(...)` callsite, run
  `grep -nE '(^|[^t])(funcName|otherFn)\(' tests/ src/ | …` and visually
  scan for hits inside `async` bodies that lack `await`. The regex misses
  method invocations like `obj.handleMessage(...)` when keyed on top-level
  identifiers. Caught 4 test files in PR #1112; canonicalized after the
  same gap surfaced in PR #1099.

- **ADR scope-estimation: count test-file callers, not just src callers** (#1101).
  For any function whose signature changes (sync→async, single→variadic,
  return-type widening), test-file scope is typically 2-3× production
  scope. Run `grep -lr <symbol> tests/` upfront and put the count in the
  ADR. ADR-013 said "~5 caller sites"; actual was 13.

- **Forward kwargs in mixins: `is None` coalesce, NOT `setdefault`** (#1103).
  `kwargs.setdefault('x', self.default_x)` does NOT overwrite a
  caller-passed `None` — the key already exists. When the value flows
  through to a dict-key write (e.g. `attrs[kwargs['x']] = ...`), `None`
  becomes `attrs[None]` and emits broken HTML. Use:
  ```python
  if kwargs.get('x') is None:
      kwargs['x'] = self.default_x
  ```

- **Mechanical replacement: N similar sites need N tests** (#1104).
  When a PR makes the same change at N call sites, the test suite must
  cover all N — not "a representative few". Identical-looking ≠ tested;
  one site's surrounding context can subtly differ. PR #1102 missed the
  radio site (`frameworks.py:345`) of 5 because tests only covered 4.

- **CHANGELOG additions to existing test files: name the CLASS, not
  the file** (#1106). The pre-push hook
  `scripts/check-changelog-test-counts.py` reads
  `N regression cases in path/to/file.py` as a claim about the FILE's
  total count. When adding K tests to a file with M existing tests,
  write `New cases in TestNewBehavior` — never
  `K regression cases in tests/test_existing.py`. Tripped twice in 24h
  (PR #1105, PR #1112).

- **Filter-shape parameters: contract is `Iterable[T]`, not `list[T]`** (#1108).
  When a parameter is used for membership checks (`fname in filter_x`),
  the contract is "any iterable supporting `in`" — list, tuple, set,
  frozenset all work. Don't annotate as `list[T] | None`; that lies
  about the contract. Test at least one non-list shape (tuple OR set)
  to lock it in.

- **Test fixtures with class-varying state: dynamic subclass, not class
  mutation** (#1109). When a test fixture needs different class-level
  state per instance, use `type('Name', (Base,), {'attr': value})` to
  build a fresh subclass per call. Do NOT do `type(self).attr = value`
  in `__init__` — that mutates a shared object and leaks across tests.

- **Async-callback test stubs MUST yield a microtask** (PR #1113 retro).
  When stubbing a browser API whose real implementation runs callbacks
  in a microtask (`startViewTransition`, `MutationObserver`,
  `IntersectionObserver`, etc.), the stub MUST do
  `await Promise.resolve()` BEFORE invoking the callback. Sync
  invocation lies about real-browser semantics — PR #1092 shipped a
  bug because of exactly this. Add a regression test that asserts
  intermediate state is UNCHANGED before await; that test fails-fast
  against any future stub regression.

- **Multi-issue batch PRs: include an issue × file × test mapping table
  in the PR body** (PR #1115 retro). For batch PRs closing >2 issues, a
  single table mapping each issue → modified files → covering tests
  makes Stage 11 reviewers' job faster. Without it, the reviewer has
  to derive the mapping from prose.

## Process canonicalizations from v0.8.6 retro arc

Five additional rules from the View Transitions arc + downstream-consumer data_table arc.

- **Split-foundation pattern for high-blast-radius features** (#1122).
  When a feature has blast radius (signature changes, new patterns
  across many call sites, or correctness depends on non-obvious
  browser/runtime semantics), split foundation from capability into
  separate PRs. Foundation should soak through one or more releases
  before the capability rides on top. Validated 3× across the View
  Transitions arc: PR-A async signature (v0.8.5) → #1098 interleaving
  fix (v0.8.6) → PR-B wrap (v0.8.6). PR #1092's earlier monolith
  attempt shipped a sync-callback bug. Apply this when:
  - Signature change touches public surface (`window.djust.X`)
  - Feature correctness depends on browser semantics that JSDOM
    can't fully model (microtasks, paint timing, layout)
  - More than ~5 call sites need migration

- **Pre-mount/post-mount keyset invariant test** (#1123). Any
  framework-level context dict with both a default form (returned when
  state isn't initialized) and a runtime-populated form (returned
  post-mount) needs a test asserting `post_mount_keys ⊆ pre_mount_keys`.
  Future post-mount additions that forget to update the default trip
  the test immediately. Pattern from PR #1117's
  `test_pre_mount_default_has_required_template_keys` — the symmetry test
  would have failed had a future PR updated only the post-mount dict;
  PR #1118 (the show_stats fix) is the closing case that exercises that
  branch.

  Note: this test is **one-directional** (`post ⊆ pre`). The inverted
  bug class — pre-mount declares a key that post-mount silently drops —
  is the failure mode #1118 actually hit. Existing pre-mount-only keys
  (`current_group_by`, `current_density`, `visible_columns`,
  `row_order`, `column_order`) intentionally don't appear post-mount and
  would false-positive a strict-equality test. If/when those keys move
  to genuinely-post-mount, tighten to `post == pre` or add a per-key
  whitelist for the legitimately-pre-only set.

- **CodeQL `js/tainted-format-string` self-review checkpoint** (#1124).
  When introducing or modifying logging where the format string's
  interpolated value comes from user-controlled data (DOM attributes,
  server frame fields, request body), use:
  ```javascript
  console.error('[label] msg %s:', userControlledValue, errObj);
  ```
  NOT:
  ```javascript
  console.error(`[label] msg ${userControlledValue}:`, errObj);  // CodeQL flags
  ```
  The `%s` parameterized form pulls the dynamic value out of the
  format string entirely. PR #1120 hit this post-CI; the fix was
  one-line per call site. Add as a Stage 7 self-review grep target.

- **Bulk dispatch-site refactor + count-test pattern** (#1125). When
  introducing a helper that wraps many call sites (e.g. decorators,
  lifecycle dispatchers), include a count-based test that enumerates
  the EXPECTED sites and asserts the count matches what's actually in
  the codebase. Catches future additions that forget to follow the
  pattern. Examples: PR #1117's
  `test_handler_count_matches_expected` (21 `on_table_*` decorators),
  PR #1120's regex-based grep for `_safeCallHook` callsite count.

- **Format-string hygiene in test assertions** (PR #1120 retro).
  Tests that capture `console.error` calls should target the LABEL
  arg position (e.g. `errors[0][1]`), not substring-match the format
  string (`errors[0][0].toContain('label')`). Decouples the test from
  later parameterization fixes for tainted-format-string warnings.

## Process canonicalizations from v0.9.0 retro arc

Each rule below was a v0.9.0 retro tracker row that surfaced repeated
failure modes across the 6-PR shape-C wave (#1128, #1135, #1138, #1139,
#1141, #1142). Canonicalized here so the next feature wave doesn't
repeat them.

- **Stage-4 first-principles grep before architecting** (#168 / #1143).
  Before proposing a new abstraction or wire-protocol shape in Stage 4,
  grep the codebase for how *existing* call sites solve the same
  problem. Three v0.9.0 PRs (#1128 mount-batch, #1041 component
  time-travel, #1135 lazy=True dispatch) shipped cleaner because the
  Plan stage opened with a "how do other features do X?" pass that
  surfaced the existing pattern. Skipping this step produces NIH
  abstractions that add surface area without adding capability — and
  Stage 11 reviewers correctly flag them. The grep targets:
  - **Wire-protocol decisions**: `python/djust/websocket.py` and
    `python/djust/streaming.py` for outbound frame shapes.
  - **State-snapshot patterns**: `python/djust/time_travel.py` and
    `python/djust/live_view.py:_capture_snapshot_state`.
  - **Async dispatch**: `python/djust/mixins/async_work.py:45` for the
    canonical `start_async` definition (see also the dispatch site
    that consumes it).
  - **Decorator composition**: `python/djust/decorators.py` — every
    djust decorator stamps metadata on `func._djust_decorators` (a
    dict keyed by decorator name, e.g. `{"event_handler": True,
    "action": {...}, "lazy": True}`). Inspect existing decorators
    via `is_event_handler` / `is_action` (line ~220 / ~396) to see
    the contract before adding a new one.
  - **Component lifecycle hooks**: `python/djust/components/base.py`
    for the canonical mount/unmount/refresh order.

  The Plan stage output should explicitly cite the file:line of the
  pattern being mirrored. "Mirrors `mixins/async_work.py:45` shape" is
  better than "follow the standard pattern" — the latter is unverifiable.

- **Branch-name verify check before commit** (#169 / #1144). Twice in
  v0.9.0 a commit landed on the wrong branch — the implementer was on
  branch X but had a state file pointing at branch Y, and the commit
  silently went to whichever branch git's HEAD pointed at. Add a
  pre-commit reflex: discover the active state file by matching its
  `branch_name` field against `git symbolic-ref --short HEAD`. The
  one-liner (no placeholder — copy-paste runnable):
  ```bash
  HEAD=$(git symbolic-ref --short HEAD)
  STATE=$(grep -l "\"branch_name\": \"$HEAD\"" .pipeline-state/*.json 2>/dev/null | head -1)
  if [ -z "$STATE" ]; then
    echo "ERROR: no state file for branch $HEAD — run /pipeline-next or /pipeline-ship first"
    exit 1
  fi
  echo "OK: HEAD=$HEAD matches state file $STATE"
  ```
  Add this to the pipeline-run skill's pre-commit checklist (alongside
  the existing post-commit `&& git log -1 --oneline` reflex). The
  failure mode is silent (commit lands on wrong branch, gets pushed,
  then the state file's pr_number points at a PR with the wrong
  changes) and the recovery is expensive (cherry-pick, force-push,
  re-run pre-push hooks).

## Process canonicalizations from v0.9.1 retro arc

Each rule below was a v0.9.1 retro tracker row distilled across the
7-PR drain (#1159, #1161, #1163, #1164, #1166, #1168, #1170).
Canonicalized here so the next drain doesn't repeat the failure mode.

- **One implementer agent per checkout** (#180 / #1172, applied to
  `~/.claude/skills/pipeline-run/SKILL.md`). Two background implementer
  agents on the same git working tree flip branches via pre-commit
  stash/restore mid-edit and produce CHANGELOG cross-contamination +
  duplicate-heading collisions on `[Unreleased]` blocks. Either
  serialize agent execution OR use the single-script-transformation
  pattern (each agent writes one Python script that applies all edits
  in one filesystem pass + commits immediately). Different git
  worktrees or different repos are safe — the rule is one-checkout =
  one-agent.

- **Two-commit shape: impl+tests / docs+CHANGELOG** (#181 / #1173,
  applied to `.pipeline-templates/feature-state.json`,
  `bugfix-state.json`, `ship-state.json`). Stage 5 (Implementation)
  forbids CHANGELOG.md edits; Stage 9 (feature/bugfix) or Stage 5
  (ship-pipelines, since they have no separate Implementation stage)
  is the canonical CHANGELOG commit boundary. Defends against
  cross-edit collisions on `[Unreleased]` even under serial execution.

- **3-clean-runs verification gate for pollution-class fixes** (#182 /
  #1174, applied to `.pipeline-templates/bugfix-state.json` Stage 6).
  When the bugfix task description matches `/pollution|leak|flak|test
  isolation/i`, run the full pytest suite three times consecutively;
  all three must be clean. Single-run pass is insufficient — pollution
  by definition shows up under specific orderings, and the "second
  hidden polluter" failure mode is real (PR #1159 caught
  `sys.modules`-rebind on the third verification run after the
  primary SQLite leak fix).

- **CSP-strict defaults for new client-side framework code** (#183 /
  #1175). Any new framework feature that emits HTML must default to:
  no inline `<script>` blocks, no inline event handlers, auto-bind
  via marker class + delegated listener on `document`/root. Use a
  static JS module served from `python/djust/.../static/` that
  registers itself on `DOMContentLoaded` + a `MutationObserver` for
  morphdom-managed regions. Inline scripts with `request.csp_nonce`
  are the rare exception (lazy-fill / #1147 case). The PR-checklist
  has a CSP-Strict Defaults block at `docs/PULL_REQUEST_CHECKLIST.md`
  with concrete external-module-shape references.

## Process canonicalizations from v0.9.4 retro arc

Six rules distilled from v0.9.4 retro tracker rows #185–#190 (PRs #1190, #1192, #1193, #1194). Each was a Stage 11 finding; canonicalized here so the next time-travel/refactor/canon-doc/index-cursor PR doesn't repeat the failure mode.

- **Refactor-with-helper guard audit** (#1195). When extracting a
  helper from N call sites that previously had inline input-validation
  logic, audit each call site to decide explicitly: push the
  validation INTO the helper, or keep it AT the call site. Failure
  mode is silent — production keeps working when inputs are well-formed,
  breaks only on malformed inputs that may not appear in tests.
  PR #1194's `_sendTimeTravelMessage` extraction inadvertently dropped
  a `typeof index !== 'number'` guard for programmatic callers; the
  DOM dispatch path still validated via `parseInt+isNaN`, so the bug
  only mattered for non-DOM callers. Stage 11 caught it.

- **Delegated-listener integration tests** (#1196). For any "marker
  class + delegated event listener" feature, unit tests (direct method
  invocation) and integration tests (real DOM event → registered
  handler → method) need separate coverage. Method-level tests verify
  methods, not the wiring (`parseInt`, `target.closest`, branch dispatch
  order, containment check). PR #1194's first version had 17
  method-level vitest cases but ZERO integration tests; backfill added
  6 integration cases (one per click branch + non-tt-button +
  non-numeric data). Rule: at least one integration test per
  delegated-listener selector branch.

- **Canon-doc citation discipline** (#1197). Every `file:line`,
  attribute name, method name, and bash one-liner cited in a canon doc
  (CLAUDE.md, PR-checklist, ADR) should be `grep`-verified before
  committing. PR #1192 had 5 inaccuracies in a 3-rule docs PR —
  wrong line numbers, wrong attribute names (e.g.,
  `_event_handler` literal doesn't exist; the marker is the
  `_djust_decorators` dict), bash one-liners with placeholder
  `<state-file>.json` (not copy-paste runnable), wrong section
  ordering, speculative prose claims. Stage 11 reviewers will run
  the greps anyway — pre-empting saves a roundtrip and keeps adjacent
  canon trustable. Rule: pre-commit on any canon-doc PR, grep every
  cited symbol, run every code block, verify section ordering.

- **Commit-or-rollback handler shape** (#1198). Any async handler
  that does BOTH a state mutation AND has an early-return path
  (validation failure, downstream failure, missing dependency) should
  mutate AFTER the commit point. Two clean shapes:
  1. Defer the mutation past all early-return checks (preferred for
     single-attribute mutations).
  2. Wrap in try/except with explicit rollback (justified only when
     multiple mutations need atomic rollback).

  Failure mode is silent — early-return doesn't raise, so observability
  tools won't flag it. State stays in a half-committed shape. PR #1193's
  `handle_forward_replay` set `view._time_travel_branch_id = new_branch`
  BEFORE awaiting `replay_event`; on `replayed is None` (handler
  missing), branch state stayed bumped with no recorded events; view
  + client diverged. Stage 11 caught it.

- **Index/cursor edge-case coverage** (#1199). When implementing a
  handler with index or cursor logic, run through the cases at
  `index=0`, `index=len/2`, `index=len-1`, `index=len` (out of range)
  before declaring done. Four mental cases catch most off-by-one
  classes. PR #1193's `_build_time_travel_state` and
  `handle_forward_replay` answered the same boolean question with
  different formulas; they disagreed at `cursor=len-1, which="before"`
  with override_params. Rule: every handler with index logic gets at
  least one test at each boundary (0, mid, len-1, out-of-range).

- **Tautology test detection** (#1200). When a test asserts "this
  thing happened", check whether the assertion would ALSO pass if the
  action under test did nothing. If yes, it's a tautology — production
  state from prior tests, fixtures, or module setup may be making it
  pass for the wrong reason. PR #1190's
  `test_ready_completes_other_setup_even_when_auto_enable_skipped`
  asserted `any(isinstance(filters, DjustLogSanitizerFilter))`, but
  every prior test in the file calls `app.ready()` which adds another
  filter (no idempotency guard). By test #6, the filter was already on
  the logger from prior calls — assertion passes even if test #6's
  ready() did nothing. Fix pattern: snapshot count BEFORE, assert
  count grew by exactly 1. Rule: for any "action happened" assertion,
  ask "would this pass if the action didn't run?"

## Process canonicalizations from v0.9.3-4 retro arc

Rules distilled from the v0.9.3-4 audit and process drain bucket.

- **Bulk renames use single-script transformation** (#1312). When a PR
  renames a symbol/string across >5 sites or multiple files, use a single
  Python (or shell) script that does the entire transformation in one pass
  + immediately stages the changes. Do NOT use incremental Edit-tool calls
  for bulk renames — they leave intermediate states where pre-commit hooks
  may see inconsistent code, increase reviewer cognitive load, and burn
  agent context. The script doubles as documentation of what was changed.
  Action #180 (v0.9.1) already lists the single-script-transformation
  pattern as a safe alternative for parallel-agent safety; this rule
  extends it to single-agent bulk operations regardless of agent count.

- **Symbol-migration grep canon (#1391, #1400, v0.9.3-2 + v0.9.5-2 retros).**
  When changing a filter convention OR removing a top-level symbol as part
  of a refactor, grep the codebase for the EXACT pre-fix expression / OLD
  symbol name across `tests/`, `python/tests/`, `examples/`, and any other
  consumer directory. Verify all matches are updated. Two failure-mode
  classes this catches:

  1. **Filter conventions** (e.g., `k.startswith("_")` → `k in _framework_attrs`).
     Filters that operate on the same data type often have parallel
     implementations (Python change-tracker, Rust differ, push-commands path,
     identity snapshots). A migration that updates one path and not the
     others creates a latent invariant violation that may only surface in a
     future audit. PR #1281 fixed `_snapshot_assigns()` but identity
     snapshots stayed on the old filter until the audit found it (#1327).

  2. **Symbol removals during refactor** (extracting a helper, removing a
     module-global, deprecating a function). Python imports are resolved
     at runtime — the compiler doesn't catch orphan references. PR #1399's
     `_TRUNCATION_WARNED` removal left an orphan import in
     `test_snapshot_truncation_warning.py`; pre-push hook caught it after
     the commit landed locally.

  Concrete check during Stage 4 planning AND Stage 5 implementation: for
  any PR that changes a filter expression OR removes a top-level symbol,
  grep for the pre-fix text / OLD symbol name across the repo and visually
  scan all hits. The grep is fast (<1s); the failure mode is hours of
  debugging an orphan reference 2 sessions later.

- **Split-foundation soak-time guidance (#1385, v0.9.5-1 retro).** When
  an iteration ships a new public API surface AND the framework has
  external consumers, soak the API for at least one release before
  stacking the next iteration. When the framework owner is the only API
  customer (no external production usage), soak is optional — proceed
  directly. Document the soak decision explicitly in the milestone retro
  for future reference. Empirical: v0.9.5-1's three iterations
  (-1a/-1b/-1c) shipped in <3 hours with NO soak; iterations stacked
  cleanly because there were no external consumers. Action #1122
  (split-foundation pattern) primary value is API design lock-in, not
  calendar soak.

## Process canonicalizations from v0.9.6-1 retro arc

Five rules from the v0.9.6-1 retro tracker rows #245–#250 (PRs #1431, #1438, #1441, #1442, #1443, #1444). Canonicalized here so the next milestone doesn't repeat the failure modes.

- **Lock-release/lock-reacquire TOCTOU rule (#245 / #1445).** When a code path acquires a lock, releases for unlocked work (CPU-only round-trips, network calls, async yields), then re-acquires the lock to mutate state, the entry it's mutating may have been replaced by a concurrent writer. Identity-guard the mutation (`current is original_ref`) or version-counter check at re-entry. Same class of failure as Action #1198 (`commit-or-rollback handler shape`) but for lock-windows rather than await-windows. Canonical case study: PR #1438's `python/djust/state_backends/memory.py:117-141` — the first-pass fail-closed pop did `lock → read → unlock → round-trip → relock → pop(key)` and a concurrent `set(key, new_view)` in the unlock window would have been clobbered. Identity-guarded with `current[0] is view`.

- **Zero-cost-when-unused middleware/processor pattern (#246 / #1446).** Any middleware or context-processor for an optional djust extra (`tenants`, `theming`, `presence`, `streaming`, etc.) should detect "not opted in" once in `__init__` and switch the hot path to a no-op that just calls `get_response(request)`. Preserve attribute existence on `request` (e.g., `request.tenant = None`) so `getattr(request, "X", None)` callers see the same shape. Canonical case studies: PR #1441 (`TenantMiddleware` short-circuits when neither `DJUST_CONFIG['TENANT_RESOLVER']` nor `DJUST_TENANTS` is set) and PR #1443 (`theme_context` pre-rendering with fail-soft empty-string fallback). Saves ~2-5% per-request CPU when unused.

- **Cache-by-struct: include all fields upfront, prune later (#247 / #1447).** When wrapping a function whose inputs are derived from a struct, the cache key MUST include every field of the struct. Pruning a field later (because profiling shows it doesn't matter) is a one-line change with a regression test. Adding a field later means cache-poisoning bugs in production — two callers with different field-N values get the same cached output. Canonical case study: PR #1442's `_render_theme_outputs` initially keyed on `(preset, pack, mode, resolved_mode, presets_key)` but missed `theme` and `layout`; test failure caught it pre-merge.

- **Wire-protocol JSON pinning as a standard test class (#248 / #1448).** Any Rust↔JS or Python↔JS wire-format that's a `serde`/`json.dumps`-derived contract with a client gets a snapshot-test file. Existing tests verify *semantics* (`this input produces this output`); the new class pins the *JSON shape*. A field rename or `#[serde(skip_serializing_if = "Option::is_none")]` removal would silently break every deployed client running an older bundle. Canonical case study: PR #1444's `crates/djust_vdom/tests/wire_protocol_snapshot.rs` (16 literal-string assertions for every Patch variant + VNode struct + every optional-field permutation).

- **Stage 11 must verify branch is not stale vs base BEFORE reviewing (#250 / #1450).** Any PR opened a non-trivial number of commits ago + not rebased gets a stale-base diff. The reviewer reviews against THAT base; the merge applies on top of CURRENT main. The two are different programs. Canonical case study: PR #1431 was 7 PRs behind main; the diff vs main *deleted* 5 CHANGELOG entries, the entire v0.9.6-1 retro, the wire-protocol snapshot test, and reverted the perf rewrites — merging would have silently undone v0.9.6-1. All 13 CI checks were green; the merge button looked safe. The reviewer subagent caught it via `git log main..HEAD --oneline` showing only 1 commit despite 7 on main since branch base. Mandatory Stage 11 check:

```bash
git fetch origin
BEHIND=$(git rev-list --count HEAD..origin/<base>)
if [ "$BEHIND" -gt 0 ]; then
  echo "STOP: branch is $BEHIND commits behind origin/<base>. Rebase before reviewing."
  exit 1
fi
```

If BEHIND > 0, STOP. Rebase (`git rebase origin/<base>`) or merge base into branch BEFORE reviewing. Reviewing against a stale base reviews a different program than the merge will apply.

## Process canonicalizations from v0.9.6-2 retro arc

One rule from the v0.9.6-2 drain (PRs #1454, #1455, #1457). The other v0.9.6-2 retro tracker rows (#251 pre-commit ruff auto-restage, #248-follow-up #1456 wire-protocol pinning for ~22 remaining shapes) are filed for v0.9.7+ and don't change canon yet.

- **Empirical canary for tooling/lint PRs (#252 / #1459).** For any PR whose central claim is "catches bug class X" (lint extension, static-analysis addition, new system check, AST walker, codemod-style tool), Stage 11 review must construct a SYNTHETIC bug-trigger of class X — ideally by copying a real pre-fix shape from git history — run the tool against it, and confirm the tool reports the trigger.

  **Why**: empirical canary is the highest-confidence validation a tooling PR can get. Inspection-only review can rubber-stamp a lint that doesn't actually catch what it claims to catch.

  **How to apply**: in the Stage 11 prompt's "What to check" list, include a "synthetic-bug-trigger empirical canary" item for tooling-class PRs. The reviewer subagent:

  1. Identifies a real pre-fix commit from history that exemplifies bug class X (e.g., for a bundle-init-order lint, `git log --all -- python/djust/static/djust/src/19-hooks.js` to find the pre-#1370 shape).
  2. Constructs a synthetic test bundle that re-introduces that shape (in a copy, never on main).
  3. Runs the tool against the synthetic bundle and asserts it reports the bug.
  4. Cites the file:line of the synthetic trigger in the review comment.

  **Canonical case study**: PR #1455 (depth-N bundle-init-order walker). The Stage 11 reviewer flipped `var _activeHooks` → `let _activeHooks` in a copy of `python/djust/static/djust/src/19-hooks.js` (the exact pre-fix shape of #1370). The walker reported the transitive chain `djustInit() → mountHooks() → _ensureHooksInit()` at depth 3, plus two more variants via the Turbo reinit path. Without the empirical canary, the reviewer would have rubber-stamped the lint based on the unit tests alone. With it, "the walker catches the bug class it claims to catch" was empirically proven, not just trusted from inspection.

  Generalizes Action #1046 (doc-claim verbatim TDD) for the tooling-PR subclass: the doc claim "this lint catches X" gets an executable verifier (run the lint against the canonical X shape).

## Process canonicalizations from v0.9.7-2 retro arc

One rule from the v0.9.7-2 drain (PR #1466 — clean-redo of stale PR #1429 via `/pipeline-run`).

- **Gate-the-change-off tautology self-test (Action #254 / #1468).** Action #1200 (tautology test detection) is a Stage 11 reviewer concern: gate the change off, re-run tests, see which fail. PR #1466 showed this needs to fire at Stage 5 (implementer) too — the first-pass subagent shipped 7 tests, and only 3 (source-grep pins) exercised the actual change under test. The other 4 mocked the session / proxied via HTTP-path POST / reproduced logic in pytest. Stage 11 caught all 4 via the gate-off check; the Stage 13 fix-pass replaced one with a real `WebsocketCommunicator` integration test.

  **Why**: subagents under context pressure write tests that look plausible but exercise the wrong path. The gate-off self-test makes "would this test fail if the change did nothing?" an explicit verification step, not an implicit assumption.

  **How to apply**: after writing new tests + verifying they pass, temporarily revert the change under test — set a flag to `False`, comment out the new behavior, gate it on an always-False conditional, or similar. Re-run the tests. Confirm AT LEAST ONE of the new tests fails — preferably the most behavior-meaningful one. Restore the change. If all tests still pass with the change gated off, AT LEAST ONE test is tautological. Fix before reporting "tests pass."

  **Canonical case study**: PR #1466 (WS-reconnect state continuity, the clean-redo of stale PR #1429). First-pass subagent reported "7/7 tests pass." Stage 11 reviewer ran the gate-off check (`if False and target_view is self.view_instance:`) and found 4 of 7 still passed — they were tautological. Stage 13 fix-pass: replaced `test_round_trip_save_then_restore_proxies_ws_reconnect` (HTTP-POST proxy — never exercised `handle_event`) with `test_ws_event_save_block_writes_through_to_session` (real `WebsocketCommunicator` against `LiveViewConsumer.as_asgi()`). Empirically validated: gating the save off makes the new test fail with `"Save block in handle_event must have written 'liveview_/counter/' to the session — but the key is absent"`.

  Generalizes Action #1200 from "reviewer applies at Stage 11" to "implementer applies at Stage 5." Same epistemic, left-shifted by 6 stages. Saves the Stage 13 fix-pass cycle when subagent tests turn out tautological.

  **Where this lives now**: `docs/PULL_REQUEST_CHECKLIST.md` Test Quality section as a one-bullet requirement. Out-of-repo follow-up: the implementer-subagent prompt template in the pipeline-run skill repository should add a Verification-section step calling this out explicitly.

## Process canonicalizations from v0.9.7-3 retro arc

One rule from the v0.9.7-3 drain (PRs #1469, #1470 + the #1467 investigation).

- **LiveComponent vs sticky-child LiveView event-routing distinction (#1467 investigation).** Two distinct mechanisms exist for embedded children in a djust page; they look similar from a template/user perspective but route events through different code paths and have different persistence semantics:

  1. **LiveComponents** (`python/djust/components/base.py` `LiveComponent`): assigned as parent attributes (`self.foo = MyComponent(...)`); routed via `component_id` param; resolved at `python/djust/websocket.py:2856` via `self.view_instance._components.get(component_id)`; persisted via `_save_components_to_session` walking parent's `get_context_data()`.

  2. **Sticky-child LiveViews** (`{% live_render %}`-embedded full `LiveView` subclasses): registered via `StickyChildRegistry._register_child`; routed via `view_id` param; resolved at `python/djust/websocket.py:2689-2696` via `self.view_instance._get_all_child_views()`; NOT persisted (gap; tracked at #1471).

  **Implication for save-block work**: when the `handle_event` save block gates on `target_view is self.view_instance`, only sticky-child events are skipped (LiveComponent events pass the gate because `target_view` stays as parent — `component_id` routing doesn't reassign `target_view`). PR #1466's gate was originally written about "child LiveComponent views" but the actual path it gates is sticky-children. Future readers should not conflate these.

  **Investigation cost saved**: ~1 hour of code-path tracing at Stage 4 of #1467. The issue body and #1466's gate comment both used "LiveComponent" loosely to mean "embedded child"; tracing the routing showed LiveComponents already persist via the existing parent-save path, and only sticky-child LiveViews need new architectural work.

  **Where this lives now**: this CLAUDE.md section + the #1467 close comment + the #1471 follow-up issue body.

  **Generalized rule for child-routing PRs**: when working on `handle_event`-adjacent code, explicitly state whether the change targets LiveComponents (`component_id`), sticky-child LiveViews (`view_id`), or both. Test the routing path you claim to affect — a `component_id`-routed test does NOT exercise the sticky-child path, and vice versa.

## Additional Documentation

- `docs/PULL_REQUEST_CHECKLIST.md` — PR review checklist
- `CONTRIBUTING.md` — contribution guidelines
- `QUICKSTART.md` — quick setup guide
- `docs/STATE_MANAGEMENT_API.md` — decorator API reference
- `docs/website/guides/loading-states.md` — loading states & background work guide
- `DEVELOPMENT_PROCESS.md` — 9-step development process
