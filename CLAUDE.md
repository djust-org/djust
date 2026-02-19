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
│   └── static/djust/       # Client JS (~5KB)
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
- Client JS must stay minimal (~5KB). No new dependencies without discussion.
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

## Common Pitfalls

- **Ruff F509**: `%`-format strings containing CSS semicolons trigger false positives. Separate HTML (`%s` substitution) from CSS (static string) and concatenate.
- **VDOM form values**: Ensure form field values are preserved during updates. See `VDOM_PATCHING_ISSUE.md`.
- **Pre-commit reformatting**: If commit fails due to ruff auto-format, re-stage and commit again.

## Additional Documentation

- `docs/PULL_REQUEST_CHECKLIST.md` — PR review checklist
- `CONTRIBUTING.md` — contribution guidelines
- `QUICKSTART.md` — quick setup guide
- `docs/STATE_MANAGEMENT_API.md` — decorator API reference
- `DEVELOPMENT_PROCESS.md` — 9-step development process
