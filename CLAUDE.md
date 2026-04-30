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

## Additional Documentation

- `docs/PULL_REQUEST_CHECKLIST.md` — PR review checklist
- `CONTRIBUTING.md` — contribution guidelines
- `QUICKSTART.md` — quick setup guide
- `docs/STATE_MANAGEMENT_API.md` — decorator API reference
- `docs/website/guides/loading-states.md` — loading states & background work guide
- `DEVELOPMENT_PROCESS.md` — 9-step development process
