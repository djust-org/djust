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
│   ├── checks/             # Django system checks (C/V/S/T/Q/A/Y categories), split by family (#1822): utils, configuration, integrations, components, security, templates, accessibility, quality
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

## Process canonicalizations from v1.0.0rc2 retro arc

One rule from the v1.0.0rc2 drain (PRs #1504, #1506, #1508, #1510, #1512 —
9 issues, 5 PRs). Tasks 1-3 of the drain each lost time to the same failure
class; tasks 4-5 applied the lesson. Canonicalized here so the next drain
doesn't repeat it.

- **Verify environment premises before acting on them (#1516, v1.0.0rc2
  retro finding #1).** A subagent — planner, implementer, or reviewer —
  will silently assume facts about repo state unless the pipeline either
  verifies them upfront or states them in the subagent's brief. Three
  v1.0.0rc2 tasks were bitten by exactly this:

  1. **File-tracked state (PR #1506).** The Stage-4 plan assumed
     `.claude/skills/djust-release/SKILL.md` was an in-repo file and
     scheduled an edit to it. `.gitignore:73` ignores all of `.claude/`;
     `git ls-files .claude/` returns nothing on `main`. **Before planning
     any edit to a file, verify it is tracked:**
     ```bash
     git ls-files --error-unmatch <path> 2>/dev/null \
       || echo "NOT TRACKED: $path — the edit cannot land via this repo's PR"
     git check-ignore -v <path> 2>/dev/null \
       && echo "GITIGNORED: $path — re-scope as out-of-repo"
     ```
     An untracked or gitignored target is a hard signal the work belongs
     out-of-repo (or the plan's repo-boundary assumption is wrong). This
     extends the Stage-4 "VERIFY LITERAL API CONTRACTS" discipline from
     symbol names / line numbers to **file-tracking state**.

  2. **`git add -f` on a gitignored path is a STOP, not a workaround
     (PR #1506).** When `git add` reports a path is ignored, that is
     *information* — usually the file is intentionally out-of-repo
     (user-private skills, secrets, generated artifacts). Do NOT reach for
     `-f`. Stop, surface the conflict between the plan's premise and the
     repo's gitignore state, and re-scope. Force-adding silently converts
     a planning error into a committed artifact that then needs an amend
     (or escapes to `main`).

  3. **Execution-verify doc-snippet fixes (PR #1508).** An import-path
     correction to a fenced code snippet that is "plausible on inspection"
     can still raise on copy-paste (PR #1508: a snippet used
     `class X(Component)` while `register_component` requires
     `LiveComponent` — disjoint hierarchies). AST + import-resolution
     checks (`scripts/check-doc-snippets.py`) are necessary but not
     sufficient — they cannot catch a phantom method call or a wrong base
     class. Any doc-snippet edit must be *executed* (`django.setup()` +
     `exec` the snippet body, or a minimal harness) and confirmed to raise
     no exception before the fix is reported done.

  The durable form of the rule: the gate that works is **active
  falsification** — construct the case that would disprove the premise and
  run it — not passive inspection. PR #1512's Stage 7 caught a real
  `\b`/`data-tabindex` regex false-match precisely because it built a
  falsifying `data-tabindex` case rather than re-reading the regex.

## Process canonicalizations from v1.0.0rc3 retro arc

One rule from the v1.0.0rc3 drain (PRs #1518, #1519, #1520, #1521 — the
final pre-1.0 retro-backlog drain). The drain's defining thread —
"verify, don't assume" — was already canonized in the v1.0.0rc2 section;
rc3 surfaced one concrete blind spot in the existing post-commit
verification reflex.

- **`git commit --amend` verification must assert the HEAD hash CHANGED
  (#1524, v1.0.0rc3 retro finding #2).** Action #122 prescribes a
  `&& git log -1 --oneline` reflex after every `git commit` to detect a
  pre-commit-hook-swallowed commit. That reflex works for the
  create-a-new-commit case: a swallowed commit leaves the PREVIOUS
  subject visible, which is the signal. It does **not** work for
  `git commit --amend` — after a bounced amend the OLD commit is still
  HEAD with its OLD subject, so a subject *is* shown and the reflex
  passes green on a failure.

  Observed in PR #1519: a fixer's `--amend` was swallowed by a
  pre-commit reformat; HEAD stayed at the pre-fix hash `5ca016d0`,
  `git status` showed `MM` (staged + working-tree-modified,
  uncommitted), and the fixer agent reported "amended commit 5ca016d0"
  — quoting the stale hash without verifying. The orchestrator caught
  it on first principles (amend always rehashes, so an unchanged HEAD
  after `--amend` is definitionally a bounce; a `git show HEAD:` grep
  for the new symbol returned 0).

  **The rule**: for `git commit --amend` specifically, capture the
  pre-amend hash and assert it changed:
  ```bash
  PRE=$(git rev-parse HEAD)
  git commit --amend -m "..."
  POST=$(git rev-parse HEAD)
  if [ "$PRE" = "$POST" ]; then
      echo "FAIL: --amend bounced (HEAD unchanged). Re-stage and retry."
      exit 1
  fi
  echo "OK: amend registered — $PRE -> $POST"
  ```
  Also: any agent reporting a commit hash must obtain it from a live
  `git rev-parse HEAD` *after* the commit operation — never quote a
  hash printed earlier or planned. The PR #1519 fixer quoted
  `5ca016d0`, a hash that was never HEAD post-amend; sourcing the
  reported hash from `rev-parse` would have surfaced the bounce
  immediately. The `&& git log -1 --oneline` reflex stays correct for
  plain `git commit`; this rule is the `--amend` companion.

  The skill-prompt propagation of this rule into
  `~/.claude/skills/pipeline-run/SKILL.md` is tracked OUT-OF-REPO in
  #1524 (`.claude/` is gitignored repo-wide).

## Process canonicalizations from v1.0.0rc4 retro arc

Three rules from the v1.0.0rc4 drain (PRs #1526–#1542 — the final pre-1.0
backlog drain: ADR-018 sticky-child persistence + an 8-PR Phase-2 drain).
Each was a milestone retro finding; canonicalized here so the next
coverage-suite / value-dependent-bug / cross-environment-CI PR doesn't
repeat the failure mode.

- **A coverage/pinning suite must enumerate EVERY variant of the surface
  it covers (#1543-adjacent, v1.0.0rc4 retro finding #1).** Three
  correctness bugs surfaced *during* the rc4 drain — #1529 (VDOM diff),
  #1531 (`ThemeMixin` theme_head), #1538 (`VNode` msgpack) — and each had
  a purpose-built coverage effort that *looked* complete but shared one
  failure shape: the bug lived entirely in a variant the coverage never
  exercised.

  - The #1448 wire-protocol snapshot suite
    (`crates/djust_vdom/tests/wire_protocol_snapshot.rs`) — a whole
    milestone of work built to pin exactly the serde-asymmetry class
    #1538 is — pinned only the `serde_json` (named-map) encoding and
    never `rmp_serde` (positional array). A msgpack-only 5-vs-6-element
    `skip_serializing_if`-without-`default` asymmetry sailed through 16
    green tests.
  - #1522's keyboard-nav test matrix exercised each interactive widget
    in isolation and never *composed* two, so a dropdown-nested-in-a-
    dialog keyboard dead zone (#1533) shipped unflagged.
  - #1452 fixed one drift path of `theme_head.html` without enumerating
    its other consumers, so a third consumer —
    `ThemeMixin._setup_theme_context()` (#1531) — stayed silently broken
    until a downstream build hit it.

  **The rule**: when a suite exists to cover a bug class, it must
  enumerate every variant the surface actually has — every wire encoding
  a multi-encoding protocol uses, every N×N composition of N interactive
  widgets, every parallel consumer of a shared template/contract.
  Single-variant coverage of a multi-variant surface is false confidence,
  not coverage — and it is *worse* than no coverage, because it makes the
  bug class look handled. At Stage 7 self-review, for any new/modified
  test suite ask: "what variants of this surface exist, and does the
  suite touch each one?"

- **Empirically bisect the trigger of a value-dependent bug before
  architecting the fix (v1.0.0rc4 retro finding #2).** From PR #1530
  (#1529): the planning subagent did not just describe the symptom — it
  ran the bug variants and pinned the exact trigger boundary
  (`a=0,b=0` identical baselines reproduces; `a=1,b=2` distinct
  baselines does not; a single-value change does not). That narrowing
  *proved* the root cause was content-based first-match (content
  equality is not a unique key) rather than a path-accumulation bug in
  the VDOM differ — which the trace had to clear as a suspect — and it
  produced two regression cases for free (the distinct-baseline guard
  and the only-second-changed sharpest-mapping assertion).

  **The rule**: for any bug whose reproduction depends on input *values*
  and not just structure, find the smallest value change that flips the
  bug on/off *before* writing the fix. The trigger boundary is the
  root-cause proof and seeds the regression test. Extends the
  "Bug-report triage" section's symptom-up tracing with a value-axis
  bisection step.

- **A CI job exercising an environment the dev machine cannot reproduce
  needs ≥1 runner-only iteration budgeted, and known ecosystem gaps
  researched at plan time (v1.0.0rc4 retro finding #3).** From PR #1540
  (#1534): the new `python-tests (py3.14t free-threaded)` job
  (`.github/workflows/test.yml:145`) failed twice on its first real
  runs. Fail 1 — `uv sync --extra dev` pulled `orjson`, which has no
  free-threaded wheel, so dependency install failed before the smoke
  test ran. Fail 2 — `uv run maturin develop` re-managed the project env
  from `pyproject.toml` with the *default* 3.12 interpreter, wiping the
  hand-built 3.14t venv. Neither was catchable by `yaml.safe_load` +
  local reasoning; both are structural facts of the free-threaded
  ecosystem / `uv` semantics that only surface on the actual runner.
  Fail 1 *was* predictable at plan time — #1432's own issue body had
  already documented that the free-threaded path works "after dropping
  orjson/psycopg2-binary."

  **The rule**: when a PR adds a CI job exercising a toolchain or
  interpreter the dev machine cannot run, (a) treat ≥1 runner-only
  iteration as expected, not a process failure — do not mark the PR
  blocked on it; and (b) at plan time, grep prior issues/PRs touching
  that environment for already-documented ecosystem gaps (wheel
  availability, dependency-graph holes) and bake the workarounds into
  the first commit. Keep such jobs `continue-on-error: true` until they
  have shipped green at least once.

## Process canonicalizations from v1.0.0rc6 retro arc

Two rules from the v1.0.0rc6 open-issue drain (PRs #1546 / #1547 / #1548 / #1549). Each was a milestone retro finding; canonicalized here so the next serde-fix and the next security-PR don't repeat the failure mode.

- **Serde fix-shape generalization requires field-position verification (#1541 / PR #1546, sibling of #1538 / PR #1542).** When mirroring a serde annotation fix from one struct to another — particularly `#[serde(default, skip_serializing_if = "Option::is_none")]` and similar shape-sensitive annotations — verify that the field POSITION (leading vs trailing) matches between the source and target struct before assuming the fix generalizes. The empirical fact: `serde + rmp-serde` encodes a plain `#[derive(Serialize, Deserialize)]` struct as a *positional array*. `skip_serializing_if` on a STRICTLY TRAILING optional drops the trailing element on serialize; `#[serde(default)]` then fills it back on deserialize → round-trip works (the #1538 / `VNode.djust_id` case, where `djust_id` is the 6th and last field). `skip_serializing_if` on a LEADING optional shifts later array elements into the wrong positional slot on deserialize; `#[serde(default)]` does NOT help because the deserializer isn't running out of elements, it's reading wrong-typed values at the wrong positions (the #1541 / `PatchResponse.{patches, html}` case, where `patches` and `html` are fields 0 and 1, followed by `version: u64`). The correct fix for leading-optional or interior-optional shapes is to remove `skip_serializing_if` entirely — `None` is serialized as msgpack `nil` (1 byte) and positional slots stay aligned.

  **How to apply at Stage 4 plan time**: when the plan calls for mirroring a serde-annotation fix from a prior PR, the plan must include (a) the source-struct field ordering with the fixed field's position, (b) the target-struct field ordering with the analogous field's position, and (c) a one-line statement that the positions are equivalent (both trailing) OR that the target requires a different fix shape (remove `skip_serializing_if`). If the implementer at Stage 5 cannot trivially restate (c), the Stage 4 reproducer-first gate (Action #1210) must fire — write the failing reproducer and run a candidate-fix probe (the standalone `/tmp/<probe>.rs` pattern from the v1.0.0rc6 drain is fast — small `cargo` project with the candidate `serde` annotations, dump bytes + attempt round-trip across all None/Some combinations).

  **Where this lives now**: this CLAUDE.md section + the inline doc-comment on `PatchResponse` at `crates/djust_live/src/actors/messages.rs:96-114` + the three structural witness tests in `crates/djust_vdom/tests/wire_protocol_snapshot.rs::msgpack_skip_without_default_fails` / `_skip_with_default_works_for_trailing_optional_only` / `_no_skip_round_trips_in_all_positions`. Future maintainers grepping for "skip_serializing_if" or "msgpack" should find all three.

- **Security / lockfile-only Dependabot PRs may post a minimal 3-line Stage 14 retro (#1549).** The pipeline-run mandatory retro-artifact gate (filed in the v0.9.x retro arcs; codified in `pipeline-run/SKILL.md`) requires every PR to carry a Stage 14 retro before `completed_at` is set. PR #1549 (idna 3.11 → 3.15 / CVE-2026-45409 / Dependabot #101) was a one-line lockfile bump that shipped without a per-PR retro posted to the PR — the security path skipped the ceremony, and the v1.0.0rc6 milestone retro Stage 2 caught it as a `RETRO_GATE_VIOLATION`. The honest accounting is that a lockfile-only Dependabot bump genuinely has nothing useful to retrospect on at the per-PR level (the milestone retro is the right place to surface the gate violation).

  **The rule**: a security PR whose entire diff is a lockfile bump (`uv.lock`, `Cargo.lock`, `package-lock.json`, etc.) plus a `CHANGELOG.md` `### Security` entry — and which does NOT touch any code path — may post a minimal Stage 14 retro of the form:

  > ## Stage 14 retro — PR #NNNN (Dependabot #M)
  > - Advisory followed: [GHSA-…] / CVE-….
  > - Full regression suite ran clean: N passed, 0 failed.
  > - No API surface change; no behavioral change.
  > `RETRO_COMPLETE`

  Any PR that *touches code* in response to a security advisory (a guard-rail patch, a CVE that exposes a design weakness, a workaround for a vulnerability the upstream hasn't patched) gets a full Stage 14 retro per the existing gate — the minimal form is reserved for pure dependency-version bumps.

  **Where this lives now**: this CLAUDE.md section + the milestone retro entry under "Process Improvements Applied" in `RETRO.md` v1.0.0rc6. Future Dependabot PRs that ship clean lockfile-only diffs may use the minimal form; the milestone retro Stage 2 gate is preserved as a backstop in case the minimal form is forgotten.

## Process canonicalizations from v1.0.0rc14 drain arc

Two rules from the #1635–1645 open-issue drain (PRs #1646, #1649, #1650, #1651, #1652, #1653). Every one of the six issues was the **same meta-bug** — a path-specific invariant correct on one path and broken on a parallel one — so the canon is about the *class*, not any single fix.

- **Parallel-path-drift audit (#1646/#1640/#1637/#1635/#1645/#1642).** When a bug is a per-path invariant implemented in more than one place — sync vs async (#1638: mount `sync_to_async` vs per-event bare sync), main vs secondary send path (#1639/#1645: `handle_event` arms recovery, `_run_async_work` didn't), two DOM walkers (#1640: `getNodeByPath` dj-if-only vs `getSignificantChildren` all-comments), dev vs deploy (#1637: `migrate --run-syncdb` vs `migrate`), first-load vs re-execution (#1635: classic-script global lexical scope), HTTP-GET vs WS-mount baseline (#1642) — fixing only the cited path leaves the latent twin. **At Stage 4, grep every parallel path that implements the same invariant and decide each explicitly.** Prefer the structural cure over N correct copies: one shared helper (`_arm_recovery`, `_flush_all_pending`, `isDjIfComment`), a scope boundary (the client.js IIFE), or a guard that makes drift mechanically detectable (the regex writer-guard pinning `_recovery_html` is only assigned via `_arm_recovery`; `assert_http_ws_djid_parity` pinning the two render baselines agree). A point fix patches one instance; a structural fix retires the class.

- **Reproduction fidelity — the harness must exercise the REAL path, not a convenient proxy (#1650/#1638/#1637).** A reproducer that uses the wrong mechanism gives a false negative and hides the bug:
  - **Classic-script re-execution** (bfcache / `live_redirect` morph re-attaching `<script>`): reproduce by injecting two `<script>` elements, NOT `window.eval(code)` twice. `eval`'s top-level `const`/`let` scope to the eval call, not the global lexical environment, so `eval`×2 does NOT collide while two `<script>`s DO (#1650 — the eval repro passed; the `<script>` repro threw).
  - **Sync-ORM / auth-in-async bugs**: the view's `get_object()` (or any predicate) must do a REAL `Model.objects.get(...)`, not return an in-memory stub. Every pre-#1638 object-permission test used a `_StubDocument` and so never hit the `SynchronousOnlyOperation` path the bug lives on.
  - **Dev-vs-deploy bugs**: exercise the DEPLOY path. #1637's scaffold only ever ran `migrate --run-syncdb` (dev), which masked the missing migrations; the deploy `migrate` (no flag) was a different program and the only one that failed.
  - **Client-side VDOM / `morphChildren` bugs**: build the existing DOM the way the browser does — `container.innerHTML = "<div>…\n  <div>…"` WITH the inter-element whitespace a real SSR page carries — NOT via `appendChild`/`createElement` (which omit insignificant whitespace text nodes). #1724's SSR-hydration teardown reproduced ONLY with whitespace text nodes between the element children: the positional existing node was a whitespace text node when an element was processed, so every element-matching strategy skipped (they require `ELEMENT_NODE`) → clone+insert+remove (wholesale teardown, destroying a mounted Chart.js `<canvas>`). The first fix was dead code because the test used `appendChild` (no whitespace) + a standard `id` the renderer never emits (`dj-id`); the reviewer's `innerHTML`-with-whitespace + `dj-id` repro found the real whitespace-misalignment cause. The DOM-construction *method* itself is part of reproduction fidelity for morph/patch tests.

  Generalizes the existing "trust the symptom, not the cited path" triage rule with a mechanism axis: also distrust the *reproduction harness* until it exercises the same code path production does.

## Process canonicalizations from v1.0.2 retro arc

Two rules from the v1.0.2 drain (PRs #1725–#1731). The reproduction-fidelity
addition for client-side VDOM tests is folded into the "Reproduction fidelity"
bullet above (#1724); the rules below are the new standalone ones.

- **Promoting a "soft" CI check to blocking requires verifying it's in the aggregate gate's AND-condition — not merely in `needs`/echoed (#1713 / PR #1730).** When flipping a `continue-on-error` check (or a check that only prints its result) into an enforcing gate, the load-bearing question is not "is it a job?" but "does a failure actually fail the merge?" Trace it explicitly: a failing check → `needs.<job>.result == "failure"` → the aggregate gate's success `if`-condition (the `&&` chain in `test-summary`) evaluates false → the else-branch runs `exit 1`, AND there is no `continue-on-error` on the job, any of its steps, or the aggregate job itself. A check can be in the `needs:` list and echoed in the summary yet still NOT gate the merge (informational checks like playwright/security-scan are deliberately excluded from the AND). Pair this with the rc4 rule (#1534): a new CI job exercising an environment the dev machine can't fully mirror ships `continue-on-error: true` until it has been green on the runner at least once, THEN gets promoted. PR #1730's `demo-checks` followed both: green on first runner run, then added to the `test-summary` AND-condition with no `continue-on-error`.

- **Per-event work that feeds change-detection must be memoized, not first-sync-gated (#1722 / PR #1726, follow-up #1727).** When a fix applies request/per-render work (context processors, derived state) on a path that runs on EVERY WebSocket event (e.g. `_sync_state_to_rust`), do not "optimize" by running it only on the first sync — djust's change-detection only forwards *changed* vars, so the work must re-run each event to detect a change (e.g. a live theme switch). The correct cost reduction is request-scoped memoization of the expensive sub-renders, not gating the application. Also verify the per-event path actually has the inputs it needs: the WS-path `request` is a long-lived instance attr set in `handle_connect` (non-None), which is what makes such a fix effective on every navigation rather than only the initial GET.

## Process canonicalizations from v1.1.0 retro arc (security & navigation)

Two rules from the v1.1.0 security/nav arc (WS auth threat model + fixes,
`docs/audits/websocket-auth-2026-06.md`; PRs #1775, #1776, #1780, #1781, #1782,
#1783). Both are Action-Tracker rows #291/#292.

- **Multiplexed-path transport rule (#291 / PR #1780 review).** A
  transport-terminating side effect — `self.close()`, a connection drop, a
  socket-level write — placed inside a handler that is ALSO reused under a
  multiplexer/collector will fire on the *shared* transport mid-batch and kill
  the sibling operations. Canonical case: PR #1780's auth fix added
  `await self.close(code=4403)` inside `LiveViewConsumer.handle_mount`;
  `handle_mount_batch._mount_one` reuses `handle_mount` but swaps only
  `self.send_json` for a collector — NOT `close()` — so a single
  login-redirecting view in a `mount_batch` closed the whole shared socket,
  dropping the survivor mounts + the collected `navigate[]` and reconnect-storming
  the client. The existing batch test could not catch it (its fake consumer's
  `close()` is a no-op). **Rule:** before adding a transport-level side effect to
  a handler, grep for collector/batch reuse of that handler (a swapped
  `send_json`, a `_mounting_in_batch`-style flag, a `_collect` wrapper); gate the
  transport side effect on "not in batch," but apply the *state* change that
  closes the security/correctness gap (e.g. clearing `view_instance`)
  unconditionally. Same family as the parallel-path-drift rule (#1646) but for the
  multiplex axis. A real-`WebsocketCommunicator` batch test is required — a fake
  consumer with a no-op `close()` hides the bug.

- **Pre-commit can silently drop UNSTAGED working-tree files; recover from the
  patch cache (#292).** The `pre-commit` framework stashes UNSTAGED working-tree
  files to `~/.cache/pre-commit/patch<ts>-<pid>`, runs hooks against the staged
  snapshot, then restores. A failed/skipped restore (the stash-pop-conflict class
  — same root as the swallowed-commit failure mode in "MANDATORY Post-Commit
  Verification") leaves that unstaged work ONLY in the patch cache, silently
  absent from the working tree. Canonical case: the user kept in-progress
  `BEST_PRACTICES*.md` drafts uncommitted; after a pipeline commit cycle they
  vanished and were recovered with `git apply` of the newest patch. **Rule:** when
  uncommitted *unstaged* work coexists with pipeline commits (a collaborator's
  drafts, scratch edits you promised to preserve), do NOT assume `git checkout -B`
  / commit kept them — verify with `git status` after the commit. If they're gone,
  they are almost certainly in `~/.cache/pre-commit/`: `grep -rl '<distinctive
  text>' ~/.cache/pre-commit/` to find the newest patch, then
  `git apply ~/.cache/pre-commit/patch<newest>` to restore. Prefer staging or
  stashing such work yourself before a commit so it never enters this window.

## Process canonicalizations from v1.0.5-1 retro arc (production-incident drain)

Two rules from the v1.0.5-1 drain (PRs #1789/#1790/#1792/#1793), which started
from a live djust.org `/insights/` production incident and drained four open
bugs.

- **Reproduce a production incident LOCALLY before changing infra or theorizing
  (#1789 / #1785).** The `/insights/`-reload incident burned three wrong
  theories — OOM (bumped the pod memory limit), multi-pod state loss (scaled to
  1 replica), and the template's variable-length DOM — before a local
  WebSocket reproduction settled it frame-by-frame (mount `v1` → `set_period`
  `html_update` `v1` → client version-mismatch → `request_html` → `_recovery_html`
  None → reload). The bug was reproducible on a **single local process the whole
  time**; every infra experiment was wasted motion because the trigger was
  framework code, not deployment. **Rule:** for a production incident, stand up
  the smallest faithful local reproduction (for WS/VDOM bugs, a
  `WebsocketCommunicator` capturing the actual frames + versions) BEFORE editing
  k8s resources, replica counts, or proposing an architecture theory. Each
  "maybe it's X" that costs an infra change or a deploy must first survive "does
  the local repro show X?" This is the deploy-axis companion to the existing
  Bug-report triage + Reproduction-fidelity rules: distrust not just the cited
  path but the cited *environment cause* until the local repro reproduces it.
  Empirically: the memory-bump and scale-to-1 experiments both failed to fix it
  (the user confirmed "still failing"), which is exactly the signal that the
  cause is single-process/framework, not infra.

- **Worktree-subagent drain pattern with symptom-up briefs (#1790/#1792/#1793).**
  The three follow-on drain bugs were each implemented by a `general-purpose`
  subagent in its own `git worktree` (isolation: `worktree`), given a
  prescriptive brief (root cause + the exact reference pattern to lift +
  reproduce-first + gate-off + two-commit shape + verification steps). Every one
  caught a real error the brief got wrong, precisely because the brief told them
  to trace symptom-up rather than trust it: #1787 found the real scaffolder is
  `scaffolding/templates.py` (not the cited deprecated `cli.py`) and that there
  were TWO blocking errors (A014 **and** admin.E403); #1784 found the
  parallel-path twin (`render_full_template` AND `render_with_diff` both re-run
  the tag on GET — #1646); #1786 pinned the exact leak path
  (`_sync_state_to_rust` → `_apply_context_processors`, not the
  `get_state`/`_snapshot_assigns` paths). **Rule:** for a multi-issue drain,
  one worktree-isolated subagent per issue (parallel-safe per #180), each brief
  carrying (a) the reference impl to lift verbatim (#1077), (b) an explicit
  "verify the cited path/environment symptom-up" instruction, and (c) the
  gate-off self-test (#1468). Review every resulting PR (CI + diff) before
  merge — do not rubber-stamp. Caveat surfaced: the native pre-push hook
  hardcodes `.venv/bin/python` and fails inside a worktree, so subagents push
  `--no-verify` after running gates manually; CI is the authoritative gate.
  Tracked at #1796.

## Process canonicalizations from v1.0.5-2 retro arc (render-path + cleanup drain)

Two rules from the v1.0.5-2 drain (PRs #1797, #1798, #1799, #1800, #1804 —
the render-path + cleanup bucket that, with v1.0.5-1, shipped in 1.0.5rc1–rc4).

- **A read-only review subagent must NEVER mutate the main checkout —
  especially `git config core.bare` (#1804 retro).** A Code Review subagent
  that needs to *run* code (exercise the compiled PyO3 Rust extension,
  reproduce a fix, gate-off-verify) must do so in its own `git worktree`
  (`isolation: worktree`) or not at all — a pure-inspection review uses
  `gh pr diff` and touches nothing. PR #1804's reviewer set
  `git config core.bare true` on the **main** checkout to repoint PyO3 at a
  built artifact; that broke the parent session's working tree —
  `git checkout` / `git status` failed with *"this operation must be run in a
  work tree"* — until recovered with `git config core.bare false`. The review
  verdict itself was sound (APPROVE, gate-off empirically validated), but the
  side effect was a repo-corruption incident the orchestrator had to clean up
  mid-drain. This generalizes the **Worktree-restore reflex (#36)** from
  *working-tree dirtiness* (reverted/staged files) to *git-config mutation*
  (`core.bare`, `core.worktree`, `core.hooksPath`). **Rules:**
  1. Give a review subagent `isolation: worktree` whenever it must build/run;
     never let it `git config`/`git checkout` the main checkout.
  2. Default reviews to read-only `gh pr diff` (the #1806 reviewer did exactly
     this — *"read-only review via `gh pr diff` … avoiding the #1804 core.bare
     incident"* — so the canon was already self-applied one PR later).
  3. After ANY subagent that could touch git config, verify
     `git config core.bare` returns `false`/empty as a reflex (the orchestrator
     ran this check at the top of every subsequent merge in the drain).

- **`{% extends %}` first-paint regressions are a silent-catch + parallel-path
  double-bug — de-silence AND unify (#1801 / PR #1804).** The
  template-inheritance head-loss (#1801) was two known classes stacked: a broad
  `except Exception` in `get_template()` swallowed a real
  `resolve_template_inheritance` *"Template not found"* (logged only at DEBUG,
  set `_full_template=None`, fell through to a `dj-root`-fragment render with no
  `<head>`), and the underlying *"Template not found"* came from the dir-collection
  hardcoding `BACKEND == django.template.backends.django.DjangoTemplates` —
  dropping app-template dirs for projects on djust's own `DjustTemplateBackend`
  + `APP_DIRS=True` (the `djust new` scaffold's config). The fix de-silenced the
  catch (now WARNING, scoping only the resolution call) AND unified all **three**
  parallel dir-collection paths through one `get_template_dirs()` +
  `_APP_DIRS_TEMPLATE_BACKENDS` set (parallel-path-drift, #1646). Reinforces both
  the existing **Reproduction fidelity / "are we sure it isn't a silent catch?"**
  triage instinct and **#1646** — and #1646 recurred *four* times this release
  cycle (#1784 render twin, #1801 three collectors, #1791 `cli.py` startproject
  twin, #1805 `is_dir` parity), so treat "grep every parallel implementation of
  the invariant" as the default Stage-4 reflex for any render-path change.

## Process canonicalizations from v1.0.5-4 + v1.0.5-5 retro arc (DX drain + sticky-recovery P0)

One rule from the final 1.0.5 drains (v1.0.5-4: PRs #1811/#1812; v1.0.5-5:
PRs #1814/#1815). The other findings reinforced existing canon (the #1813
structural cure reinforced #1646; the #1810 empirical mechanism-bisection
reinforced #1529/#1516; the #300 `core.bare` review discipline held across
all four PRs) — see RETRO.md Insights. The new rule:

- **A concurrency test asserts a logical ORDERING invariant, never a
  wall-clock duration/ratio (#1795 / PR #1815, a two-release flaky
  recurrence).** A test that proves concurrency by asserting on wall-clock
  durations or ratios — `elapsed < 100ms`, `parallel < serial/2` — is
  fundamentally flaky under CPU saturation: when the concurrent work can't
  get dedicated cores (full `make test -n auto`), the speedup degrades and
  the ratio drifts past any fixed threshold. `test_total_wall_clock_is_max_not_sum`
  was "fixed" once (PR #1797: absolute→relative ratio) and STILL false-failed
  TWO releases later (parallel=88.1ms vs serial/2=85.8ms at the 1.0.5rc5 cut;
  passed 3/3 in isolation). The durable fix replaces the timing threshold with
  a deterministic logical property — **interval overlap / event ordering**:
  each unit records its `[start, end]`; a concurrent run satisfies
  `max(start) < min(end)` (every unit starts before any finishes), which a
  serial loop can NEVER satisfy. Event ordering is immune to saturation jitter
  (scheduling N coroutines is microseconds, far under the work duration), so
  the assertion is load-independent. Pair it with an in-suite **gate-off
  sibling** that runs the same units SERIALLY and asserts they do NOT overlap
  — proving the assertion distinguishes parallel from serial (non-tautological
  by construction, per #1200/#1468). **Rule:** never assert a duration/ratio to
  prove concurrency; assert an ordering invariant. Canonical case:
  `tests/integration/test_chunks_overlap.py::TestParallelRender` (PR #1815).
  Generalizes the v1.0.5-2 lesson that the prior #1795 fix treated the symptom
  (absolute→relative) rather than the class (timing assertions are flaky under
  saturation).

## Process canonicalizations from v1.0.6-1 + v1.0.6-2 retro arc (wire-version + security drain)

Two rules from the first 1.0.6 drains (v1.0.6-1: PR #1816 / #1788; v1.0.6-2:
PRs #1823/#1824/#1825 + the rc1-cut benchmark fix). Other findings reinforced
existing canon (the #1788 single-`_next_version()` helper + the implementer
catching 3 send sites the design missed reinforced #1646/#294; the #1820 audit
declining to add `@strict_types` reinforced #1079) — see RETRO.md Insights.

- **A security validation/sanitization fix MUST have its review empirically
  probe encoding-bypass variants — the downstream consumer often DECODES after
  validation (#1819 / PR #1825 review).** PR #1825's first pass validated the
  mount URL by checking for a literal `..` segment in `urlparse(url).path` —
  but `RequestFactory.get()` percent-DECODES the path *after* validation, so
  `/%2e%2e/admin/` sailed past the check and landed in `request.path` as
  `/../admin/`. The fix's own CHANGELOG/SECURITY_AUDIT claimed traversal was
  blocked; it was false for any `%2e%2e`/`%2f`/`%5c` payload. The adversarial
  Code Review caught it ONLY because the brief explicitly told it to feed
  encoded variants through the helper AND the downstream sink (`RequestFactory`)
  and check the *final* `request.path` — an inspection-only review would have
  rubber-stamped the literal-`..` check. **Rule:** when reviewing any input-
  validation / sanitization / escaping fix, the review's empirical probe must
  include the ENCODED and ALTERNATE-REPRESENTATION forms of the attack
  (percent-encoding `%2e`/`%2f`/`%5c`, double-encoding, alternate separators,
  case variants, unicode look-alikes) fed end-to-end through the real downstream
  consumer — because validation that runs *before* a decode/normalize step is
  defeated by the encoded form. Fix shape: decode/normalize to the same
  canonical form the sink uses (here, `unquote` once) BEFORE the check. Same
  family as the empirical-canary rule (#1459) but for the security-validation
  subclass: the canary must be the encoded bypass, not just the literal attack.

- **A latency-SLA benchmark asserts on MEDIAN, not the outlier-sensitive mean
  (#1795 family, v1.0.6rc1 cut).** `tests/benchmarks/conftest.py`'s
  `_assert_benchmark_under` asserted `benchmark.stats["mean"] < target`. The
  mean is dragged past the SLA by a handful of GC / scheduling-pause outliers
  (a single ~34ms spike among thousands of ~4ms rounds), so the serial pre-push
  false-failed two VDOM-diff benchmarks on a loaded machine while median/min
  (~3.8ms) were comfortably under the 5ms target — and the VDOM path was
  untouched since the last green release, confirming non-regression. Fixed to
  assert the **median** (`tests/benchmarks/conftest.py`, commit `49893831`):
  the median reflects the actual per-call cost and is immune to those outliers,
  the right statistic for a latency SLA. This is the same outlier-sensitivity
  class as the v1.0.5-4/-5 concurrency-test rule above — generalize both as:
  **never assert a pass/fail gate on an outlier-sensitive statistic (mean,
  raw wall-clock) when a robust one (median, event-ordering) measures the same
  property.** Caveat surfaced: the threshold is *skipped under `-n auto`* (xdist
  disables `benchmark.stats`), so `make test` and CI never enforce it — it only
  bites the local serial pre-push, which is why a fragile mean-threshold could
  pass one release by luck and fail the next.

## Process canonicalizations from v1.0.7-1 retro arc (post-1.0.6 drain)

One rule from the v1.0.7-1 open-issue drain (PRs #1838, #1839; #1827 closed-no-code). The other findings reinforced existing canon — #1817's structural `_next_version_armed` helper + the `test_arm_recovery_is_the_only_arming_mechanism` single-source-of-truth pin reinforced #1646/#1125; #1827's reproduce-against-the-real-render-path close reinforced the Bug-report-triage mechanism axis (#1650/#1638); the actor-path "recommend a follow-up" that wasn't filed (caught by the retro gate) reinforced the retro classification gate itself. See RETRO.md v1.0.7-1.

- **The flaky-timing rule covers real-frame `requestAnimationFrame`; the remedy is a controllable async-primitive stub driven explicitly, asserting an ordering invariant (#1830 / PR #1839).** The "never assert a pass/fail gate on an outlier-sensitive statistic (mean, raw wall-clock)" rule above generalizes to *any* assertion that races a real timer/scheduler — including a test that relies on a `setTimeout(0)` microtask flush winning against a real rAF (jsdom backs `requestAnimationFrame` with a ~16 ms timer). `tests/js/dj_transition.test.js`'s "active/end on next frame" case flaked under parallel load because the real rAF fired before the start-class assertion. **Remedy:** replace the real async primitive with a **controllable stub the test drives explicitly** — for rAF, an opt-in queue flushed via a `flushFrame()` handle (see the `controlledRaf` option in that test's `createDom`), so phase transitions advance only when the test drives them. The test then asserts the **ordering invariant** (state-before-frame ≠ state-after-driven-frame), is fully synchronous, and no scheduler jitter can flake it. Pair with a gate-off (neuter the drive → the post-frame assertions must fail) to keep it non-tautological (#1468). Same family as the "Async-callback test stubs MUST yield a microtask" rule (PR #1113) — both say: own the async primitive in the test; never depend on the real one's wall-clock timing.

## Process canonicalizations from v1.0.7-3 + v1.0.7-4 retro arc (security audit drain + coordinated disclosure)

Three rules from the security-audit drain (private PRs #165–#177 → djust 1.0.7 GA on PyPI + 13 published GHSAs, 2026-06-22). The dominant finding — *the whole audit was parallel-path drift cured by shared chokepoints + the WU1 anti-drift net (PR #172)* — reinforces #1646/#1459 and is already canon; the new rules are the gaps that drift surfaced AFTER the unit suite went green.

- **Framework-upgrade validation MUST browser-test downstream interactive paths, not just run their pytest suites (#1849).** Two real runtime breaks shipped in the 1.0.7 upgrade that the 8237-passing suite + HTTP-200 smoke both PASSED over, caught only by driving the live page in a browser: (1) the demo's `dj-view="demo_app.views_old.IndexView"` — a stale ref that rode the boundary-less `startswith` the F22 fix tightened, now refused at WS mount; (2) djust.org's examples-page tab/copy handler — an inline `<script>` inside the dj-root that the #1610 WS-mount morph re-creates without executing, so the delegated listener never registered (silent, no console error; see #1848). Both are runtime/wiring breaks (mount-path allowlist; inline-script execution under morph) that unit tests structurally cannot see. **Rule:** on any framework version bump in a downstream app, after the unit suite passes, BROWSER-test the key interactive paths against the running app — assert the LiveView mounts (no mount-refusal), click the primary controls (tabs/copy/nav), assert the expected DOM change + no console error. **Diagnosis recipe for "click does nothing, no error":** add a capture-phase AND a late bubble-phase `document` click listener, dispatch a real click — if BOTH fire but the page handler didn't act, the page's own listener was never registered (e.g. its `<script>` is inside a morph-managed region). Tracked: #1849.

- **Page JS belongs OUTSIDE the dj-root — morphdom does not execute inserted `<script>` (#1848).** base.html-style layouts wrap page content in `<div dj-root>`; on the #1610 mount morph, morphdom re-creates that subtree and never executes inline `<script>` it inserts, so handlers defined inside the content block silently never register. Put page JS in a base-template block rendered AFTER the dj-root `</div>` (djust.org uses `{% block extra_scripts %}`); a delegated `document` listener still catches clicks on the morph-managed elements. Likely a 1.0.7 framework regression vs the `live_redirect` path which already re-executes classic scripts (#1635/#1650) — tracked at #1848 (re-execute classic `<script>` on the mount morph, or a system-check warning). Until fixed: keep page JS out of dj-root.

- **GHSA publish needs a version-range pre-flight; coordinated disclosure gates GHSA-publish on PyPI-live; CI-dark merges ride local validation.** Three pre-existing GHSA drafts carried `vulnerable: <= 1.0.7rc1` / `patched_versions: None` — publishing as-is gives Dependabot NO upgrade target (no alert fires). **Rule:** before any `state=published`, read-only-verify every draft's `vulnerable_version_range`/`patched_versions` are correct + present (normalize to `< X` / `X`). The disclosure sequence that produced no bad window: private staging → release to public + PyPI → **confirm PyPI X is live BEFORE publishing any GHSA** → publish all together (Dependabot then points at a real installable fix). With Actions exhausted for most of the drain, PRs merged on local validation (full suite + worktree-isolated adversarial review + gate-off + two-commit), later confirmed by the green release CI. Process slip to avoid: a push to a branch-protected `main` can land via admin-BYPASS of the PR rule — don't discover that via a "diagnostic" push; use a PR or explicitly intend the bypass. Runbook: `scratch/sec-audit/GHSA-TRACKING.md`.

## Process canonicalizations from v1.0.8-1 retro arc (security-drift prevention program)

Three rules from the v1.0.8-1 drain (PRs #1859, #1860, #1861, #1863, #1864, #1866, #1867 — the post-disclosure prevention program built on the security-audit findings: Tier-1 convergence, Tier-2 detection nets/gates, Tier-3 codified defaults). The ironic through-line: an *anti-drift* milestone twice shipped non-load-bearing anti-drift artifacts, and a *secure-defaults* doc mis-stated the default — each caught only by adversarial gate-off / citation-discipline review, never by the green suite.

- **An anti-drift test or pin is decorative unless it is load-bearing — distinct seams, or wired into the production control path it claims to pin (#1859 / #1860).** The prevention milestone itself shipped two artifacts that *looked* like protection but protected nothing, both caught only by the adversarial gate-off (#1468):
  1. PR #1859's three `test_all_transports_agree_*` methods compared a *single already-converged chokepoint's* output to itself — the auth / object-perm / rate-limit controls had already converged onto one function, so the per-transport adapters were `distinct=1` (no seam to differentiate). A self-comparison can never go red. Removed in the fix-pass. The real protection on those axes is the **call-site pin** (Concern 4 at `python/djust/tests/test_mount_chokepoint_structural.py:431`) plus the gate-off-proven deny/allow rows — NOT the `agree` test. A transport-parity `agree` test is meaningful ONLY when the adapters point at genuinely distinct seams (in `python/djust/tests/test_transport_parity_security.py`: origin = `distinct=3`, mount-traversal = `distinct=2`).
  2. PR #1860's `RUNTIME_OWNED_VERBS` set was a coincidental test-pin that `receive()` never consulted — if routing drifted from the set, no test would have noticed. Made load-bearing by routing on membership (`python/djust/websocket.py:1933` — `if msg_type in self.RUNTIME_OWNED_VERBS`), so the contract test now couples the set ↔ the actual routing.

  **Rule (generalizes #1468 to the test/pin-DESIGN axis):** before shipping any parity test, pin, or count-canary, ask "would this go red if the thing it pins actually drifted?" If the adapters are `distinct=1`, or the pinned constant isn't membership-checked in the production path, the answer is no — it is decorative. A "pin" in an anti-drift PR that isn't mechanical is the sharpest version of the tautology class (#1200/#1468).

- **A convergence plan's "these two paths are the same" is a hypothesis the implementer must falsify before merging — converge the shared SEQUENCE, not the whole handler (#1860 / #1861).** The prevention plan asserted the event path was "fully converged" and the WS mount path was a thin shim away from `ViewRuntime.dispatch_mount`. Stage-4 Explore confirmed the *leaf* chokepoints — but the implementer's symptom-up trace found `runtime.dispatch_*` is NOT a superset of the WS handlers (~16 WS-only behaviors: binary upload, presence, cursor, time-travel, sticky-child preservation, signed-snapshot restore, actor channel-layer). T1-B (#1860) correctly narrowed to routing only `url_change` (a genuine runtime-owned verb). T1-A (#1861) re-scoped from "make `handle_mount` a thin shim" to "extract the genuinely-shared pre-mount auth+tenant SEQUENCE into one `run_pre_mount_auth` helper (`python/djust/auth/core.py:396`) that all three live mount paths call" — convergence (the #1646 cure: retire the class, not 2-of-3) WITHOUT merging the fat WS-only bodies. Both re-scopes were right and each closed a latent bug the point-fix would have missed (#1861 a runtime/SSE auth fail-OPEN; #1863 an IDOR on the HTTP-API + SSE-legacy object-perm twins). **Rule:** a refactor/convergence plan must, at Stage 5, list what is genuinely UNIQUE to each path before merging; if the unique set is non-trivial, converge the shared sequence into a helper every path calls, never collapse the handlers. An Explore-confirmed premise about leaf controls does NOT license a premise about orchestration shape.

- **Documenting a secure default falsification-tests it — a canon/doc "X always holds" claim must be run against the code, not just have its citation verified (#1867).** Writing `docs/SECURE_DEFAULTS.md` Pattern-1 ("the `_ALWAYS_EXCLUDED_FIELDS` serialization floor is unconditional") forced a read of `python/djust/serialization.py:362` (`_field_is_serializable`) and revealed the claim is FALSE: the per-model `allowed` allowlist is checked BEFORE the floor denylist, so `djust_serializable_fields=['password']` re-exposes a floor field. The #1197 citation-discipline review caught it — and notably, all 23 *other* citations in the doc were exact; only the *claim about what the cited code does* was wrong. The fix reworded to the accurate precedence (identity → allowlist-wins → floor-only-when-no-allowlist) + a WARNING, and the act of documenting surfaced a genuine secure-by-default question (#1868: should the floor be unconditional regardless of allowlist?). **Rule (extends #1516 active-falsification + #1197 citation-discipline to PROSE INVARIANTS):** any canon/doc statement of the form "this always / never happens" must be falsification-tested by constructing the case that would disprove it and running it — verifying the file:line citation is necessary but NOT sufficient, because the citation can be exact while the invariant it asserts is false.

The remaining v1.0.8-1 findings reinforced existing canon rather than adding new rules — the tooling-PR dual validation (empirical canary #1459 + broad dogfood #1060 drove S011 to 0 false-positives in PR #1864), new-CI-gate discipline (bandit blocks only after a verified 0-HIGH baseline; the browser-smoke ships non-gating per #1534 until runner-green, promotion tracked in #1869; the #1236 governance gate fired correctly on the release-workflow change), and the #1646 parallel-path cure applied 3× — see RETRO.md v1.0.8-1 Insights.

## Process canonicalizations from v1.1.0-3 retro arc (ViewRuntime convergence — ADR-022)

Six rules from the v1.1.0-3 ViewRuntime dispatch convergence — the ~18-PR arc (Iter 0 #1886 → Iter 1 SSE #1888 → Iter 2 WS-event #1890/#1893/#1895/#1897/#1909 → Iter 3 WS-mount #1912/#1914/#1916/#1918/#1920) that collapsed WebSocket + SSE + runtime onto ONE `ViewRuntime` dispatch spine for both mount and event, structurally retiring the #1646 parallel-path-drift class (the #1 recurring failure of the v1.0.x arc). These generalize the per-PR findings into the durable convergence playbook.

- **The #1646 convergence dividend — merging paths surfaces the drift the fork hid; budget for it (#1898 + 5 more).** Every flip in the arc exposed a latent bug that had silently diverged between the parallel paths and that NO inspection had found: `component_id`-over-WS returned an error frame (the parent was never re-rendered, #1898), `live_redirect` from a state-unchanging handler dropped its navigation frame, async results lacked `source="async"`, time-travel didn't record on permission-denial, object-perm denial left the WS socket open on the runtime path, and `mount_batch` leaked a failed view's error into survivors' collectors. They surfaced ONLY when the second path was deleted and the survivor had to become a true superset (or when a characterization test repointed at the real path, per #1638). **Rule:** when planning a convergence, budget explicitly for "the flip will surface latent old-path bugs" — they are findings, not regressions; the characterization net (next rule) is what catches them, and each is a parity-restore worth its own CHANGELOG line. A convergence pays down existing drift, not just prevents future drift.

- **Dormant-define → wire → flip: the safe shape for an all-or-nothing-verb convergence.** A verb whose routing is gated by `RUNTIME_OWNED_VERBS` membership (`websocket.py` `receive()`) CANNOT be partially flipped — adding it routes *all* of that verb's traffic at once. So the only safe sequencing (proven identically for the event flip and the mount flip) is three movements: **(1)** grow the target path (`dispatch_event`/`dispatch_mount`) to a functional superset over zero-routing-risk PRs + DEFINE the transport hooks DORMANT (declared, default no-op, NOT yet called — structural pins assert `dispatch_*` doesn't call them); **(2)** WIRE the hooks into the target path + prove it's a superset by running the full real-transport suite against it via a DIRECT-CALL shim (a real `WSConsumerTransport`, NOT by flipping `RUNTIME_OWNED_VERBS`) — Phase 3.3a's 9/9 proof; **(3)** the atomic FLIP: add the verb to `RUNTIME_OWNED_VERBS` + reduce the bespoke handler to a thin shim + delete its body. Each build-up PR is inline-reviewable; only the final flip is high-blast-radius. This generalizes the split-foundation rule (#1122) to the routing-verb case.

- **Characterization-tests-first against the OLD path are the parity proof for a flip (#1897/#1911).** Before flipping, write real-`WebsocketCommunicator` tests against the *bespoke* path: they pass NOW, and the flip's contract is that they stay green against the *new* path (PR #1897 for event, #1912's gap-tests for mount). That green-in-both-states equivalence IS the parity proof — and it doubles as the discovery mechanism for the convergence dividend above (the bespoke `component_id` error frame was found writing the characterization test). Land the net BEFORE the fold, as its own PR. Each characterization test carries a gate-off sibling (#1468) so it's non-vacuous.

- **Stage-4 read-only scoping before each flip caught 3 #560-class landmines — scope-before-coding on high-blast-radius routing changes pays for itself.** A read-only architect pass before each flip (the "first-principles grep before architecting" canon, #168/#1143) found, every time, a silent-failure landmine that naive coding would have shipped: (a) the runtime's `_render_lock` was DEAD CODE — a runtime-local lock cannot serialize against the WS-only tick loop, so "wiring it up" ships the #560 version-interleave bug; the fix is to BORROW the consumer's real lock via `transport.event_context()`. (b) the actor-mount/event axis-misalignment — the verb-flip is all-or-nothing but actor-vs-not is *view-state*-gated, so a flipped actor view hits a runtime path with no actor branch → needs a `transport.dispatch_actor_*` hook. (c) the stale `runtime.view_instance` idempotency collision — `dispatch_mount` early-returns if `view_instance is not None`, but the consumer nulls only `self.view_instance` on teardown, so a post-flip `live_redirect` re-mount silently no-ops → reset `runtime.view_instance=None` in the shim + all teardown sites. **Rule:** for any routing-flip / high-blast-radius convergence, run a read-only Stage-4 scope FIRST and cite the file:line of each landmine; the scope is cheap and each landmine it surfaces is a silent production bug avoided.

- **Routing-flip PRs MUST run the FULL CI-way suite (all test roots), not the worktree subset (#1391/#1399 subclass).** The event flip's first pass shipped a RED suite because the implementer's `python/djust/tests` subset run missed two now-stale tests in `tests/unit` + `tests/integration` (an orphaned `_handle_event_inner` getattr + a `handle_event`-direct caller needing `scope`). CI + the adversarial review caught both. Every subsequent flip ran `pytest tests/ python/tests/ python/djust/tests/ -n auto`. **Rule:** a PR that deletes a symbol or flips routing must run the suite across EVERY test root (the #1391 symbol-removal-grep must also cover `tests/unit` + `tests/integration` + `tests/benchmarks`), because the worktree-subagent's convenient subset is exactly where the orphan hides.

- **Two-gate treatment for routing-flip PRs; proportionate inline review for the dormant build-up folds.** Each atomic flip (event 2.3b #1909, mount 3.3b #1920) got a MANDATORY worktree-isolated adversarial review (independent gate-off + the full suite via the runtime path + a per-behavior delta scan) IN ADDITION to CI — and it earned its keep (caught the 2 stale tests on the event flip; 8/8 empirical confirm on the mount flip). The dormant build-up folds (which leave routing + the bespoke handler untouched, blast radius SSE+runtime only) got proportionate inline review + CI. **Rule:** match review depth to blast radius — the routing flip and any security-boundary change get the independent worktree-adversarial pass with empirical gate-off (#1825); a dormant/additive build-up fold with the bespoke path untouched gets careful inline review. This let the ~18-PR arc move fast without rubber-stamping the dangerous PRs.

## Process canonicalizations from the v1.1 type-enforcement arc (ADR-023)

One rule from the ADR-023 strict-mypy ratchet (PRs #1936–#1960). The arc's other lessons reinforced existing canon or are captured in RETRO.md ("v1.1 type-enforcement arc"): the type-ratchet dividend (enforcing dead config found ~8 latent bugs) is canonized in the v1.1.0-4 rule 1; the "intractable boundary deserves a second look with the right tooling" lesson (rust_handlers flipped clean in M4g with a `_safe()` cast wrapper, 0 ignores) is closed by #1959.

- **A green CI lint/type gate can be RED in a full dev environment — verify newly-enforced gates with all deps installed, not just on CI (#1960).** After the ratchet, `mypy python/djust` passed CI but failed a full local dev env with 3 `import-untyped` errors. Root cause: `import-untyped` (a third-party lib INSTALLED without type stubs — requests/yaml in a full dev env) is a DIFFERENT error code than `import-not-found` (the lib ABSENT, which CI's minimal env hit and `ignore_missing_imports` suppressed), and it is reported against the IMPORTING module (the strict islands `permissions`/`deploy_cli`/`mcp.server`), so a per-lib `[[tool.mypy.overrides]]` on the lib cannot reach it. A contributor running `mypy`/pre-commit locally would have hit the 3 errors a green CI hid. **Rule:** when you promote a lint/type checker to an enforced gate, run it once in a FULL dev environment (every optional/dev dependency installed) before declaring it green — CI-green ≠ contributor-green, because CI's minimal dependency set suppresses a different error class than a full install surfaces. Fix shape for this specific case: `disable_error_code = ["import-untyped"]` in the global `[tool.mypy]` (not a per-module override). Same family as the empirical-canary rule (#1459): exercise the gate against the environment it will actually run in.

## Process canonicalizations from v1.1.0-4 retro arc (post-convergence open-issue drain)

Two rules from the v1.1.0-4 drain (PRs #1961–#1965 — the open-issue drain of follow-ups surfaced by the ADR-022 convergence + ADR-023 type-ratchet arcs). The other findings reinforced existing canon — #1940's identity-guard-after-await reinforced #245/#1198 (TOCTOU commit-or-rollback, extended here to a DETACHED-task view capture: `_run_async_work` captured `view = self.view_instance` before its await, and a disconnect/re-mount reassigned `self.view_instance` mid-await → stale-view write; cure is the same identity-guard `if self.view_instance is not view: return` on BOTH the success and error paths, since `sync_to_async` thread-pool work can't be cancelled); #1938's "ship a detector, not a phantom fix, for an external/unpinnable root cause" reinforced the reproduce-locally / distrust-the-cited-cause triage rules; #1961's poll-the-REAL-canary-route (not `/`, a different LiveView) reinforced reproduction fidelity. See RETRO.md v1.1.0-4.

- **Test the BARE base class when it declares/documents a method, and init instance attrs in `__init__` not a property setter (#1947/#1952 — the type-ratchet dividend).** Two latent AttributeError-on-real-paths crashes shipped green for releases because the suite only ever exercised subclasses that filled the gap: #1947 — `LiveComponent` *documented* `update(**props)` but never implemented it (only `Component` did); every test subclass defined its own `update()`, so a bare `LiveComponent` routed through `ComponentMixin.update_component` was never run. #1952 — `TutorialMixin` initialized four `_tutorial_*` signal attrs only inside the `tutorial_total_steps` *setter*; a view that read them before/without the setter hit AttributeError, but every test set the property first. Both were surfaced by the ADR-023 strict-typing ratchet (mypy `[attr-defined]`), NOT the 8600-green suite — the **type-ratchet dividend**: strict typing's payoff is the latent bugs the checker forces you to confront, not the annotations themselves (the ratchet found ~8 real bugs across M1–M4g). **Rule:** (a) for any base class that *declares or documents* a method/attribute its subclasses are expected to use, add a test that exercises the BARE base class (no override) on that path; (b) initialize instance attrs in `__init__`, never only in a property setter — a setter-only init is an AttributeError waiting for the first read-before-set. Generalizes #1104 (N similar sites need N tests) to the base-vs-subclass axis.

- **Cap concurrent worktree implementer agents at ~3 — 5 trips a transient server-side rate-limit (#1961–#1965 drain orchestration).** Launching all 5 drain fixers as concurrent worktree-isolated agents tripped a server-side API throttle ("temporarily limiting requests — not your usage limit") that stalled 3 of them mid-task. Recovery was clean (worktrees persist across a rate-limit rest, so each agent resumes mid-task via a follow-up message with no work lost) — but the right prevention is to cap concurrency. **Rule:** when fanning out worktree-isolated implementer agents for a drain/batch, launch at most ~3 concurrently; queue the rest and resume as slots free (serial resumption of a throttled agent is safe — worktree state persists). The concurrency-ceiling companion to the one-checkout=one-agent rule (#180/#1172); the OUT-OF-REPO propagation of this cap into the `pipeline-run`/`pipeline-drain` skill prompts is tracked in the v1.1.0-4 Action Tracker row.

## Additional Documentation

- `docs/SECURE_DEFAULTS.md` — secure-by-default pattern catalog (denylist serialization, HMAC signed snapshots, fail-closed precedence gate, `safe_setattr`) + how to make a new feature secure-by-default
- `docs/SECURITY_GUIDELINES.md` — `djust.security` utilities, banned patterns, contributor security checklist
- `docs/PULL_REQUEST_CHECKLIST.md` — PR review checklist
- `CONTRIBUTING.md` — contribution guidelines
- `QUICKSTART.md` — quick setup guide
- `docs/STATE_MANAGEMENT_API.md` — decorator API reference
- `docs/website/guides/loading-states.md` — loading states & background work guide
- `DEVELOPMENT_PROCESS.md` — 9-step development process
