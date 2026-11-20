# CLAUDE.md

This file provides guidance to Claude Code when working with the djust framework.

## Application conventions

For application code, examples and scaffolding, start with
[Application engineering conventions](docs/ai/conventions.md) and load the
relevant [AI reference sections](docs/ai/README.md).

## Project Overview

djust is a hybrid Python/Rust framework bringing Phoenix LiveView-style reactive server-side rendering to Django. Rust handles performance-critical operations (template rendering, VDOM diffing, HTML parsing) via PyO3; Python provides the developer-facing API.

## Build & Test Commands

```bash
make install          # Full install (Python + Rust build)
make install-quick    # Python-only install (skip Rust rebuild)
make build            # Build Rust extensions (release)
make dev-build        # Build Rust extensions (dev, faster)

make test-selected    # Affected Python checks; conservative full fallback
make test-integration # Full Python + JavaScript + Rust integration gate
make test             # Same full integration gate (unchanged coverage)
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
bash scripts/run-with-venv-python.sh -m pytest tests/unit/test_select_tests.py -q
make test-selected FROM=origin/main TO=HEAD
# Full Python coverage includes ALL three roots:
bash scripts/run-with-venv-python.sh -m pytest tests/ python/tests/ python/djust/tests/
cargo test                            # All Rust tests
cargo test -p djust_vdom              # Single crate
```

## Project Structure

```
djust/
├── python/djust/           # Python package
│   ├── live_view.py        # LiveView base class
│   ├── components/         # LiveComponent base (components/base.py) + ui/
│   ├── forms.py            # FormMixin (real-time validation)
│   ├── websocket.py        # LiveViewConsumer (Channels)
│   ├── auth/               # Authentication & authorization (core.py: check_view_auth, mixins.py)
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
│   └── static/djust/       # Client JS — shipped client.min.js.gz is ~87 KB
├── crates/
│   ├── djust_live/         # PyO3 bindings — the entry point
│   │                       #   (pyproject `manifest-path`; module `djust._rust`)
│   ├── djust_components/   # Rust-backed components
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
- Client JS size budget. Every figure below is generated into
  `python/djust/static/djust/client-sizes.json` by `scripts/build-client.sh`
  and enforced by `scripts/check-doc-snippets.py` against the artifact each
  line names — run `make sizes` to print the current values (#2138).
  - **Shipped**: `client.min.js.gz` is **~87 KB** gz — what a user downloads,
    and the only figure that constrains anything.
  - **Build input**: unminified `client.js` is ~284 KB gz <!-- size-claim: unminified -->
    across 61 modules in `static/djust/src/`. Not a deliverable; do not quote
    it as "the client size".
  - Until #2138 these read ~87 KB gz / 388 KB raw / 35 modules <!-- size-claim: historical -->
    and had drifted more than 2x, because only the README pair was checked.

  A marker must be on the SAME LINE as the number it governs — the check is
  per-line, and a marker one line away governs nothing. That is how the first
  version of this block shipped a decorative marker.
When adding a feature, measure its gzipped delta — aim under 2 KB gzipped per new module. Top 3 modules (`12-vdom-patch.js`, `09-event-binding.js`, `03-websocket.js`) are 42% of the budget; reducing them requires structural care. No new dependencies without discussion.
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
- **Run focused checks while editing**, then let the scoped pre-push hooks run. Do not redundantly run `make test` before every incremental push.
- **No whole-suite runs on a laptop.** CI is the full-suite evidence. Locally run the file/class/`-k` you changed or `make test-selected`; `tests/local_load_guard.py` caps xdist at 2 workers and refuses a run over 5,000 tests unless `DJUST_FULL_LOCAL=1` (the `make test*` targets set it). Time per PR/issue: `make flow-metrics` (see `docs/testing-strategy.md`, "Local load guard and flow metrics").
- **Pre-commit hooks** run automatically: ruff, ruff-format, bandit, detect-secrets
- **Pre-push hooks** select affected Python tests and Rust crates; unknown/shared changes fall back to full coverage. See [test strategy](docs/testing-strategy.md).
- **Review against** `docs/PULL_REQUEST_CHECKLIST.md` before marking PRs ready
- After completing a set of related changes, commit with a descriptive conventional commit message

## Testing Expectations

- All new code needs tests (unit and/or integration)
- New JS feature files in `static/djust/src/` need corresponding tests in `tests/js/`
- Bug fixes require regression tests
- Use [stage-specific checks](docs/testing-strategy.md): focused edit checks, selected pre-push checks, then `make test-integration` on the final integration commit. Full CI and release gates remain authoritative.
- Do not treat a selected pass as full-suite evidence. If any changed path is unclassified, dependencies/configuration change, or code has shared/transitive impact, run the full applicable checks.
- This staging policy supersedes older “full suite before every push” wording in local agent guides; a pipeline full-suite integration stage and release gates still apply.
- Tests must be deterministic — no flaky tests
- Test imports must match actual module paths (a common rejection reason)
- `feat:` and `fix:` PRs must add a changelog fragment: `changelog.d/<issue-or-slug>.<section>.md` (section ∈ added/changed/fixed/security/documentation/removed/deprecated; body = the bullet). Do NOT edit `CHANGELOG.md`'s `[Unreleased]` directly — pre-commit refuses it; the release cut compiles the fragments (`changelog.d/README.md`)
- System-check docs, check IDs and removed-name tables are generated (#4080). A new check: `make new-check-id FAMILY=V` for the ID, then `docs/checks/<ID>.md` (`docs/checks/README.md`). A removed name: `docs/removed/2.0/migration/<NNN>-<slug>.md` (`docs/removed/README.md`). Then `make generated-docs`. Never edit between `BEGIN GENERATED` / `END GENERATED` markers in `docs/system-checks.md`, `error-codes.md`, `migrating-to-2.0.md` or `API_STABILITY.md`; a prose edit there also needs `make generated-docs` (it refreshes the fingerprint on the last line). A retired ID keeps its file with `status: retired` and is never reused. A hand-edited copy of one of those docs (an older branch in a merge): `python scripts/import-generated-docs.py`, then `make generated-docs`

## Key Patterns

### LiveView
```python
from djust import LiveView, event_handler, state

class MyView(LiveView):
    template_name = 'my_template.html'
    count = state(0)

    @event_handler
    def increment(self, **kwargs):
        self.count += 1
```

### Event Handlers — always use `@event_handler`
```python
from djust.decorators import event_handler

@event_handler()
def search(self, value: str = "", **kwargs):
    """Use 'value' param for @input/@change events"""
    self.query = value
```

### What reaches the template (explicit exposure, the default since 2.0)
- A field declared with `state(...)` (`from djust import state`) renders in the template (as above); `state(..., render=False)` keeps one out. Persisting it (`persist="server"`) or sending it raw to the browser (`client=True`) are separate grants.
- Any other attribute set on `self` stays on the server, with or without an underscore: `self.total = 0` alone renders `{{ total }}` empty, with no error.
- `get_context_data()` supplies derived and render-only values, including `@computed` values and `assign_async()` results; build on `super().get_context_data(**kwargs)`, which carries the declared fields. Framework mixins supply their own (`form_errors`, `streams`, `uploads`).
- `_private` is a naming convention for internal state. `exposure_policy = "legacy"` restores the 1.x attribute walk and is deprecated. See `docs/website/state/explicit-exposure.md`.

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

## Common Pitfalls

- **Ruff F509**: `%`-format strings containing CSS semicolons trigger false positives. Separate HTML (`%s` substitution) from CSS (static string) and concatenate.
- **VDOM form values**: Ensure form field values are preserved during updates. See `VDOM_PATCHING_ISSUE.md`.
- **Pre-commit reformatting**: If commit fails due to ruff auto-format, re-stage and commit again.
- **Hot reload integration (v0.9.0+)**: djust auto-enables HVR from its
  own `DjustConfig.ready()` whenever `DEBUG=True` and `watchdog` is
  installed. Downstream consumers do NOT need to add
  `enable_hot_reload()` to their own `AppConfig.ready()`. Existing
  explicit calls keep working (idempotent). Opt out via
  `LIVEVIEW_CONFIG['hot_reload_auto_enable']: False`. A pytest process
  skips the auto-enable (`djust.apps._running_under_pytest`: `pytest` is
  imported, or `PYTEST_CURRENT_TEST` is set — the latter alone misses
  pytest-django's `django.setup()`, #3157), so test sessions don't spawn
  a watchdog thread. Don't wrap `uvicorn` in
  `watchfiles` / `--reload` for djust dev servers — that's process
  restart and drops view state; djust's HVR is strictly better
  (preserves form input, scroll position, counters).

## Process canon (read on demand)

The rules adopted by past retros live in [`docs/process-canon/`](docs/process-canon/README.md),
one page per arc, plus bug-report triage and chain-shaped issues. They are not loaded by
default. Planning and Code Review read the index and grep it for the issue numbers, files
and failure classes in play; other stages read only pages a plan names. A comment citing
`CLAUDE.md #NNNN` resolves with `grep -rn "#NNNN" docs/process-canon/`.

Keep this file a rule sheet: new retro rules go in `docs/process-canon/`, not here.
`tests/test_claude_md_size.py` caps this file at 24 KB.

## Pipeline

- default_branch: main. Since 2026-11-11 (#4013) `main` IS the 2.0 line (`2.0.0.dev0`); there is
  no separate 2.0 trunk. The released 1.3 line lives on the `1.3` branch, which takes security and
  serious fixes only.
- test_command: `npx vitest run` (JS) · `pytest` (Python; see `pyproject.toml` testpaths)
- regenerate: `bash scripts/build-client.sh` — run this after any merge that touched
  `static/djust/src/`; never hand-merge `client.js` / `client.min.js` / `client-sizes.json`
- merge: squash (main carries no PR merge commits); drain PRs merge with `--admin`,
  since the author cannot approve their own PR and human review is at the milestone level
  **A branch-to-branch merge is the one exception, and it must NOT be queued (#4010, #4013).**
  `main`'s merge queue only squashes, and "merge when ready" on `main` goes through that queue. A
  squash of one long-lived branch into another drops the source as a parent, so the next sync
  re-hits every conflict and the source branch can never be retired. Land such a merge with
  `gh pr merge <n> --merge --admin --match-head-commit <full sha>` once its own checks are green.
  **A batch merges one PR at a time (#3735):** for the second and later PRs of a batch, wait for
  main's `Tests` to finish green on the previous merge commit before squashing the next (or `git merge
  origin/main` into the branch and wait for CI on that merged tree). Cross-file gates — a ratchet over
  every test file, shard-duration coverage, a count or floor, a settings-leak scan — pass alone and fail
  together; four back-to-back merges put main red twice in v1.3.0-16 (#3727, #3729). Run `make merge-gates`
  on the scratch merge: it runs the whole-tree meta tests from one list (`MERGE_GATES` in the `Makefile`),
  and `tests/test_merge_gates_3757.py` fails when a new tree-scanning test is neither on it nor excluded
- release: squash-merge the release PR first, then tag `main` with `make release`; never tag
  `release/*` (the squash leaves the tag unreachable from main — #3149, v1.3.0rc3 / #3131)

**Running the suite from a git worktree** (three parallel worktrees is the tested shape):
the venv's editable install points at the *main* checkout, so a bare `import djust` inside a
worktree resolves to the main source and the tests exercise the wrong code — silently. Pin it
and prove it:

```bash
PYTHONPATH="$WT/python" "$MAIN/.venv/bin/python" -c "import djust; print(djust.__file__)"
```

Also copy the gitignored Rust extension into the worktree and symlink `node_modules`; do NOT
symlink `target/` (concurrent cargo builds collide).

**Installing a built extension into a checkout** (macOS 27, #2899 follow-up): never `cp` a
`_rust*.so` over the one already there — `cp` rewrites the same inode, and when a dev server or
daemon has it mapped the next `import djust._rust` is SIGKILLed (exit 137, no dyld message).
Build a wheel from the worktree (`maturin build --release --out <dir> -i <venv>/bin/python`; not
`maturin develop`, which needs a venv and repoints the editable install) and install it with
`make install-ext SO=<wheel-or-so>` / `scripts/install-rust-ext.sh`, which re-signs the copy
(`codesign -s - -f`) and moves it into place as a new inode. `make build` in place is always safe.

**Failure classes**: `docs/patterns/` is this repo's pattern wiki — one page per class, each
with an instance table whose `rule in force?` column `/pipeline-retro` Stage 3.7 counts to
KEEP / HARDEN / DEMOTE the matching rule in `docs/process-canon/`. `scripts/check-retro-coverage.py` enforces
that a completed drain bucket has a `RETRO.md` entry (#2848).

## Additional Documentation

- `docs/SECURE_DEFAULTS.md` — secure-by-default pattern catalog (denylist serialization, HMAC signed snapshots, fail-closed precedence gate, `safe_setattr`) + how to make a new feature secure-by-default
- `docs/SECURITY_GUIDELINES.md` — `djust.security` utilities, banned patterns, contributor security checklist
- `docs/PULL_REQUEST_CHECKLIST.md` — PR review checklist
- `CONTRIBUTING.md` — contribution guidelines
- `QUICKSTART.md` — quick setup guide
- `docs/STATE_MANAGEMENT_API.md` — decorator API reference
- `docs/website/guides/loading-states.md` — loading states & background work guide
- `DEVELOPMENT_PROCESS.md` — 9-step development process
