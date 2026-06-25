# Contributing to djust

Thank you for your interest in contributing! We welcome contributions from everyone.

## Getting Started

1. Fork the repository
2. Clone your fork: `git clone https://github.com/YOUR_USERNAME/djust.git`
3. Create a branch: `git checkout -b feature/your-feature-name`
4. Make your changes
5. Run tests: `cargo test && pytest`
6. Commit: `git commit -m "Add your feature"`
7. Push: `git push origin feature/your-feature-name`
8. Open a Pull Request

## Development Setup

### Prerequisites

- Python 3.10+
- Rust 1.70+
- Django 3.2+

### Environment Setup

```bash
# Install Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Install uv (fast Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies and build Rust extension
uv sync --extra dev

# Install pre-commit and pre-push hooks (required for contributions)
uvx pre-commit install
uvx pre-commit install --hook-type pre-push
```

### Optional: commit wrapper that auto-restages reformats

Pre-commit hooks like ruff-format reformat staged files but do not
auto-restage the rewrite — so `git commit` exits non-zero and you have
to re-`git add` + re-commit by hand. `scripts/git-commit-with-precommit.sh`
(or `make commit MSG="..."`) wraps that loop:

```bash
# instead of: git add foo.py && git commit -m "feat: bar"
make commit MSG="feat: bar"

# or directly, forwarding any git commit args:
scripts/git-commit-with-precommit.sh -m "feat: bar" --signoff
```

The wrapper runs pre-commit against your staged files first; if hooks
rewrote anything it re-stages and commits. The bare `git commit` path
still works — the wrapper is opt-in. Closes #1464.

### Working in a `git worktree`

A linked `git worktree` has no `.venv` of its own and the editable
`maturin develop` install binds Python imports to the **main** checkout's
`python/` (via a plain `djust.pth`). Two helpers make worktree work behave:

- **Interpreter resolution (#1796).** The pre-push hook and `make` targets
  resolve the interpreter from the main checkout via
  `scripts/run-with-venv-python.sh`, so they no longer fail with exit-127 in
  a worktree — no `--no-verify` needed for that reason alone.
- **Python-source gating (#1810).** The pre-push pytest hook prepends the
  worktree's `python/` to `PYTHONPATH` (it wins over `djust.pth`) and
  symlinks the matching compiled `_rust.*.so` from the main checkout, so a
  worktree `git push` runs the suite against the **worktree's** Python
  changes — not the main tree's. (`PYTHONPATH` is inserted before `.pth`
  processing, so the worktree source wins; the symlinked `.so` is gitignored
  and never shows in `git status`.)

> **Caveat — Rust changes.** This shadows only **Python** source. If your
> worktree changes Rust (`crates/`, anything compiled into `djust._rust`),
> the symlinked `.so` is still the main checkout's build. Run
> `maturin develop` against the worktree before relying on the pre-push gate
> for Rust changes, or build + verify in the worktree manually. CI is the
> authoritative gate either way.

To reproduce the gated PYTHONPATH manually from a worktree:

```bash
WT="$(bash scripts/run-with-venv-python.sh --worktree-pythonpath)"
PYTHONPATH="${WT:+$WT:}." bash scripts/run-with-venv-python.sh -m pytest tests/ python/tests/ -q
```

`--worktree-pythonpath` prints the path to prepend (empty in the main
checkout, so the same command is a no-op there).

#### Shared-config corruption: `core.bare = true` (#1938)

A linked worktree shares **one** `.git/config` with the main checkout (the
worktree's `.git` is a *file* pointing at `<main>/.git/worktrees/<name>`, and
`core.*` is read from the shared `[core]` section). If anything flips
`core.bare` to `true` in that shared config — a build/PyO3-repoint step that
runs `git config core.bare true` to repoint the compiled extension (the #1804
pattern), an IDE/GitKraken integration, or a stray manual command — then
`git status` / `git push` break in **both** the worktree **and** the main
checkout: every tracked file shows as deleted, because git now thinks the work
tree has no working directory.

No djust pre-push hook, test, or script writes `core.bare` (every in-repo git
operation is read-only or scoped to an isolated tmp dir — verified in #1938),
so this is an **external** corruption, not a framework bug. Two mitigations:

- **Push `--no-verify` from worktrees.** A `--no-verify` push does not run the
  pre-push hook chain and does not leak `core.bare`. Run the gates manually
  first (the gated-PYTHONPATH command above) and rely on CI as the
  authoritative gate. This is the established worktree-subagent pattern.
- **Detect + recover with the helper.** Run the check before and after a
  worktree `git push`; `--fix` performs the documented recovery
  (`core.bare false`). It reads the **shared** config (works from any
  worktree) and never writes `core.bare true`:

  ```bash
  bash scripts/check-shared-git-config.sh         # exit 1 if leaked
  bash scripts/check-shared-git-config.sh --fix   # auto-recover a leak
  ```

  Manual recovery is equivalent: `git config core.bare false`.

## Code Style

### Python
- Follow PEP 8
- Use type hints where possible
- Use `ruff format` for formatting and `ruff check` for linting

```bash
ruff format python/
ruff check python/
mypy python/
bandit -r python/djust/ -ll
```

### JavaScript
- Run `eslint` for security linting (runs automatically via pre-commit hook)

```bash
npm run lint
```

### Rust
- Follow standard Rust conventions
- Run `cargo fmt` before committing
- Use `cargo clippy` for linting

```bash
cargo fmt
cargo clippy -- -D warnings
```

## Security

**IMPORTANT**: All contributors must follow the security guidelines in [`docs/SECURITY_GUIDELINES.md`](docs/SECURITY_GUIDELINES.md).

Key requirements:
- Use `safe_setattr()` instead of raw `setattr()` with untrusted keys
- Use `sanitize_for_log()` before logging user input
- Use `create_safe_error_response()` for error responses
- Never include stack traces or params in production error responses
- **Template tags**: Always use `format_html()` or `escape()` — never `mark_safe(f'...')` with user-controlled values
- **Multi-tenant**: New features touching data storage or queries must respect tenant isolation (key prefixing, queryset scoping)
- **CSRF**: New endpoints must maintain Django CSRF protection — do not add `@csrf_exempt` without documented justification and equivalent protection

```python
from djust.security import safe_setattr, sanitize_for_log, create_safe_error_response
```

See [`docs/SECURITY_GUIDELINES.md`](docs/SECURITY_GUIDELINES.md) for complete details on template tag security, multi-tenant isolation, and PWA/offline sync hardening.

## Testing

### Rust Tests
```bash
cargo test
cargo test --release  # Run with optimizations
```

### Python Tests
```bash
pytest
pytest --cov=djust  # With coverage
```

### Integration Tests
```bash
cd examples/demo_project
python manage.py test
```

### CI Mirror — catch coverage / xdist surprises pre-push

`make test` runs Python, Rust, and JS in parallel for speed but uses a
different invocation than CI. Before pushing a branch, run:

```bash
make ci-mirror
```

This executes the EXACT pytest commands from `.github/workflows/test.yml`:

- Full Python suite with `pytest-xdist` (`-n auto`, as CI runs it)
- Security-tests with `--cov-fail-under=75` coverage threshold

Catches the two classes of bugs `make test` can miss:
- Coverage-threshold regressions (e.g. PR #959 shipped with 64.72% security
  coverage; only caught by CI)
- xdist-ordering issues that pass under sequential runs

### Benchmarks
```bash
cd benchmarks
python benchmark.py
```

## Pull Request Guidelines

- Keep PRs focused on a single feature or fix
- Write clear commit messages
- Add tests for new features
- Update documentation as needed
- Ensure all tests pass
- Add yourself to CONTRIBUTORS.md

## Documentation

- Code comments for complex logic
- Docstrings for public APIs
- Update README.md for new features
- Add examples when appropriate

## Performance

- Profile before optimizing
- Run benchmarks to verify improvements
- Consider memory usage
- Document performance characteristics

## Dual Implementation Maintenance

djust uses a hybrid Python/Rust architecture where some functionality exists in both languages:
- **Python**: Public API, business logic, Django integration
- **Rust**: Performance-critical operations (template rendering, VDOM diffing)

### When to Implement in Both Languages

You need to maintain dual implementations when:

1. **UI Components with Rust Optimization**: Components that support both Python rendering (for flexibility) and Rust rendering (for performance)
   - Example: Form field components can render in Python or be optimized with Rust
   - Python provides the developer-facing API
   - Rust provides optional performance optimization

2. **Core Abstractions**: Backend interfaces that support multiple implementations
   - Example: `StateBackend` (InMemory vs Redis)
   - Python defines the abstract interface
   - Each implementation must follow the contract

3. **Serialization**: Data structures that cross the Python/Rust boundary
   - Example: LiveView state serialization
   - Both sides must agree on format (MessagePack)

### Guidelines for Maintaining Consistency

When working on dual implementations:

**1. Define Contracts Clearly**
```python
# Python: Define abstract interface
class StateBackend(ABC):
    @abstractmethod
    def health_check(self) -> Dict[str, Any]:
        """
        Returns:
            - status: 'healthy' or 'unhealthy'
            - latency_ms: Response time
            - error: Error message if unhealthy
        """
        pass
```

**2. Test Both Implementations**
- Write tests for each implementation path
- Verify consistent behavior and response format
- Use integration tests to ensure they work together

```python
# Test each backend implementation
def test_inmemory_health_check():
    backend = InMemoryStateBackend()
    result = backend.health_check()
    assert result["status"] == "healthy"

def test_redis_health_check():
    backend = RedisStateBackend(...)
    result = backend.health_check()
    assert result["status"] == "healthy"
```

**3. Document Differences**
- Note any behavioral differences in docstrings
- Document performance characteristics
- Explain when to use each implementation

```python
class InMemoryStateBackend(StateBackend):
    """
    In-memory state backend for development and testing.

    Fast and simple, but:
    - Does not scale horizontally
    - Data lost on server restart
    """
```

**4. Keep APIs Synchronized**
- When adding methods to abstract base classes, implement in all subclasses
- Maintain consistent return types and error handling
- Use type hints to enforce contracts

**5. Version Compatibility**
- When changing serialization formats, maintain backward compatibility
- Document breaking changes clearly
- Provide migration paths

### Common Patterns

**Pattern 1: Abstract Base Class with Multiple Implementations**
```python
# Python defines interface
class StateBackend(ABC):
    @abstractmethod
    def get(self, key: str) -> Optional[Tuple[RustLiveView, float]]:
        pass

# Each implementation follows contract
class InMemoryStateBackend(StateBackend):
    def get(self, key: str) -> Optional[Tuple[RustLiveView, float]]:
        return self._cache.get(key)

class RedisStateBackend(StateBackend):
    def get(self, key: str) -> Optional[Tuple[RustLiveView, float]]:
        data = self._client.get(self._make_key(key))
        if not data:
            return None
        view = RustLiveView.deserialize_msgpack(data)
        return (view, view.get_timestamp())
```

**Pattern 2: Python API with Rust Acceleration**
```python
# Python provides public API
class LiveView:
    def render(self):
        # Use Rust for heavy lifting
        return self._rust_view.render()

# Rust handles performance-critical work
#[pyclass]
struct RustLiveView {
    template: String,
    vdom: VirtualDom,
}
```

**Pattern 3: JavaScript Dual Implementation (Embedded + ES Module)**

For client-side JavaScript that needs both runtime execution AND testing:

```javascript
// File 1: decorators.js (ES Module for testing)
/**
 * IMPORTANT: This module is used for testing and documentation.
 * The actual implementation runs as embedded JavaScript in live_view.py.
 * When making changes, update BOTH files to keep them in sync.
 */
export const debounceTimers = new Map();

export function debounce(eventName, eventData, config, sendEvent) {
    // Implementation here
}

// File 2: live_view.py (embedded for runtime)
def _get_decorator_js(self) -> str:
    return '''
    // Same implementation as decorators.js but without exports
    const debounceTimers = new Map();

    function debounce(eventName, eventData, config, sendEvent) {
        // Identical implementation
    }
    '''
```

**Why this pattern?**
- **Runtime**: JavaScript must be embedded in HTML (no module imports in inline scripts)
- **Testing**: ES modules required for Jest/Vitest to import and test functions
- **Challenge**: Must maintain two identical copies manually

**Synchronization checklist:**
- [ ] Update logic in `decorators.js` (ES module)
- [ ] Copy identical changes to `live_view.py` embedded string
- [ ] Remove `export` keywords when copying to embedded version
- [ ] Run JavaScript tests: `npm test`
- [ ] Run integration tests to verify runtime behavior
- [ ] Update JSDoc comments in both locations

**Files using this pattern:**
- `python/djust/static/djust/decorators.js` - ES module for testing
- `python/djust/live_view.py` - Embedded version in `_get_decorator_js()`

**Common mistakes:**
- Forgetting to update embedded version after changing ES module
- Leaving `export` keywords in embedded code
- Version drift between the two implementations

### Testing Strategy

For dual implementations:

1. **Unit Tests**: Test each implementation independently
2. **Interface Tests**: Verify all implementations satisfy the contract
3. **Integration Tests**: Test Python and Rust working together
4. **Benchmark Tests**: Compare performance when relevant

Example:
```python
class TestStateBackendInterface:
    """Test all backends follow the same contract."""

    @pytest.fixture(params=['memory', 'redis'])
    def backend(self, request):
        if request.param == 'memory':
            return InMemoryStateBackend()
        elif request.param == 'redis':
            return RedisStateBackend(...)

    def test_health_check_returns_correct_fields(self, backend):
        """All backends must return same fields."""
        result = backend.health_check()
        assert "status" in result
        assert "backend" in result
        assert "latency_ms" in result
```

### Checklist for Dual Implementation Changes

When modifying code with dual implementations:

- [ ] Update Python interface/abstract base class
- [ ] Update all implementations (InMemory, Redis, etc.)
- [ ] Update type hints and docstrings
- [ ] Add/update tests for each implementation
- [ ] Verify interface tests pass for all implementations
- [ ] Update relevant documentation
- [ ] Check for breaking changes
- [ ] Run benchmarks if performance-critical

## Areas for Contribution

### High Priority
- Additional Django template tags (custom tags beyond built-ins)
- More comprehensive test coverage
- Performance optimizations
- Documentation improvements

### Medium Priority
- Additional example applications
- Browser compatibility testing
- Error message improvements
- Accessibility features

### Future Features
- Template inheritance support
- Redis session backend

## Supporting the Project

Beyond code contributions, there are many ways to support djust:

### Financial Support
- 💜 [GitHub Sponsors](https://github.com/sponsors/johnrtipton) - Monthly support from $5/month

### Non-Financial Support
- ⭐ Star the repository on GitHub
- 📢 Share djust on social media and with your network
- 📝 Write blog posts or tutorials about djust
- 🎤 Give talks about djust at conferences or meetups
- 💬 Help answer questions in Discord and GitHub Discussions
- 📚 Improve documentation
- 🐛 Report bugs and suggest features

Every contribution, big or small, helps make djust better for everyone!

## Questions?

- Open a discussion on GitHub
- Join our Discord: https://discord.gg/djust
- Email: dev@djust.org

## Code of Conduct

Be respectful, inclusive, and professional. We're all here to build great software together.

Thank you for contributing! 🚀
