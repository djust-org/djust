# CI Optimization Guide

This document explains the CI test parallelization strategy and performance improvements.

## Overview

The test suite has been optimized to run tests in parallel, significantly reducing CI execution time.

## Parallelization Strategy

### Before: Sequential Execution
```
┌─────────────────────────────────────┐
│ Setup (1 min)                       │
├─────────────────────────────────────┤
│ Rust tests (2 min)                  │
├─────────────────────────────────────┤
│ Python tests (3 min)                │
├─────────────────────────────────────┤
│ JavaScript tests (1 min)            │
├─────────────────────────────────────┤
│ Linting (1 min)                     │
└─────────────────────────────────────┘
Total: ~8 minutes
```

### After: Parallel Execution
```
┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ Rust tests   │  │ Python tests │  │ JS tests     │  │ Playwright   │
│ + linting    │  │ + linting    │  │ + linting    │  │ (browser)    │
│ (2.5 min)    │  │ (2 min)      │  │ (1 min)      │  │ (2 min)      │
└──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘
         ↓               ↓               ↓               ↓
         └───────────────┴───────────────┴───────────────┘
                              ↓
                     ┌──────────────────┐
                     │ Summary (5 sec)  │
                     └──────────────────┘
Total: ~2.5 minutes (70% faster!)
```

## Optimizations Implemented

### 1. Job-Level Parallelization

**Four parallel test jobs:**
- `rust-tests` - Rust tests + clippy + fmt
- `python-tests` - Python tests + ruff
- `javascript-tests` - JS tests + linter (if configured)
- `playwright-tests` - Browser automation tests (@loading, @cache, DraftMode)

**Benefits:**
- Tests run simultaneously on separate runners
- Failures in one job don't block others
- Clearer separation of concerns

### 2. Python Test Parallelization (pytest-xdist)

**Within Python tests:**
```bash
pytest tests/ python/tests/ -n auto
```

The `-n auto` flag:
- Detects available CPU cores
- Distributes tests across workers
- Runs ~4x faster on 4-core runner

**Test Distribution:**
```
Worker 1: tests/unit/test_forms.py
Worker 2: tests/unit/test_live_view.py
Worker 3: tests/e2e/test_phase5_decorators.py
Worker 4: python/tests/test_actor_integration.py
... (parallel execution)
```

### 3. Dependency Caching

**Cached artifacts:**
- Rust dependencies (`~/.cargo/`)
- Python dependencies (`.venv/`)
- Node modules (`node_modules/`)

**Cache keys based on:**
- `Cargo.lock` hash
- `pyproject.toml` hash
- `package-lock.json` hash

**Benefits:**
- Faster setup (30 sec → 10 sec)
- Reduced network usage
- More reliable builds

### 4. Optimized Build Steps

**Rust optimizations:**
- `--release` flag for faster test execution
- Skip `djust_live` (tested via Python)
- Parallel compilation (default)

**Python optimizations:**
- `uv sync` with minimal dependencies
- Separate dev/prod dependency sets
- Maturin release builds

## Performance Metrics

### Expected CI Times

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| **Total time** | ~8 min | ~2.5 min | **70% faster** |
| **Python tests** | 3 min | 45 sec | **75% faster** |
| **Setup time** | 1 min | 10 sec | **83% faster** |
| **Feedback time** | 8 min | 2.5 min | **69% faster** |

### Test Count

- **172 Python tests** - Distributed across 4+ workers (pytest-xdist)
- **218 JavaScript tests** - Vitest parallel by default
- **3 Playwright tests** - Browser automation (Phase 5 features)
- **Rust tests** - Cargo parallel by default

## Local Development

### Run Tests Locally (Parallel)

```bash
# Python tests in parallel
make test-python-parallel

# All tests (still sequential between suites)
make test

# Individual test suites
make test-rust
make test-js
make test-python
```

### Install pytest-xdist

```bash
uv pip install pytest-xdist
# or
pip install pytest-xdist
```

### Run Specific Test in Parallel

```bash
# Run unit tests in parallel
pytest tests/unit/ -n auto

# Run with specific worker count
pytest tests/ -n 8

# Disable parallelization (for debugging)
pytest tests/ -n 0
```

## CI Configuration Files

### GitHub Actions

Two workflow files:

1. **`.github/workflows/test.yml`** (Current)
   - Sequential execution
   - Simple, reliable
   - Use for stable releases

2. **`.github/workflows/test-parallel.yml`** (New)
   - Parallel execution
   - Faster feedback
   - Use for active development

### Switching to Parallel CI

**Option 1: Rename files**
```bash
mv .github/workflows/test.yml .github/workflows/test-sequential.yml
mv .github/workflows/test-parallel.yml .github/workflows/test.yml
```

**Option 2: Keep both (recommended for testing)**
- Both workflows run on PR
- Compare performance
- Verify reliability

## Troubleshooting

### Tests Fail Only in Parallel

**Symptom:** Tests pass with `pytest` but fail with `pytest -n auto`

**Common causes:**
1. **Shared state** - Tests modifying global state
2. **Database conflicts** - Multiple workers writing to same DB
3. **File system** - Tests creating/deleting same files

**Solution:**
```python
# Use pytest-django's transactional tests
@pytest.mark.django_db(transaction=True)
def test_concurrent_safe():
    # Each worker gets isolated transaction
    pass
```

### Slow Test Detection

**Find slow tests:**
```bash
pytest --durations=10 tests/
```

**Optimize slow tests:**
- Use fixtures for common setup
- Mock external services
- Skip integration tests in unit suites

### Cache Issues

**Clear cache if builds fail:**
```bash
# Locally
rm -rf ~/.cache/uv .venv node_modules target/

# GitHub Actions
# Go to repo → Actions → Caches → Delete specific cache
```

## Future Improvements

### Potential Optimizations

1. **Test Sharding** - Split tests across multiple CI jobs
   ```yaml
   strategy:
     matrix:
       shard: [1, 2, 3, 4]
   ```

2. **Matrix Testing** - Test multiple Python/Node versions
   ```yaml
   strategy:
     matrix:
       python: ['3.10', '3.11', '3.12']
   ```

3. **Test Result Caching** - Skip unchanged tests
   - Use `pytest --lf` (last failed)
   - Use `pytest --cache-show`

## Best Practices

### Writing Parallel-Safe Tests

**DO:**
- ✅ Use fixtures for test data
- ✅ Use unique identifiers
- ✅ Clean up resources in teardown
- ✅ Use transactional tests

**DON'T:**
- ❌ Modify global state
- ❌ Hard-code file paths
- ❌ Assume test execution order
- ❌ Share mutable objects

### Example: Parallel-Safe Test

```python
import pytest
from django.test import RequestFactory

@pytest.mark.django_db(transaction=True)
def test_parallel_safe():
    # Each worker gets unique factory
    factory = RequestFactory()

    # Each worker gets unique user
    user = User.objects.create(
        username=f"user_{uuid.uuid4()}"
    )

    # Test logic...

    # Cleanup (optional with transaction=True)
    user.delete()
```

## Monitoring

### CI Performance Dashboard

Track these metrics:
- Total CI time (target: <3 min)
- Test pass rate (target: >99%)
- Cache hit rate (target: >80%)
- Flaky test count (target: 0)

### Alerts

Set up alerts for:
- CI time > 5 minutes (regression)
- Test failures > 5% (instability)
- Cache misses > 50% (configuration issue)

## Resources

- [pytest-xdist documentation](https://pytest-xdist.readthedocs.io/)
- [GitHub Actions optimization](https://docs.github.com/en/actions/using-jobs/using-a-matrix-for-your-jobs)
- [Cargo parallel compilation](https://doc.rust-lang.org/cargo/reference/build-scripts.html)
- [Vitest performance](https://vitest.dev/guide/improving-performance.html)
