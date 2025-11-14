# JavaScript Testing Guide

This document describes how to run and maintain JavaScript tests for djust's state management decorators.

## Overview

djust uses [Vitest](https://vitest.dev/) for testing JavaScript code. The test suite covers three decorator types:

- **@debounce** (Phase 2) - Delays event execution until user stops triggering events
- **@throttle** (Phase 2) - Limits event execution frequency
- **@optimistic** (Phase 3) - Applies instant UI updates before server validation

## Test Structure

### Files

```
djust/
├── python/djust/static/djust/
│   └── decorators.js          # Testable decorator module
├── tests/js/
│   ├── debounce.test.js       # Debounce decorator tests
│   ├── throttle.test.js       # Throttle decorator tests
│   └── optimistic.test.js     # Optimistic update tests
├── vitest.config.js           # Vitest configuration
└── package.json               # Test scripts
```

### Architecture

The decorators are implemented in two places:

1. **`python/djust/static/djust/decorators.js`** - Standalone ES module used for testing
2. **`python/djust/live_view.py`** - Embedded JavaScript (actual runtime implementation)

**⚠️ Important**: These two implementations must be kept in sync. When modifying decorator logic, update BOTH files.

## Running Tests

### Run all tests

```bash
npm test
```

### Run tests in watch mode (auto-rerun on changes)

```bash
npm run test:watch
```

### Run tests with coverage

```bash
npm run test:coverage
```

### Run tests with UI

```bash
npm run test:ui
```

## Coverage Requirements

The project maintains **85%+ coverage** across all metrics:

- **Statements**: ≥ 85%
- **Branches**: ≥ 85%
- **Functions**: ≥ 85%
- **Lines**: ≥ 85%

Coverage reports are generated in:
- **Console**: Text summary
- **HTML**: `coverage/index.html` (open in browser for detailed view)
- **JSON**: `coverage/coverage-final.json`

## Test Organization

### Debounce Tests (`debounce.test.js`)

Tests the `@debounce` decorator which delays event execution:

- **Basic Debouncing**: Event delays and timer resets
- **Max Wait**: Force execution after maximum wait time
- **State Management**: Timer state tracking and cleanup
- **Real-World Scenarios**: Search input, window resize events

**Key Test Cases**:
```javascript
// Basic delay
debounceEvent('search', { query: 'test' }, { wait: 0.5 }, sendFn);
vi.advanceTimersByTime(500);
expect(sendFn).toHaveBeenCalledOnce();

// Max wait forces execution
debounceEvent('search', { query: 'a' }, { wait: 0.5, max_wait: 2.0 }, sendFn);
// ... continuous events for 2+ seconds
expect(sendFn).toHaveBeenCalled(); // Forced execution
```

### Throttle Tests (`throttle.test.js`)

Tests the `@throttle` decorator which limits execution frequency:

- **Basic Throttling**: Interval enforcement
- **Leading Edge**: Execute immediately on first call
- **Trailing Edge**: Execute after events stop
- **Leading + Trailing**: Both edges enabled
- **Real-World Scenarios**: Scroll events, mouse tracking

**Key Test Cases**:
```javascript
// Leading edge (first event executes immediately)
throttleEvent('scroll', { scrollY: 0 }, { interval: 0.5, leading: true }, sendFn);
expect(sendFn).toHaveBeenCalledOnce();

// Trailing edge (executes after events stop)
throttleEvent('scroll', { scrollY: 100 }, { interval: 0.5, trailing: true }, sendFn);
vi.advanceTimersByTime(500);
expect(sendFn).toHaveBeenCalledOnce();
```

### Optimistic Update Tests (`optimistic.test.js`)

Tests the `@optimistic` decorator which applies instant UI updates:

- **Checkbox/Radio**: Instant toggle
- **Input/Textarea**: Instant value updates
- **Select**: Instant option selection
- **Button**: Disable + loading text
- **State Management**: Save, clear, revert operations
- **Error Handling**: Revert with animation

**Key Test Cases**:
```javascript
// Checkbox toggle
const checkbox = document.createElement('input');
checkbox.type = 'checkbox';
applyOptimisticUpdate('toggle', { checked: true }, checkbox);
expect(checkbox.checked).toBe(true);

// Button loading state
const button = document.createElement('button');
button.setAttribute('data-loading-text', 'Saving...');
applyOptimisticUpdate('submit', {}, button);
expect(button.disabled).toBe(true);
expect(button.textContent).toBe('Saving...');
```

## Writing New Tests

### Test Structure

Follow this pattern for new test files:

```javascript
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { yourFunction, clearAllState } from '../../python/djust/static/djust/decorators.js';

describe('Your Feature', () => {
    let sendFn;

    beforeEach(() => {
        clearAllState();
        sendFn = vi.fn();
        vi.useFakeTimers();
    });

    afterEach(() => {
        vi.restoreAllMocks();
        vi.useRealTimers();
    });

    it('should do something', () => {
        // Your test
    });
});
```

### Best Practices

1. **Always use fake timers** for decorator tests:
   ```javascript
   vi.useFakeTimers();
   vi.advanceTimersByTime(500); // Advance time
   vi.useRealTimers(); // Cleanup
   ```

2. **Clear state before each test**:
   ```javascript
   beforeEach(() => {
       clearAllState(); // Clears debounce/throttle/optimistic state
   });
   ```

3. **Mock the send function**:
   ```javascript
   const sendFn = vi.fn();
   expect(sendFn).toHaveBeenCalledOnce();
   ```

4. **Create DOM elements for optimistic tests**:
   ```javascript
   const input = document.createElement('input');
   document.body.appendChild(input);
   ```

5. **Test edge cases**:
   - Empty data
   - Null/undefined values
   - Very short/long intervals
   - Concurrent events

## Debugging Tests

### Enable verbose logging

Set debug flag before running tests:

```javascript
globalThis.djustDebug = true;
```

### View test output

```bash
npm run test:watch # Shows real-time output
```

### Inspect coverage gaps

```bash
npm run test:coverage
open coverage/index.html
```

Look for:
- **Uncovered lines** (red highlighting)
- **Partial branch coverage** (yellow highlighting)
- **Function coverage** (function names in red)

## Common Issues

### Timers not advancing

**Problem**: Tests hang or don't execute callbacks.

**Solution**: Ensure you're using fake timers:
```javascript
vi.useFakeTimers();
vi.advanceTimersByTime(500);
```

### State pollution between tests

**Problem**: Tests pass individually but fail when run together.

**Solution**: Clear state in `beforeEach()`:
```javascript
beforeEach(() => {
    clearAllState();
});
```

### DOM elements not found

**Problem**: `element.classList` or similar throws errors.

**Solution**: Create elements explicitly:
```javascript
const element = document.createElement('button');
document.body.appendChild(element);
```

### Coverage not updating

**Problem**: Changes to code don't affect coverage report.

**Solution**: Clear cache and rebuild:
```bash
rm -rf coverage/
npm run test:coverage
```

## Continuous Integration

Tests run automatically on:
- Every push to main
- Every pull request
- Pre-commit hooks (if configured)

**CI Requirements**:
- All tests must pass
- Coverage must be ≥ 85% for all metrics
- No test failures or errors

## Updating After Code Changes

When modifying decorator behavior:

1. **Update the implementation**:
   - Edit `python/djust/live_view.py` (embedded JS)
   - Copy changes to `python/djust/static/djust/decorators.js`

2. **Update tests**:
   - Modify expectations to match new behavior
   - Add tests for new functionality
   - Remove obsolete tests

3. **Verify coverage**:
   ```bash
   npm run test:coverage
   ```

4. **Check both implementations match**:
   - Compare embedded JS with decorators.js
   - Ensure logic is identical

## Additional Resources

- [Vitest Documentation](https://vitest.dev/)
- [Testing Library Best Practices](https://testing-library.com/docs/)
- [Phase 2 Implementation Docs](../docs/IMPLEMENTATION_PHASE2.md)
- [Phase 3 Implementation Docs](../docs/IMPLEMENTATION_PHASE3.md)

## FAQ

### Q: Why are there two decorator implementations?

A: djust uses **embedded JavaScript** in `live_view.py` for runtime, but this can't be directly imported by tests. The `decorators.js` module is a standalone, testable version that must be kept in sync.

### Q: What's the difference between debounce and throttle?

A:
- **Debounce**: Waits until events stop, then executes once (e.g., search after typing stops)
- **Throttle**: Limits execution frequency (e.g., max 10 scroll events per second)

### Q: How do I test WebSocket cleanup?

A: Use `clearAllState()` to simulate WebSocket disconnect:
```javascript
applyOptimisticUpdate('event', {}, element);
clearAllState(); // Simulates disconnect
expect(optimisticUpdates.size).toBe(0);
```

### Q: Can I test the embedded JavaScript directly?

A: No. The embedded JS in `live_view.py` is a Python string and can't be imported by tests. Always test via `decorators.js`.

### Q: How do I increase coverage?

A:
1. Look at coverage report: `open coverage/index.html`
2. Find uncovered lines (red)
3. Write tests that exercise those code paths
4. Run `npm run test:coverage` to verify
